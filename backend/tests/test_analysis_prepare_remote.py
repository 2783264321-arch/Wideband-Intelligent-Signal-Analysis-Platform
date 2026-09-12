from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.request_builder import build_batch

from executor_fixtures import FakeProvider, FakeRegistry

RUN = "a" * 40
MANIFEST = "b" * 64


class RemoteCapablePipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="remote_test", name="Remote Test", version="1.0", label_space="spacenet_14",
            recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
            task_capability="detection_classification", executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu", model_release_required=True,
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeProbe:
    def __init__(self, *, available=True, reason_code=None):
        self.available = available
        self.reason_code = reason_code
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        self.calls.append((recording.id, pipeline.id))
        if self.available:
            return ExecutorAvailabilityRead(executor="remote_gpu", available=True,
                                            reason_code=None, reason_message=None,
                                            remote_profile="autodl_primary", recommended=True)
        return ExecutorAvailabilityRead(executor="remote_gpu", available=False,
                                        reason_code=self.reason_code or "REMOTE_EXECUTOR_UNAVAILABLE",
                                        reason_message="unavailable", remote_profile="autodl_primary",
                                        recommended=False)


class FakeModelReleaseStore:
    def __init__(self, release_id="golden"):
        self.release_id = release_id
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version,
            model_release_id=requested or self.release_id,
            asset_manifest_path=Path("/tmp/asset_manifest.json"),
            asset_manifest_sha256=MANIFEST,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST)
        return ResolvedModelRelease(release=release, manifest=manifest)


class FakeLauncher:
    def __init__(self):
        self.launches = []

    def launch(self, run_id, coordinator_token):
        self.launches.append((run_id, coordinator_token))
        return 12345


def _identity_resolver(session, recording, data_root, local_run_id):
    return SimpleNamespace(
        recording_fingerprint="2" * 64, source_data_sha256="1" * 64,
        dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
        label_space="spacenet_14", local_run_id=local_run_id,
    )


def _orch_commit_resolver(project_root):
    return RUN


def _add_recording(client, recording_id="rec_x", label_space="spacenet_14"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space=label_space, source_data_sha256="1" * 64,
        ))
        session.commit()


def _service_remote(client, *, probe=None, launcher=None, pipeline=None):
    session = client.app.state.database.session_factory()
    probe = probe or FakeProbe()
    launcher = launcher or FakeLauncher()
    service = AnalysisService(
        session,
        PipelineRegistry([pipeline or RemoteCapablePipeline()]),
        client.app.state.job_manager,
        remote_executor_probe=probe,
        remote_coordinator_launcher=launcher,
        identity_resolver=_identity_resolver,
        orchestrator_commit_resolver=_orch_commit_resolver,
        model_release_store=FakeModelReleaseStore(),
        runtime_commit_config=RUN,
        project_root=Path("/tmp"),
        data_root=Path("/tmp/data"),
        executor_registry=FakeRegistry(
            {"remote_gpu": FakeProvider("remote_gpu", probe=probe, launcher=launcher)}
        ),
    )
    return service, probe, launcher


def _prepare_remote(service, **overrides):
    kwargs = dict(recording_id="rec_x", pipeline_id="remote_test",
                  executor="remote_gpu", parameters={})
    kwargs.update(overrides)
    return service.prepare_run(**kwargs)


def test_prepare_run_remote_stages_without_launch(client):
    _add_recording(client)
    service, probe, launcher = _service_remote(client)
    run = _prepare_remote(service)
    assert run.status == "pending"
    assert run.worker_pid is None
    assert run.executor == "remote_gpu"
    assert probe.calls == [("rec_x", "remote_test")]
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_remote_freezes_full_provenance_and_token_before_commit(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client)
    run = _prepare_remote(service)
    metadata = run.execution_metadata_json
    assert metadata["local_run_id"] == run.id
    assert metadata["request_id"] and metadata["batch_id"] and metadata["item_key"]
    assert metadata["request_sha256"] == compute_request_sha256(build_batch(metadata))
    assert metadata["recording_fingerprint"] == "2" * 64
    assert metadata["source_data_sha256"] == "1" * 64
    assert metadata["model_release_id"] == "golden"
    assert metadata["asset_manifest_sha256"] == MANIFEST
    assert metadata["coordinator_token"]
    assert metadata["runtime_descriptor"]["executor"] == "remote_gpu"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_remote_provenance_matches_legacy_request_identity(client):
    # request_sha256 is reconstructed purely from persisted metadata: launch must
    # never need to rebuild a scientifically different request.
    _add_recording(client)
    service, _, _ = _service_remote(client)
    run = _prepare_remote(service)
    metadata = dict(run.execution_metadata_json)
    assert build_batch(metadata).request_sha256 == metadata["request_sha256"]


