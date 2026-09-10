"""TASK C3 — validate plugin parameters and freeze them into request identity.

The parameter flow becomes:

    user parameters -> Plugin parameter schema validation
      -> frozen provenance -> canonical request -> request_sha256 -> remote worker

No plugin-specific parameter branch lives in ``AnalysisService``; the plugin's
own ``parameter_schema`` decides validity.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.canonical import canonical_request_payload
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.request_builder import build_batch, freeze_request_provenance

RUN = "a" * 40
MANIFEST = "b" * 64

_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "threshold": {"type": "number"},
        "label": {"type": "string"},
    },
}


class ParamPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="param_test",
            name="Param Test",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="detection_classification",
            executors_supported=("local_cpu", "remote_gpu"),
            recommended_executor="local_cpu",
            parameter_schema=_PARAM_SCHEMA,
        )

    def run(self, recording: RecordingInput, parameters: dict, workspace: Path) -> PipelineOutput:
        raise AssertionError("test pipeline must not execute")


# FIX ROUND 2 — a schema outside the closed A2 subset (additionalProperties
# defaults to allowed) must be rejected before any parameter can reach hashing.
_UNSUPPORTED_PERMISSIVE_SCHEMA = {
    "type": "object",
    "properties": {"threshold": {"type": "number"}},
}


class UnsupportedSchemaPipeline(ParamPipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return replace(
            ParamPipeline().definition, parameter_schema=_UNSUPPORTED_PERMISSIVE_SCHEMA
        )


class FakeJobManager:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, run_id: str) -> int:
        self.started.append(run_id)
        return 4242


class FakeProbe:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def availability(self, recording, pipeline, source_data_sha256):
        self.calls.append((recording.id, pipeline.id))
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile="autodl_primary",
            recommended=True,
        )


class FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[tuple[str, str]] = []

    def launch(self, run_id, coordinator_token):
        self.launches.append((run_id, coordinator_token))
        return 1


class FakeStore:
    def __init__(self) -> None:
        self.resolve_calls: list[tuple[str, str, str | None]] = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            model_release_id=requested or "golden",
            asset_manifest_path=Path("/tmp/asset_manifest.json"),
            asset_manifest_sha256=MANIFEST,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST)
        return ResolvedModelRelease(release=release, manifest=manifest)


def _identity_resolver(session, recording, data_root, local_run_id):
    return SimpleNamespace(
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        local_run_id=local_run_id,
    )


def _freeze_kws(**overrides) -> dict:
    base = dict(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id="param_test",
        pipeline_version="1.0",
        required_remote_runtime_commit=RUN,
        orchestrator_commit=RUN,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )
    base.update(overrides)
    return base


def _add_recording(client, recording_id="rec_x"):
    with client.app.state.database.session_factory() as session:
        session.add(
            RecordingModel(
                id=recording_id,
                name="0",
                data_path="recordings/0/raw.iq",
                data_format="complex64_le",
                sample_rate_hz=1e6,
                center_frequency_hz=0.0,
                frequency_low_hz=-5e5,
                frequency_high_hz=5e5,
                num_samples=1000,
                duration_s=0.001,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space="spacenet_14",
                source_data_sha256="1" * 64,
            )
        )
        session.commit()


def _service(
    client, *, job_manager=None, probe=None, store=None, launcher=None, pipeline_cls=ParamPipeline
) -> AnalysisService:
    registry = PipelineRegistry([pipeline_cls()])
    session = client.app.state.database.session_factory()
    return AnalysisService(
        session,
        registry,
        job_manager or FakeJobManager(),
        remote_executor_probe=probe or FakeProbe(),
        remote_coordinator_launcher=launcher or FakeLauncher(),
        identity_resolver=_identity_resolver,
        orchestrator_commit_resolver=lambda project_root: RUN,
        model_release_store=store or FakeStore(),
        runtime_commit_config=RUN,
        project_root=Path("/tmp"),
        data_root=Path("/tmp/data"),
    )


# ---------------------------------------------------------------------------
# A. frozen provenance carries parameters
# ---------------------------------------------------------------------------


def test_parameters_frozen_into_request_metadata():
    metadata = freeze_request_provenance(
        **_freeze_kws(parameters={"threshold": 0.5, "label": "x"})
    )
    assert metadata["parameters"] == {"threshold": 0.5, "label": "x"}
    assert build_batch(metadata).items[0].parameters == {"threshold": 0.5, "label": "x"}


def test_frozen_parameters_are_detached_from_caller_dict():
    caller = {"threshold": 0.5}
    metadata = freeze_request_provenance(**_freeze_kws(parameters=caller))
    caller["threshold"] = 0.9
    assert metadata["parameters"] == {"threshold": 0.5}


def test_request_sha256_changes_with_parameters():
    ids_a = iter(["rid", "bid", "ik"])
    ids_b = iter(["rid", "bid", "ik"])
    a = freeze_request_provenance(
        **_freeze_kws(parameters={"threshold": 0.5}), id_factory=lambda: next(ids_a)
    )
    b = freeze_request_provenance(
        **_freeze_kws(parameters={"threshold": 0.6}), id_factory=lambda: next(ids_b)
    )
    assert a["request_sha256"] != b["request_sha256"]
    assert canonical_request_payload(build_batch(a))["items"][0]["parameters"] == {
        "threshold": 0.5
    }


def test_omitted_and_empty_parameters_preserve_legacy_identity():
    ids_a = iter(["rid", "bid", "ik"])
    ids_b = iter(["rid", "bid", "ik"])
    omitted = freeze_request_provenance(**_freeze_kws(), id_factory=lambda: next(ids_a))
    empty = freeze_request_provenance(**_freeze_kws(parameters={}), id_factory=lambda: next(ids_b))
    assert omitted["parameters"] == {}
    assert empty["parameters"] == {}
    assert omitted["request_sha256"] == empty["request_sha256"]


def test_no_schema_defaults_are_materialized():
    metadata = freeze_request_provenance(**_freeze_kws(parameters={}))
    assert metadata["parameters"] == {}


# ---------------------------------------------------------------------------
# B. AnalysisService validates against the plugin schema
# ---------------------------------------------------------------------------


def test_empty_schema_run_rejects_parameters(client):
    _add_recording(client)
    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "dummy",
            "executor": "local_cpu",
            "parameters": {"foo": 1},
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "PLUGIN_PARAMETERS_INVALID"


def test_local_run_preserves_valid_parameters(client):
    _add_recording(client)
    job_manager = FakeJobManager()
    service = _service(client, job_manager=job_manager)
    run = service.create_run(
        recording_id="rec_x",
        pipeline_id="param_test",
        executor="local_cpu",
        parameters={"threshold": 0.5, "label": "x"},
    )
    assert run.parameters_json == {"threshold": 0.5, "label": "x"}
    assert job_manager.started == [run.id]


def test_local_run_rejects_invalid_parameters(client):
    _add_recording(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_x",
            pipeline_id="param_test",
            executor="local_cpu",
            parameters={"threshold": "high"},
        )
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_remote_run_freezes_valid_parameters(client):
    _add_recording(client)
    service = _service(client)
    run = service.create_run(
        recording_id="rec_x",
        pipeline_id="param_test",
        executor="remote_gpu",
        parameters={"threshold": 0.5},
    )
    metadata = run.execution_metadata_json
    assert metadata["parameters"] == {"threshold": 0.5}
    assert run.parameters_json == {"threshold": 0.5}
    assert build_batch(metadata).items[0].parameters == {"threshold": 0.5}


def test_remote_run_rejects_invalid_parameters(client):
    _add_recording(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_x",
            pipeline_id="param_test",
            executor="remote_gpu",
            parameters={"label": 3},
        )
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_remote_run_with_empty_parameters_keeps_legacy_metadata(client):
    _add_recording(client)
    service = _service(client)
    run = service.create_run(
        recording_id="rec_x",
        pipeline_id="param_test",
        executor="remote_gpu",
        parameters={},
    )
    assert run.execution_metadata_json["parameters"] == {}


# ---------------------------------------------------------------------------
# FIX ROUND 1 — reject non-finite numbers at validation (canonical contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validator_rejects_non_finite_numbers(value):
    from app.pipelines.plugin import validate_plugin_parameters

    with pytest.raises(PlatformError) as exc:
        validate_plugin_parameters(ParamPipeline().definition, {"threshold": value})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_validator_still_accepts_finite_numbers():
    from app.pipelines.plugin import validate_plugin_parameters

    validate_plugin_parameters(ParamPipeline().definition, {"threshold": 0.5, "label": "x"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_local_run_rejects_non_finite_parameters_before_worker(client, value):
    _add_recording(client)
    job_manager = FakeJobManager()
    service = _service(client, job_manager=job_manager)
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_x",
            pipeline_id="param_test",
            executor="local_cpu",
            parameters={"threshold": value},
        )
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert job_manager.started == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_remote_run_rejects_non_finite_parameters_before_side_effects(client, value):
    _add_recording(client)
    probe, store, launcher = FakeProbe(), FakeStore(), FakeLauncher()
    service = _service(client, probe=probe, store=store, launcher=launcher)
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_x",
            pipeline_id="param_test",
            executor="remote_gpu",
            parameters={"threshold": value},
        )
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert probe.calls == []
    assert store.resolve_calls == []
    assert launcher.launches == []
    with client.app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel

        assert session.query(AnalysisRunModel).filter(AnalysisRunModel.executor == "remote_gpu").count() == 0


def test_api_rejects_non_finite_number_parameter(client):
    _add_recording(client)
    response = client.post(
        "/api/analysis-runs",
        content=(
            '{"recording_id":"rec_x","pipeline_id":"stft_energy_detector",'
            '"executor":"local_cpu","parameters":{"noise_floor_percentile":NaN}}'
        ),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "PLUGIN_PARAMETERS_INVALID"


# ---------------------------------------------------------------------------
# FIX ROUND 2 — enforce the closed A2 subset so unsupported/nested values can
# never reach canonical request hashing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"threshold": 0.5},
        {"unknown": float("nan")},
        {"threshold": [float("nan")]},
        {"threshold": {"nested": float("inf")}},
    ],
)
def test_unsupported_permissive_schema_fails_closed(params):
    from app.pipelines.plugin import validate_plugin_parameters

    with pytest.raises(PlatformError) as exc:
        validate_plugin_parameters(UnsupportedSchemaPipeline().definition, params)
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


@pytest.mark.parametrize(
    "params",
    [
        {"unknown": float("nan")},
        {"unknown": {"nested": float("inf")}},
        {"threshold": [float("nan")]},
        {"threshold": {"nested": float("inf")}},
    ],
)
def test_supported_schema_rejects_unknown_and_nested_non_finite(params):
    from app.pipelines.plugin import validate_plugin_parameters

    with pytest.raises(PlatformError) as exc:
        validate_plugin_parameters(ParamPipeline().definition, params)
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_unsupported_schema_never_reaches_canonical_hashing(client, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(
        "app.remote_execution.request_builder.compute_request_sha256",
        lambda batch: calls.append(batch) or "0" * 64,
    )
    _add_recording(client)
    probe, store, launcher = FakeProbe(), FakeStore(), FakeLauncher()
    service = _service(
        client, probe=probe, store=store, launcher=launcher, pipeline_cls=UnsupportedSchemaPipeline
    )
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_x",
            pipeline_id="param_test",
            executor="remote_gpu",
            parameters={"unknown": float("nan")},
        )
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert calls == []
    assert probe.calls == []
    assert store.resolve_calls == []
    assert launcher.launches == []
