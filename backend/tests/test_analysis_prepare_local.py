from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import ResolvedModelRelease

from executor_fixtures import FakeProvider, FakeRegistry

MANIFEST_SHA = "b" * 64


class PrepareLocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="prepare_local", name="Prepare Local", version="1.0",
            label_space="spacenet_14", recommended_device="CPU", cpu_supported=True,
            stages=(), inspectable_stages=(), task_capability="classification",
            executors_supported=("local_cpu",),
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
            parameter_schema={"type": "object", "additionalProperties": False,
                              "properties": {"threshold": {"type": "number"}}},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class ReleaseBoundLocalPipeline(PrepareLocalPipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return replace(super().definition, model_release_required=True)


class FakeReleaseStore:
    def __init__(self) -> None:
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            model_release_id=requested or "golden", asset_manifest_sha256=MANIFEST_SHA
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST_SHA)
        return ResolvedModelRelease(release=release, manifest=manifest)


def _add_recording(client, recording_id="rec_x"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.commit()


def _service_local(client, *, pipeline=None, store=None, provider=None):
    session = client.app.state.database.session_factory()
    fake_provider = provider or FakeProvider("local_cpu")
    registry = FakeRegistry({"local_cpu": fake_provider})
    service = AnalysisService(
        session,
        PipelineRegistry([pipeline or PrepareLocalPipeline()]),
        client.app.state.job_manager,
        model_release_store=store,
        executor_registry=registry,
    )
    return service, fake_provider, registry


def _prepare_local(service, **overrides):
    kwargs = dict(recording_id="rec_x", pipeline_id="prepare_local",
                  executor="local_cpu", parameters={})
    kwargs.update(overrides)
    return service.prepare_run(**kwargs)


def test_prepare_run_stages_pending_local_run_without_commit_or_launch(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    assert run.status == "pending"
    assert run.worker_pid is None
    assert run.executor == "local_cpu"
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_freezes_parameters_and_runtime_descriptor(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service, parameters={"threshold": 0.5})
    assert run.parameters_json == {"threshold": 0.5}
    assert run.execution_metadata_json == {
        "runtime_descriptor": provider.runtime_descriptor().to_metadata()
    }


def test_prepare_run_release_less_fields_remain_null(client):
    _add_recording(client)
    service, _, _ = _service_local(client)
    run = _prepare_local(service)
    assert "model_release_id" not in run.execution_metadata_json
    assert "asset_manifest_sha256" not in run.execution_metadata_json


def test_prepare_run_release_bound_freezes_id_and_manifest_sha(client):
    _add_recording(client)
    store = FakeReleaseStore()
    service, _, _ = _service_local(client, pipeline=ReleaseBoundLocalPipeline(), store=store)
    run = _prepare_local(service, model_release_id="golden")
    assert run.execution_metadata_json["model_release_id"] == "golden"
    assert run.execution_metadata_json["asset_manifest_sha256"] == MANIFEST_SHA
    assert store.resolve_calls == [("prepare_local", "1.0", "golden")]


def test_prepare_run_invokes_actual_availability(client):
    _add_recording(client)
    service, _, registry = _service_local(client)
    _prepare_local(service)
    assert registry.availability_calls == [("prepare_local", "local_cpu")]


def test_prepare_run_rejects_invalid_parameters_without_side_effects(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    with pytest.raises(PlatformError) as exc:
        _prepare_local(service, parameters={"unknown": 1})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_caller_rollback_after_prepare_leaves_no_durable_run(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run_id = run.id
    service.session.rollback()
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run_id) is None


def test_caller_can_add_companion_row_in_same_transaction(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.add(DetectionResultModel(
        id="det_companion", run_id=run.id, t_start_s=0.0, t_end_s=0.1,
        f_low_hz=0.0, f_high_hz=1.0, class_id=0, class_name="x", confidence=0.5,
    ))
    service.session.commit()
    assert provider.launches == []  # commit did not launch
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is not None
        assert fresh.get(DetectionResultModel, "det_companion") is not None
