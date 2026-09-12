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


def test_create_run_composes_prepare_commit_launch(client, monkeypatch):
    _add_recording(client)
    service, provider, _ = _service_local(client)

    events = []
    real_prepare = AnalysisService.prepare_run
    real_launch = AnalysisService.launch_prepared_run
    real_commit = service.session.commit

    def spy_prepare(self, **kwargs):
        run = real_prepare(self, **kwargs)
        events.append(("prepare", run.id))
        return run

    def spy_commit(*args, **kwargs):
        events.append(("commit", None))
        return real_commit(*args, **kwargs)

    def spy_launch(self, run_id):
        events.append(("launch", run_id))
        return real_launch(self, run_id)

    monkeypatch.setattr(AnalysisService, "prepare_run", spy_prepare)
    monkeypatch.setattr(AnalysisService, "launch_prepared_run", spy_launch)
    monkeypatch.setattr(service.session, "commit", spy_commit)

    run = service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                             executor="local_cpu", parameters={})

    # prepare -> preparation commit -> launch -> launch-result commit
    assert [event[0] for event in events] == ["prepare", "commit", "launch", "commit"]
    assert [event[1] for event in events] == [run.id, None, run.id, None]
    assert provider.launches == [(run.id, None)]


def test_create_run_local_unchanged_observable_behavior(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                             executor="local_cpu", parameters={"threshold": 0.5})
    assert run.status == "pending"
    assert run.worker_pid == 4242
    assert run.executor == "local_cpu"
    assert run.parameters_json == {"threshold": 0.5}
    assert provider.launches == [(run.id, None)]
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 1


def test_create_run_remote_unchanged_observable_behavior(client):
    _add_recording(client)
    service, probe, launcher = _service_remote(client)
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                             executor="remote_gpu", parameters={})
    assert run.worker_pid == 12345
    assert launcher.launches == [(run.id, run.execution_metadata_json["coordinator_token"])]
    assert probe.calls == [("rec_x", "remote_test")]


def test_create_run_available_false_raises_before_staging(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client, probe=FakeProbe(
        available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"))
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                           executor="remote_gpu", parameters={})
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_create_run_prepare_commit_failure_skips_launch(client, monkeypatch):
    # Compatibility regression, not the refactor RED: current legacy behavior
    # also commits before launching, so a failed preparation commit must never
    # reach provider.launch.
    _add_recording(client)
    service, provider, _ = _service_local(client)
    launched = []
    real_launch = AnalysisService.launch_prepared_run

    def spy_launch(self, run_id):
        launched.append(run_id)
        return real_launch(self, run_id)

    monkeypatch.setattr(AnalysisService, "launch_prepared_run", spy_launch)

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(service.session, "commit", boom)
    with pytest.raises(RuntimeError):
        service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                           executor="local_cpu", parameters={})
    assert launched == []
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_create_run_remote_failed_launch_marks_failed(client):
    _add_recording(client)
    class RaisingLauncher:
        def launch(self, run_id, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_remote(client, launcher=RaisingLauncher())
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                           executor="remote_gpu", parameters={})
    assert exc.value.code == "ANALYSIS_FAILED"


