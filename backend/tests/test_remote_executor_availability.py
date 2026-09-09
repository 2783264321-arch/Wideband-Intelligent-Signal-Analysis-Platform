from pathlib import Path
from typing import Any

import pytest

from benchmark_fixture import add_recording

from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.dummy import DummyPipeline
from app.pipelines.registry import PipelineRegistry
from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline
from app.recordings.model import RecordingModel


class RemoteCapableTestPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="remote_test",
            name="Remote Test",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="GPU",
            cpu_supported=False,
            stages=(),
            inspectable_stages=(),
            executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu",
        )

    def run(self, recording: RecordingInput, parameters: dict[str, Any], workspace: Path) -> PipelineOutput:
        raise AssertionError("test pipeline must not execute")


TEST_REGISTRY = PipelineRegistry([STFTEnergyDetectorPipeline(), RemoteCapableTestPipeline()])


class FakeRemoteExecutorProbe:
    def __init__(self, *, available: bool, reason_code: str | None = None,
                 remote_profile: str | None = "autodl_primary"):
        self.available_value = available
        self.reason_code = reason_code
        self.remote_profile = remote_profile
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256):
        self.calls.append((recording.id, pipeline.id, source_data_sha256))
        if self.available_value:
            return ExecutorAvailabilityRead(
                executor="remote_gpu", available=True,
                reason_code=None, reason_message=None,
                remote_profile=self.remote_profile, recommended=True,
            )
        return ExecutorAvailabilityRead(
            executor="remote_gpu", available=False,
            reason_code=self.reason_code or "REMOTE_EXECUTOR_UNAVAILABLE",
            reason_message="Remote GPU executor is unavailable.",
            remote_profile=self.remote_profile, recommended=False,
        )


def _add_sn_recording(client, *, recording_id, label_space="spacenet_14"):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name="0", label_space=label_space)
        session.commit()


def _availability(client, recording_id, pipeline_id, probe=None, registry=TEST_REGISTRY):
    with client.app.state.database.session_factory() as session:
        service = AnalysisService(session, registry, client.app.state.job_manager,
                                  remote_executor_probe=probe)
        return service.executor_availability(recording_id, pipeline_id)


def test_pipeline_definition_executor_defaults():
    dummy = DummyPipeline().definition
    assert dummy.executors_supported == ("local_cpu",)
    assert dummy.recommended_executor == "local_cpu"
    stft = STFTEnergyDetectorPipeline().definition
    assert stft.executors_supported == ("local_cpu",)
    assert stft.recommended_executor == "local_cpu"


def test_pipelines_endpoint_exposes_static_executor_capability(client):
    response = client.get("/api/pipelines")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert by_id["dummy"]["executors_supported"] == ["local_cpu"]
    assert by_id["dummy"]["recommended_executor"] == "local_cpu"
    assert by_id["stft_energy_detector"]["executors_supported"] == ["local_cpu"]
    assert by_id["stft_energy_detector"]["recommended_executor"] == "local_cpu"


def test_remote_gpu_unavailable_for_non_remote_pipeline(client):
    probe = FakeRemoteExecutorProbe(available=True)
    _add_sn_recording(client, recording_id="rec_local")
    availability = _availability(client, "rec_local", "stft_energy_detector", probe=probe)
    assert availability.executor == "remote_gpu"
    assert availability.available is False
    assert availability.reason_code == "PIPELINE_NOT_REMOTE_CAPABLE"
    assert availability.recommended is False
    assert probe.calls == []


def test_remote_gpu_unavailable_without_probe(client):
    _add_sn_recording(client, recording_id="rec_sn")
    availability = _availability(client, "rec_sn", "remote_test", probe=None)
    assert availability.available is False
    assert availability.reason_code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert availability.recommended is False
    assert availability.remote_profile is None


def test_remote_gpu_unavailable_with_unavailable_probe(client):
    _add_sn_recording(client, recording_id="rec_sn")
    availability = _availability(
        client, "rec_sn", "remote_test",
        probe=FakeRemoteExecutorProbe(available=False),
    )
    assert availability.available is False
    assert availability.reason_code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert availability.recommended is False


def test_remote_gpu_available_with_configured_probe(client):
    _add_sn_recording(client, recording_id="rec_sn")
    availability = _availability(
        client, "rec_sn", "remote_test",
        probe=FakeRemoteExecutorProbe(available=True),
    )
    assert availability.available is True
    assert availability.reason_code is None
    assert availability.reason_message is None
    assert availability.recommended is True
    assert availability.remote_profile == "autodl_primary"


def test_cached_source_sha256_forwarded_exactly(client):
    probe = FakeRemoteExecutorProbe(available=True)
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_sn", name="0")
        recording = session.get(RecordingModel, "rec_sn")
        recording.source_data_sha256 = "a" * 64
        session.commit()
    availability = _availability(client, "rec_sn", "remote_test", probe=probe)
    assert availability.available is True
    assert probe.calls == [("rec_sn", "remote_test", "a" * 64)]


def test_label_space_mismatch_never_invokes_probe(client):
    probe = FakeRemoteExecutorProbe(available=True)
    _add_sn_recording(client, recording_id="rec_mismatch", label_space="signal_presence_v1")
    availability = _availability(client, "rec_mismatch", "remote_test", probe=probe)
    assert availability.available is False
    assert availability.reason_code == "PIPELINE_INCOMPATIBLE"
    assert availability.recommended is False
    assert probe.calls == []


def test_missing_recording_raises_not_found(client):
    with pytest.raises(PlatformError) as exc:
        _availability(client, "rec_missing", "remote_test",
                      probe=FakeRemoteExecutorProbe(available=True))
    assert exc.value.code == "RECORDING_NOT_FOUND"


def test_create_run_remote_gpu_not_yet_dispatched(client):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_local", name="local", label_space="spacenet_14")
        session.commit()
    with client.app.state.database.session_factory() as session:
        service = AnalysisService(session, client.app.state.pipeline_registry,
                                  client.app.state.job_manager)
        with pytest.raises(PlatformError) as exc:
            service.create_run(
                recording_id="rec_local", pipeline_id="dummy",
                executor="remote_gpu", parameters={},
            )
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"