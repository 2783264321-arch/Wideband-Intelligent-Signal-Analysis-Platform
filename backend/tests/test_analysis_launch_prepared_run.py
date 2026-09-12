from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import ResolvedModelRelease

from executor_fixtures import FakeProvider, FakeRegistry

RUN = "a" * 40
MANIFEST_SHA = "b" * 64
MANIFEST = "b" * 64


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


def test_launch_prepared_run_local_launches_existing_run_once(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.commit()
    launched = service.launch_prepared_run(run.id)
    assert provider.launches == [(run.id, None)]
    assert launched.worker_pid == 4242
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 1


def test_launch_prepared_run_remote_uses_frozen_coordinator_token(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client)
    run = _prepare_remote(service)
    frozen_token = run.execution_metadata_json["coordinator_token"]
    service.session.commit()
    service.launch_prepared_run(run.id)
    assert launcher.launches == [(run.id, frozen_token)]
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(AnalysisRunModel, run.id)
        assert stored.execution_metadata_json["coordinator_token"] == frozen_token
        assert stored.worker_pid == 12345


def test_launch_prepared_run_multiple_calls_do_not_relaunch(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.commit()
    service.launch_prepared_run(run.id)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == [(run.id, None)]


def test_launch_prepared_run_missing_run_fails_closed(client):
    service, provider, _ = _service_local(client)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run("missing")
    assert exc.value.code == "ANALYSIS_RUN_NOT_FOUND"
    assert provider.launches == []


def test_launch_prepared_run_rejects_staged_unflushed_run(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    assert run in service.session.new  # staged/unflushed before the call
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []
    assert run in service.session.new  # the guard did not flush the Session
    assert run.status == "pending"
    assert run.worker_pid is None
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_launch_prepared_run_rejects_non_pending_status(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run.status = "completed"
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []


def test_launch_prepared_run_rejects_existing_worker_pid(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run.worker_pid = 1
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []


def test_launch_prepared_run_missing_provider_fails_closed(client):
    _add_recording(client)
    session = client.app.state.database.session_factory()
    session.add(AnalysisRunModel(
        id="run_noprov", recording_id="rec_x", pipeline_id="prepare_local",
        pipeline_version="1.0", executor="local_cpu", status="pending",
        parameters_json={}, execution_metadata_json={"runtime_descriptor": {}},
    ))
    session.commit()
    empty = AnalysisService(
        client.app.state.database.session_factory(),
        PipelineRegistry([]),
        client.app.state.job_manager,
        executor_registry=FakeRegistry({}),
    )
    with pytest.raises(PlatformError) as exc:
        empty.launch_prepared_run("run_noprov")
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_launch_prepared_run_remote_missing_token_fails_closed(client):
    session = client.app.state.database.session_factory()
    session.add(AnalysisRunModel(
        id="run_notoken", recording_id="rec_x", pipeline_id="remote_test",
        pipeline_version="1.0", executor="remote_gpu", status="pending",
        parameters_json={}, execution_metadata_json={"runtime_descriptor": {}},
    ))
    session.commit()
    service, _, launcher = _service_remote(client)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run("run_notoken")
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert launcher.launches == []


def test_launch_prepared_run_local_failure_marks_failed_and_persists(client):
    _add_recording(client)
    class RaisingProvider(FakeProvider):
        def launch(self, run_id, *, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_local(client, provider=RaisingProvider("local_cpu"))
    run = _prepare_local(service)
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_FAILED"
    assert exc.value.message == "Unable to launch local inference worker."
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(AnalysisRunModel, run.id)
        assert stored.status == "failed"
        assert stored.error_type == "ANALYSIS_FAILED"
        assert stored.error_message == "boom"


def test_launch_prepared_run_remote_failure_uses_remote_message(client):
    _add_recording(client)
    class RaisingLauncher:
        def launch(self, run_id, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_remote(client, launcher=RaisingLauncher())
    run = _prepare_remote(service)
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_FAILED"
    assert exc.value.message == "Unable to launch remote coordinator."
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id).status == "failed"
