"""TASK C4 — input compatibility vs output label space.

Input compatibility answers "can this plugin consume this recording's dataset
label space?" and is independent of the label space the plugin emits. Output
label validation answers "do the emitted detections belong to the plugin's
output label space?" The two must not be conflated.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from benchmark_fixture import add_recording, add_run

from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.pipelines.base import (
    DetectionPayload,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.validation import AnalysisResultWriter

from executor_fixtures import FakeProvider, FakeRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"


class _StubPipeline(Pipeline):
    def run(self, recording: RecordingInput, parameters: dict, workspace: Path) -> PipelineOutput:
        raise AssertionError("test pipeline must not execute")


class CpnOnlyPipeline(_StubPipeline):
    """Consumes SpaceNet-14 recordings but emits a distinct CPN output space."""

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="cpn_only",
            name="CPN Only",
            version="1.0",
            label_space="cpn_bandwidth_tier_v1",
            output_label_space="cpn_bandwidth_tier_v1",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="detection_classification",
            executors_supported=("local_cpu", "remote_gpu"),
            recommended_executor="local_cpu",
            input_compatibility=("spacenet_14",),
            dataset_adapters=("SpaceNet",),
        )


class LocalizationLegacyPipeline(_StubPipeline):
    """Legacy detection_localization plugin with no explicit input compatibility."""

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="loc_legacy",
            name="Localization Legacy",
            version="1.0",
            label_space="signal_presence_v1",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="detection_localization",
            executors_supported=("local_cpu",),
            recommended_executor="local_cpu",
        )


class ClassificationLegacyPipeline(_StubPipeline):
    """Legacy non-localization plugin: prior label_space compatibility applies."""

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="legacy_cls",
            name="Legacy Classification",
            version="1.0",
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu",),
            recommended_executor="local_cpu",
        )


class FakeJobManager:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, run_id: str) -> int:
        self.started.append(run_id)
        return 1


class FakeProbe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def availability(self, recording, pipeline, source_data_sha256):
        self.calls.append(pipeline.id)
        return ExecutorAvailabilityRead(
            executor="remote_gpu",
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile="autodl_primary",
            recommended=True,
        )


def _add_recording(client, recording_id: str, label_space: str = "spacenet_14") -> None:
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name=recording_id, label_space=label_space)
        session.commit()


def _service(client, pipeline: Pipeline, *, probe=None, job_manager=None) -> AnalysisService:
    session = client.app.state.database.session_factory()
    return AnalysisService(
        session,
        PipelineRegistry([pipeline]),
        job_manager or FakeJobManager(),
        remote_executor_probe=probe,
        executor_registry=FakeRegistry(
            {
                "local_cpu": FakeProvider("local_cpu"),
                "remote_gpu": FakeProvider("remote_gpu", probe=probe),
            }
        ),
    )


# ---------------------------------------------------------------------------
# A. explicit input compatibility, distinct output
# ---------------------------------------------------------------------------


def test_input_compatible_output_distinct_allows_run(client):
    _add_recording(client, "rec_sn", "spacenet_14")
    pipeline = CpnOnlyPipeline()
    definition = pipeline.definition
    assert definition.input_compatibility == ("spacenet_14",)
    assert definition.resolved_output_label_space == "cpn_bandwidth_tier_v1"
    assert definition.resolved_output_label_space != definition.input_compatibility[0]

    job_manager = FakeJobManager()
    service = _service(client, pipeline, job_manager=job_manager)
    run = service.create_run(
        recording_id="rec_sn", pipeline_id="cpn_only", executor="local_cpu", parameters={}
    )
    assert run.pipeline_id == "cpn_only"
    assert run.worker_pid == 4242


def test_recordings_with_wrong_dataset_label_space_rejected(client):
    _add_recording(client, "rec_sp", "signal_presence_v1")
    probe = FakeProbe()
    service = _service(client, CpnOnlyPipeline(), probe=probe)

    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_sp", pipeline_id="cpn_only", executor="local_cpu", parameters={}
        )
    assert exc.value.code == "INPUT_INCOMPATIBLE"

    availability = service.executor_availability("rec_sp", "cpn_only")
    assert availability.available is False
    assert availability.reason_code == "INPUT_INCOMPATIBLE"
    assert probe.calls == []


def test_remote_create_run_rejects_wrong_input_before_probe(client):
    _add_recording(client, "rec_sp", "signal_presence_v1")
    probe = FakeProbe()
    service = _service(client, CpnOnlyPipeline(), probe=probe)
    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_sp", pipeline_id="cpn_only", executor="remote_gpu", parameters={}
        )
    assert exc.value.code == "INPUT_INCOMPATIBLE"
    assert probe.calls == []


# ---------------------------------------------------------------------------
# B. legacy fallbacks must not change accepted data
# ---------------------------------------------------------------------------


def test_detection_localization_legacy_fallback_preserved(client):
    _add_recording(client, "rec_sn", "spacenet_14")
    pipeline = LocalizationLegacyPipeline()
    assert pipeline.definition.input_compatibility == ()
    job_manager = FakeJobManager()
    service = _service(client, pipeline, job_manager=job_manager)
    run = service.create_run(
        recording_id="rec_sn", pipeline_id="loc_legacy", executor="local_cpu", parameters={}
    )
    assert run.pipeline_id == "loc_legacy"


def test_other_legacy_empty_preserves_label_space_compatibility(client):
    _add_recording(client, "rec_sn", "spacenet_14")
    _add_recording(client, "rec_sp", "signal_presence_v1")
    pipeline = ClassificationLegacyPipeline()
    assert pipeline.definition.input_compatibility == ()
    service = _service(client, pipeline)

    run = service.create_run(
        recording_id="rec_sn", pipeline_id="legacy_cls", executor="local_cpu", parameters={}
    )
    assert run.pipeline_id == "legacy_cls"

    with pytest.raises(PlatformError) as exc:
        service.create_run(
            recording_id="rec_sp", pipeline_id="legacy_cls", executor="local_cpu", parameters={}
        )
    assert exc.value.code == "INPUT_INCOMPATIBLE"


# ---------------------------------------------------------------------------
# C. AnalysisResultWriter validates the OUTPUT label space
# ---------------------------------------------------------------------------


def _label_root(tmp_path: Path) -> Path:
    root = tmp_path / "label_spaces"
    root.mkdir()
    shutil.copy(LABEL_ROOT / "spacenet_14.json", root / "spacenet_14.json")
    (root / "cpn_bandwidth_tier_v1.json").write_text(
        json.dumps(
            {
                "id": "cpn_bandwidth_tier_v1",
                "version": 1,
                "classes": [
                    {"id": 0, "name": "narrowband"},
                    {"id": 1, "name": "wideband"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _writer_definition() -> PipelineDefinition:
    """Legacy ``label_space`` projection differs from the explicit output space.

    Exercises ``resolved_output_label_space``: output validation must use the CPN
    space, never the SpaceNet input space.
    """

    return PipelineDefinition(
        id="cpn_writer",
        name="CPN Writer",
        version="1.0",
        label_space="spacenet_14",
        output_label_space="cpn_bandwidth_tier_v1",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        input_compatibility=("spacenet_14",),
    )


def _writer(session, tmp_path: Path, run_id: str):
    workspace = tmp_path / run_id
    workspace.mkdir()
    add_recording(session, recording_id="rec_w", name="w", label_space="spacenet_14")
    add_run(
        session,
        run_id=run_id,
        recording_id="rec_w",
        pipeline_id="cpn_writer",
        pipeline_version="1.0",
        executor="local_cpu",
        status="running",
    )
    session.commit()
    run = session.get(AnalysisRunModel, run_id)
    recording = session.get(RecordingModel, "rec_w")
    writer = AnalysisResultWriter(
        session=session,
        label_service=LabelSpaceService(_label_root(tmp_path)),
        pipeline_definition=_writer_definition(),
        workspace=workspace,
    )
    return writer, run, recording


def _detection(class_id: int, class_name: str) -> DetectionPayload:
    return DetectionPayload(
        t_start_s=0.01,
        t_end_s=0.02,
        f_low_hz=2_440_600_000.0,
        f_high_hz=2_440_700_000.0,
        class_id=class_id,
        class_name=class_name,
        confidence=0.9,
    )


def test_result_writer_accepts_class_from_output_label_space(session, tmp_path):
    writer, run, recording = _writer(session, tmp_path, "run_out_ok")
    writer.persist(
        run=run,
        recording=recording,
        output=PipelineOutput(detections=[_detection(1, "wideband")]),
    )
    assert run.status == "completed"


def test_result_writer_validates_output_label_space_not_input(session, tmp_path):
    writer, run, recording = _writer(session, tmp_path, "run_out_bad")
    # class 9 / "LoRa 250kHz" is valid in the spacenet_14 INPUT label space but
    # not in this plugin's CPN OUTPUT label space.
    with pytest.raises(PlatformError) as exc:
        writer.persist(
            run=run,
            recording=recording,
            output=PipelineOutput(detections=[_detection(9, "LoRa 250kHz")]),
        )
    assert exc.value.code == "INVALID_DETECTION"
    assert run.status == "running"


def test_result_writer_rejects_wrong_output_class_name(session, tmp_path):
    writer, run, recording = _writer(session, tmp_path, "run_out_name")
    with pytest.raises(PlatformError) as exc:
        writer.persist(
            run=run,
            recording=recording,
            output=PipelineOutput(detections=[_detection(1, "LoRa 250kHz")]),
        )
    assert exc.value.code == "INVALID_DETECTION"
    assert run.status == "running"
