from pathlib import Path

import pytest

from benchmark_fixture import add_detection, add_recording, add_run

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.labels.service import LabelSpaceService
from app.pipelines.base import ArtifactPayload, DetectionPayload, PipelineDefinition, PipelineOutput
from app.recordings.model import RecordingModel
from app.remote_execution.validation import AnalysisResultWriter

SAMPLE_RATE_HZ = 1_000_000.0
FREQUENCY_LOW_HZ = 2_440_500_000.0
FREQUENCY_HIGH_HZ = 2_441_500_000.0


def _definition():
    return PipelineDefinition(
        id="pipeline_x",
        name="Pipeline X",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="CPU",
        cpu_supported=True,
        stages=(),
        inspectable_stages=(),
    )


def _valid_detection(class_id=9, class_name="LoRa 250kHz", confidence=0.9):
    return DetectionPayload(
        t_start_s=0.01,
        t_end_s=0.02,
        f_low_hz=2_440_600_000.0,
        f_high_hz=2_440_700_000.0,
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        scores={"classification": confidence},
    )


def _writer(session, workspace, label_service, definition=None):
    return AnalysisResultWriter(
        session=session,
        label_service=label_service,
        pipeline_definition=definition or _definition(),
        workspace=workspace,
    )


def _setup(session, *, run_status="running", workspace):
    add_recording(session, recording_id="rec_w", name="w")
    add_run(session, run_id="run_w", recording_id="rec_w",
            pipeline_id="pipeline_x", pipeline_version="1.0",
            executor="imported", status=run_status)
    add_detection(session, detection_id="det_old", run_id="run_w",
                  class_id=6, class_name="BLE LE1M", confidence=0.5,
                  t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
    session.commit()
    return session.get(AnalysisRunModel, "run_w"), session.get(RecordingModel, "rec_w")


def _count_detections(session, run_id="run_w"):
    return len(session.query(DetectionResultModel).filter(DetectionResultModel.run_id == run_id).all())


def test_completed_run_is_immutable(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="completed", workspace=workspace)
    label_service = LabelSpaceService(settings.label_space_root)
    output = PipelineOutput(detections=[_valid_detection()],
                            artifacts=[ArtifactPayload(stage_name="stage", artifact_type="binary",
                                                       scope="run", path=workspace / "stage.bin")],
                            run_metadata={"kind": "writer_test"})

    with pytest.raises(ValueError, match="completed AnalysisRun is immutable"):
        _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)

    assert _count_detections(session) == 1
    remaining = session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").all()
    assert [item.id for item in remaining] == ["det_old"]
    assert not (workspace / "run_metadata.json").exists()
    assert not (workspace / "artifacts.json").exists()


@pytest.mark.parametrize("terminal_status", ["failed", "interrupted"])
def test_terminal_run_is_immutable(session, tmp_path, settings, terminal_status):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status=terminal_status, workspace=workspace)
    label_service = LabelSpaceService(settings.label_space_root)
    output = PipelineOutput(detections=[_valid_detection()])

    with pytest.raises(ValueError):
        _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)

    assert _count_detections(session) == 1
    assert run.status == terminal_status


def test_successful_running_finalization(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="running", workspace=workspace)
    artifact_path = workspace / "stage.bin"
    artifact_path.write_bytes(b"artifact")
    label_service = LabelSpaceService(settings.label_space_root)
    output = PipelineOutput(
        detections=[_valid_detection()],
        artifacts=[ArtifactPayload(stage_name="stage", artifact_type="binary",
                                   scope="run", path=artifact_path)],
        run_metadata={"kind": "writer_test"},
    )

    _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)

    assert run.status == "completed"
    assert run.finished_at is not None

    detections = session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").all()
    assert len(detections) == 1
    new = detections[0]
    assert new.id != "det_old"
    assert new.run_id == "run_w"
    assert new.class_id == 9
    assert new.class_name == "LoRa 250kHz"
    assert new.confidence == 0.9
    assert new.scores_json == {"classification": 0.9}

    assert (workspace / "run_metadata.json").read_text(encoding="utf-8") == '{\n  "kind": "writer_test"\n}'
    artifact_index = workspace / "artifacts.json"
    assert artifact_index.exists()
    assert '"path": "stage.bin"' in artifact_index.read_text(encoding="utf-8")

    session.commit()
    session.expire_all()
    reloaded = session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").all()
    assert len(reloaded) == 1
    assert reloaded[0].class_id == 9


def test_writer_does_not_commit(session, tmp_path, settings, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="running", workspace=workspace)
    label_service = LabelSpaceService(settings.label_space_root)
    output = PipelineOutput(detections=[_valid_detection()])

    def _no_commit():
        raise AssertionError("AnalysisResultWriter must not own transaction commit")

    monkeypatch.setattr(session, "commit", _no_commit)
    _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)

    assert run.status == "completed"
    assert run.finished_at is not None
    session.rollback()


def test_invalid_physical_box_moved_into_writer(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="running", workspace=workspace)
    label_service = LabelSpaceService(settings.label_space_root)
    bad = DetectionPayload(t_start_s=-1.0, t_end_s=0.02, f_low_hz=2_440_600_000.0, f_high_hz=2_440_700_000.0,
                           class_id=9, class_name="LoRa 250kHz", confidence=0.9)
    output = PipelineOutput(detections=[bad])

    with pytest.raises(PlatformError) as exc:
        _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)
    assert exc.value.code == "INVALID_DETECTION"

    session.rollback()
    remaining = session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").all()
    assert [item.id for item in remaining] == ["det_old"]


def test_invalid_label_moved_into_writer(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="running", workspace=workspace)
    label_service = LabelSpaceService(settings.label_space_root)
    bad = DetectionPayload(t_start_s=0.01, t_end_s=0.02, f_low_hz=2_440_600_000.0, f_high_hz=2_440_700_000.0,
                           class_id=9, class_name="NOT LORA", confidence=0.9)
    output = PipelineOutput(detections=[bad])

    with pytest.raises(PlatformError) as exc:
        _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)
    assert exc.value.code == "INVALID_DETECTION"

    session.rollback()
    remaining = session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").all()
    assert [item.id for item in remaining] == ["det_old"]


def test_artifact_escape_rejected(session, tmp_path, settings):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run, recording = _setup(session, run_status="running", workspace=workspace)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    label_service = LabelSpaceService(settings.label_space_root)
    output = PipelineOutput(
        detections=[_valid_detection()],
        artifacts=[ArtifactPayload(stage_name="stage", artifact_type="binary",
                                   scope="run", path=outside)],
    )

    with pytest.raises(PlatformError) as exc:
        _writer(session, workspace, label_service).persist(run=run, recording=recording, output=output)
    assert exc.value.code == "INVALID_ARTIFACT"

    assert not (workspace / "artifacts.json").exists()
    session.rollback()
    assert _count_detections(session) == 1