def test_prepare_run_remote_rejects_missing_release(client):
    from dataclasses import replace
    _add_recording(client)
    class ReleaseLess(RemoteCapablePipeline):
        @property
        def definition(self):
            return replace(RemoteCapablePipeline().definition, model_release_required=False)
    service, _, launcher = _service_remote(client, pipeline=ReleaseLess())
    with pytest.raises(PlatformError) as exc:
        _prepare_remote(service)
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    assert launcher.launches == []


def test_prepare_run_remote_consults_probe_before_staging(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client, probe=FakeProbe(
        available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"))
    with pytest.raises(PlatformError) as exc:
        _prepare_remote(service)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_freeze_remote_provenance_helper_is_the_single_seam(client):
    _add_recording(client)
    service, _, _ = _service_remote(client)
    recording = service.session.get(RecordingModel, "rec_x")
    definition = RemoteCapablePipeline().definition
    resolved = FakeModelReleaseStore().resolve("remote_test", "1.0", None)
    provider = service.executor_registry.provider("remote_gpu")
    availability = FakeProbe().availability(recording, definition, "1" * 64)

    metadata = service._freeze_remote_provenance(
        run_id="run_seam",
        recording=recording,
        definition=definition,
        resolved_release=resolved,
        provider=provider,
        availability=availability,
        parameters={"threshold": 0.5},
    )
    assert metadata["local_run_id"] == "run_seam"
    assert metadata["parameters"] == {"threshold": 0.5}
    assert metadata["request_sha256"] == compute_request_sha256(build_batch(metadata))
    assert metadata["coordinator_token"]
    assert metadata["runtime_descriptor"]["executor"] == "remote_gpu"


def _add_real_source_recording(client, tmp_path, *, recording_id="rec_real"):
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    raw = data_root / recording_id / "raw.iq"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"\x00\x01\x02\x03" * 256)
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path=f"{recording_id}/raw.iq",
            data_format="complex64_le", sample_rate_hz=1e6, center_frequency_hz=0.0,
            frequency_low_hz=-5e5, frequency_high_hz=5e5, num_samples=1024,
            duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256=None,
        ))
        session.commit()
    return data_root


def _service_remote_real_identity(client, *, data_root):
    from app.remote_execution.identity import resolve_remote_recording_identity
    session = client.app.state.database.session_factory()
    launcher = FakeLauncher()
    service = AnalysisService(
        session,
        PipelineRegistry([RemoteCapablePipeline()]),
        client.app.state.job_manager,
        remote_coordinator_launcher=launcher,
        identity_resolver=resolve_remote_recording_identity,
        orchestrator_commit_resolver=_orch_commit_resolver,
        model_release_store=FakeModelReleaseStore(),
        runtime_commit_config=RUN,
        project_root=Path("/tmp"),
        data_root=data_root,
        executor_registry=FakeRegistry(
            {"remote_gpu": FakeProvider("remote_gpu", probe=FakeProbe(), launcher=launcher)}
        ),
    )
    return service, launcher


def test_prepare_run_remote_real_identity_has_no_hidden_commit(client, tmp_path):
    data_root = _add_real_source_recording(client, tmp_path)
    service, launcher = _service_remote_real_identity(client, data_root=data_root)

    run = service.prepare_run(recording_id="rec_real", pipeline_id="remote_test",
                              executor="remote_gpu", parameters={})
    recording = service.session.get(RecordingModel, "rec_real")
    assert run.status == "pending"
    assert run in service.session.new
    assert recording.source_data_sha256 is not None  # staged in memory
    assert launcher.launches == []

    # Caller rollback must discard BOTH the Run and the source-hash cache.
    service.session.rollback()
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None
        assert fresh.get(RecordingModel, "rec_real").source_data_sha256 is None


def test_prepare_run_remote_real_identity_caller_commit_persists_both(client, tmp_path):
    data_root = _add_real_source_recording(client, tmp_path)
    service, launcher = _service_remote_real_identity(client, data_root=data_root)

    run = service.prepare_run(recording_id="rec_real", pipeline_id="remote_test",
                              executor="remote_gpu", parameters={})
    service.session.commit()
    assert launcher.launches == []  # preparation commit did not launch
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is not None
        assert fresh.get(RecordingModel, "rec_real").source_data_sha256 is not None
