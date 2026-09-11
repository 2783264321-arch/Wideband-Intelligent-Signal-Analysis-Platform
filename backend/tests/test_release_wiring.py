"""TASK B4 — control-plane ModelRelease wiring.

Covers:
- create_app constructs app.state.model_release_store even without remote config;
- AnalysisService resolves the requested/default release exactly once and freezes
  both model_release_id and asset_manifest_sha256 into B3 request provenance;
- explicit requested release overrides the platform default;
- identity.resolve_asset_manifest_sha256 delegates through ModelReleaseStore.resolve;
- API AnalysisRunCreate.model_release_id threads router -> service;
- the public API read model does not leak manifest paths / release internals.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.analysis.schema import AnalysisRunCreate, ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.config import Settings
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel

from executor_fixtures import FakeProvider, FakeRegistry

RUN = "a" * 40
MANIFEST = "b" * 64


class RemoteCapablePipeline(Pipeline):
    def __init__(self, *, cpu_supported=False):
        self._cpu_supported = cpu_supported

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="remote_test",
            name="Remote Test",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="GPU",
            cpu_supported=self._cpu_supported,
            stages=(),
            inspectable_stages=(),
            task_capability="detection_classification",
            executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu",
            model_release_required=True,
        )

    def run(self, recording: RecordingInput, parameters: dict, workspace: Path) -> PipelineOutput:
        raise AssertionError("test pipeline must not execute")


class FakeProbe:
    def __init__(self):
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
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
    def __init__(self):
        self.launches = []

    def launch(self, run_id, coordinator_token):
        self.launches.append((run_id, coordinator_token))
        return 12345


class FakeModelReleaseStore:
    """Records resolve() calls; never exposes resolve_by_manifest_sha."""

    def __init__(self, default_release_id="golden"):
        self.default_release_id = default_release_id
        self.resolve_calls = []
        self.reverse_lookup_calls = 0

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release_id = requested or self.default_release_id
        release = SimpleNamespace(
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            model_release_id=release_id,
            asset_manifest_path=Path("/private/deployment/asset_manifest.json"),
            asset_manifest_sha256=MANIFEST,
        )
        manifest = SimpleNamespace(
            pipeline_id=plugin_id,
            pipeline_version=plugin_version,
            asset_manifest_sha256=MANIFEST,
        )
        from app.remote_execution.model_release import ResolvedModelRelease

        return ResolvedModelRelease(release=release, manifest=manifest)

    def resolve_by_manifest_sha(self, *args, **kwargs):  # pragma: no cover - guard
        self.reverse_lookup_calls += 1
        raise AssertionError("B4 must resolve via resolve(), never reverse lookup")


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


def _orch_commit_resolver(project_root):
    return RUN


def _add_recording(client, recording_id="rec_x", label_space="spacenet_14"):
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
                label_space=label_space,
                source_data_sha256="1" * 64,
            )
        )
        session.commit()


def _service(client, *, store):
    registry = PipelineRegistry([RemoteCapablePipeline()])
    probe = FakeProbe()
    launcher = FakeLauncher()
    with client.app.state.database.session_factory() as session:
        return AnalysisService(
            session,
            registry,
            client.app.state.job_manager,
            remote_executor_probe=probe,
            remote_coordinator_launcher=launcher,
            identity_resolver=_identity_resolver,
            orchestrator_commit_resolver=_orch_commit_resolver,
            runtime_commit_config=RUN,
            project_root=Path("/tmp"),
            data_root=Path("/tmp/data"),
            model_release_store=store,
            executor_registry=FakeRegistry(
                {"remote_gpu": FakeProvider("remote_gpu", probe=probe, launcher=launcher)}
            ),
        )


def test_create_app_builds_model_release_store_without_remote_config(tmp_path):
    from app.main import create_app
    from app.remote_execution.model_release import ModelReleaseStore

    settings = Settings(
        project_root=tmp_path,
        data_root=tmp_path / "data",
        label_space_root=tmp_path / "label_spaces",
        database_url=f"sqlite:///{tmp_path / 'wiring.db'}",
    )
    app = create_app(settings)
    assert isinstance(app.state.model_release_store, ModelReleaseStore)
    assert app.state.remote_config_available is False


def test_service_resolves_default_release_into_metadata(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    service = _service(client, store=store)

    run = service.create_run(
        recording_id="rec_x",
        pipeline_id="remote_test",
        executor="remote_gpu",
        parameters={},
    )

    assert store.resolve_calls == [("remote_test", "1.0", None)]
    assert store.reverse_lookup_calls == 0
    metadata = run.execution_metadata_json
    assert metadata["model_release_id"] == "golden"
    assert metadata["asset_manifest_sha256"] == MANIFEST


def test_service_explicit_release_overrides_default(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    service = _service(client, store=store)

    run = service.create_run(
        recording_id="rec_x",
        pipeline_id="remote_test",
        executor="remote_gpu",
        parameters={},
        model_release_id="tuned",
    )

    assert store.resolve_calls == [("remote_test", "1.0", "tuned")]
    assert run.execution_metadata_json["model_release_id"] == "tuned"
    assert run.execution_metadata_json["asset_manifest_sha256"] == MANIFEST


def test_identity_resolver_delegates_to_store_resolve():
    from app.remote_execution.identity import resolve_asset_manifest_sha256

    store = FakeModelReleaseStore()
    sha = resolve_asset_manifest_sha256(store, "remote_test", "1.0", "tuned")
    assert sha == MANIFEST
    assert store.resolve_calls == [("remote_test", "1.0", "tuned")]


def test_api_explicit_release_threads_router_to_service(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.model_release_store = store
    client.app.state.remote_executor_probe = FakeProbe()
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.executor_registry = FakeRegistry(
        {
            "remote_gpu": FakeProvider(
                "remote_gpu",
                probe=client.app.state.remote_executor_probe,
                launcher=client.app.state.remote_coordinator_launcher,
            )
        }
    )

    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "remote_test",
            "executor": "remote_gpu",
            "parameters": {},
            "model_release_id": "tuned",
        },
    )
    assert response.status_code == 201
    assert store.resolve_calls == [("remote_test", "1.0", "tuned")]

    run_id = response.json()["id"]
    with client.app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel

        stored = session.get(AnalysisRunModel, run_id)
        assert stored.execution_metadata_json["model_release_id"] == "tuned"
        assert stored.execution_metadata_json["asset_manifest_sha256"] == MANIFEST


def test_api_default_release_when_model_release_id_omitted(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.model_release_store = store
    client.app.state.remote_executor_probe = FakeProbe()
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.executor_registry = FakeRegistry(
        {
            "remote_gpu": FakeProvider(
                "remote_gpu",
                probe=client.app.state.remote_executor_probe,
                launcher=client.app.state.remote_coordinator_launcher,
            )
        }
    )

    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "remote_test",
            "executor": "remote_gpu",
            "parameters": {},
        },
    )
    assert response.status_code == 201
    assert store.resolve_calls == [("remote_test", "1.0", None)]


def test_api_read_does_not_expose_release_internals(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.model_release_store = store
    client.app.state.remote_executor_probe = FakeProbe()
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.executor_registry = FakeRegistry(
        {
            "remote_gpu": FakeProvider(
                "remote_gpu",
                probe=client.app.state.remote_executor_probe,
                launcher=client.app.state.remote_coordinator_launcher,
            )
        }
    )

    run_id = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "remote_test",
            "executor": "remote_gpu",
            "parameters": {},
            "model_release_id": "tuned",
        },
    ).json()["id"]

    body = client.get(f"/api/analysis-runs/{run_id}").json()
    serialized = json.dumps(body)
    assert "asset_manifest_path" not in serialized
    assert "/private/deployment" not in serialized
    assert "asset_manifest_sha256" not in serialized


# ---------------------------------------------------------------- FIX ROUND 1
# Explicit model_release_id must be validated at the API boundary against the
# ModelRelease id contract BEFORE service/store resolution. Empty/malformed
# values must fail request validation; None keeps the platform default.


def _configure_remote_app(client, store):
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.model_release_store = store
    client.app.state.remote_executor_probe = FakeProbe()
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.executor_registry = FakeRegistry(
        {
            "remote_gpu": FakeProvider(
                "remote_gpu",
                probe=client.app.state.remote_executor_probe,
                launcher=client.app.state.remote_coordinator_launcher,
            )
        }
    )


def test_analysis_run_create_allows_none_and_valid_release_ids():
    assert AnalysisRunCreate(recording_id="r", pipeline_id="p").model_release_id is None
    for valid in ("golden", "golden.v2", "v1_0", "a", "a" * 128):
        payload = AnalysisRunCreate(recording_id="r", pipeline_id="p", model_release_id=valid)
        assert payload.model_release_id == valid


@pytest.mark.parametrize(
    "invalid",
    ["", " ", "Upper", "-leading", ".leading", "_leading", "with space", "@bad@", "a" * 129],
)
def test_analysis_run_create_rejects_invalid_release_ids(invalid):
    with pytest.raises(ValidationError):
        AnalysisRunCreate(recording_id="r", pipeline_id="p", model_release_id=invalid)


def test_api_rejects_empty_model_release_id_before_store_resolution(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    _configure_remote_app(client, store)

    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "remote_test",
            "executor": "remote_gpu",
            "parameters": {},
            "model_release_id": "",
        },
    )
    assert response.status_code == 422
    assert store.resolve_calls == []


def test_api_rejects_malformed_model_release_id_before_store_resolution(client):
    _add_recording(client)
    store = FakeModelReleaseStore(default_release_id="golden")
    _configure_remote_app(client, store)

    response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": "rec_x",
            "pipeline_id": "remote_test",
            "executor": "remote_gpu",
            "parameters": {},
            "model_release_id": "@@malformed@@",
        },
    )
    assert response.status_code == 422
    assert store.resolve_calls == []
