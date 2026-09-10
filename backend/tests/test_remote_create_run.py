from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
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
    def __init__(self, *, available: bool = True, reason_code: str | None = None):
        self.available = available
        self.reason_code = reason_code
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256):
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
    def __init__(self, release_id: str = "golden"):
        self.release_id = release_id
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        from app.remote_execution.model_release import ResolvedModelRelease

        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id,
            plugin_version=plugin_version,
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
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space=label_space, source_data_sha256="1" * 64,
        ))
        session.commit()


def _service(client, *, probe, launcher, pipeline_cls, registry_pipeline=None):
    registry = PipelineRegistry([(registry_pipeline or pipeline_cls)()])
    with client.app.state.database.session_factory() as session:
        return AnalysisService(
            session,
            registry,
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


def test_remote_create_run_requires_remote_gpu_capability(client):
    probe = FakeProbe(available=True)
    launcher = FakeLauncher()
    _add_recording(client)

    class LocalCapable(Pipeline):
        @property
        def definition(self):
            return PipelineDefinition(id="local_test", name="l", version="1", label_space="spacenet_14",
                                      recommended_device="CPU", cpu_supported=True, stages=(),
                                      inspectable_stages=(), executors_supported=("local_cpu",),
                                      recommended_executor="local_cpu")

        def run(self, recording, parameters, workspace):
            raise AssertionError

    service = _service(client, probe=probe, launcher=launcher, pipeline_cls=LocalCapable)
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="local_test", executor="remote_gpu", parameters={})
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"
    assert probe.calls == []
    assert launcher.launches == []


def test_remote_create_run_validates_label_space(client):
    _add_recording(client, label_space="signal_presence_v1")
    service = _service(client, probe=FakeProbe(available=True), launcher=FakeLauncher(),
                       pipeline_cls=RemoteCapablePipeline)
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert exc.value.code == "INPUT_INCOMPATIBLE"


def test_remote_create_run_does_not_reject_cpu_supported_true(client):
    _add_recording(client)
    launcher = FakeLauncher()
    service = _service(client, probe=FakeProbe(available=True), launcher=launcher,
                       pipeline_cls=lambda: RemoteCapablePipeline(cpu_supported=True))
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert run.executor == "remote_gpu"
    assert launcher.launches == [(run.id, run.execution_metadata_json["coordinator_token"])]


def test_remote_create_run_rejects_parameters_not_in_plugin_schema(client):
    _add_recording(client)
    launcher = FakeLauncher()
    service = _service(client, probe=FakeProbe(available=True), launcher=launcher,
                       pipeline_cls=RemoteCapablePipeline)
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={"foo": 1})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert launcher.launches == []


def test_remote_create_run_consults_probe_before_dispatch(client):
    _add_recording(client)
    launcher = FakeLauncher()
    service = _service(client, probe=FakeProbe(available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"),
                       launcher=launcher, pipeline_cls=RemoteCapablePipeline)
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert launcher.launches == []


def test_remote_create_run_freezes_provenance_with_local_run_id(client):
    _add_recording(client)
    service = _service(client, probe=FakeProbe(available=True), launcher=FakeLauncher(),
                       pipeline_cls=RemoteCapablePipeline)
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    m = run.execution_metadata_json
    assert m["local_run_id"] == run.id
    assert m["request_id"] and m["batch_id"] and m["item_key"] and m["request_sha256"]
    assert m["recording_fingerprint"] == "2" * 64
    assert m["source_data_sha256"] == "1" * 64
    assert m["dataset_key"] == "0"
    assert m["parameters"] == {}
    assert "payload_sha256" not in m
    assert run.pipeline_id == "remote_test"


def test_remote_create_run_final_metadata_contains_coordinator_token(client):
    _add_recording(client)
    service = _service(client, probe=FakeProbe(available=True), launcher=FakeLauncher(),
                       pipeline_cls=RemoteCapablePipeline)
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert "coordinator_token" in run.execution_metadata_json


def test_remote_create_run_launches_coordinator_not_local_worker(client):
    _add_recording(client)
    launcher = FakeLauncher()
    service = _service(client, probe=FakeProbe(available=True), launcher=launcher,
                       pipeline_cls=RemoteCapablePipeline)
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert launcher.launches == [(run.id, run.execution_metadata_json["coordinator_token"])]
    assert run.worker_pid == 12345


def test_remote_create_run_failed_launch_marks_failed(client):
    class RaisingLauncher:
        def launch(self, run_id, coordinator_token):
            raise RuntimeError("boom")

    _add_recording(client)
    service = _service(client, probe=FakeProbe(available=True), launcher=RaisingLauncher(),
                       pipeline_cls=RemoteCapablePipeline)
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test", executor="remote_gpu", parameters={})
    assert exc.value.code == "ANALYSIS_FAILED"


def test_local_cpu_path_unchanged(client):
    from app.pipelines.dummy import DummyPipeline

    _add_recording(client)
    with client.app.state.database.session_factory() as session:
        service = AnalysisService(
            session,
            PipelineRegistry([DummyPipeline()]),
            client.app.state.job_manager,
            executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
        )
        run = service.create_run(recording_id="rec_x", pipeline_id="dummy", executor="local_cpu", parameters={})
        assert run.executor == "local_cpu"


def test_executor_availability_route_success(client):
    _add_recording(client)
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.remote_executor_probe = FakeProbe(available=True)
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.model_release_store = FakeModelReleaseStore()
    client.app.state.executor_registry = FakeRegistry(
        {"remote_gpu": FakeProvider("remote_gpu", probe=client.app.state.remote_executor_probe)}
    )
    response = client.get("/api/executor-availability", params={"recording_id": "rec_x", "pipeline_id": "remote_test"})
    assert response.status_code == 200
    assert response.json()["available"] is True


def test_executor_availability_route_unavailable(client):
    _add_recording(client)
    client.app.state.pipeline_registry = PipelineRegistry([RemoteCapablePipeline()])
    client.app.state.remote_executor_probe = FakeProbe(available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE")
    client.app.state.remote_coordinator_launcher = FakeLauncher()
    client.app.state.identity_resolver = _identity_resolver
    client.app.state.orchestrator_commit_resolver = _orch_commit_resolver
    client.app.state.runtime_commit_config = RUN
    client.app.state.project_root = Path("/tmp")
    client.app.state.data_root = Path("/tmp/data")
    client.app.state.model_release_store = FakeModelReleaseStore()
    client.app.state.executor_registry = FakeRegistry(
        {"remote_gpu": FakeProvider("remote_gpu", probe=client.app.state.remote_executor_probe)}
    )
    response = client.get("/api/executor-availability", params={"recording_id": "rec_x", "pipeline_id": "remote_test"})
    assert response.status_code == 200
    assert response.json()["available"] is False