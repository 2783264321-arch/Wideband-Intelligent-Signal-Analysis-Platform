import json
from pathlib import Path

import pytest

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.imported_runs.validation import validate_extracted_package
from app.imported_runs.factory import build_imported_run_models
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel


def _recording(session, *, rec_id="rec_imp", label_space="spacenet_14"):
    recording = RecordingModel(
        id=rec_id, name="demo", data_path="recordings/rec_imp/raw.iq", data_format="complex64_le",
        source="custom", sample_rate_hz=1_000_000.0, center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0, frequency_high_hz=2_441_500_000.0,
        num_samples=4096, duration_s=0.004096, label_space=label_space, has_ground_truth=True,
    )
    session.add(recording)
    return recording


def _write_package(root: Path, detections: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "pipeline": {"id": "dummy", "name": "Dummy Pipeline", "version": "1.0"},
        "label_space": "spacenet_14",
        "recording": {"name": "demo", "dataset": "SpaceNet"},
        "execution": {"executor": "remote_gpu", "device": "RTX 4090", "environment": "AutoDL"},
        "results": {"detections": "detections.json"},
    }), encoding="utf-8")
    (root / "detections.json").write_text(json.dumps({"detections": detections}), encoding="utf-8")
    return root


def _valid_detection(**overrides):
    value = {
        "id": "det_001",
        "t_start_s": 0.0005, "t_end_s": 0.0035,
        "f_low_hz": 2441060000.0, "f_high_hz": 2441100000.0,
        "class_id": 6, "class_name": "BLE LE1M", "confidence": 0.94,
    }
    value.update(overrides)
    return value


def test_validate_extracted_package_does_not_write(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection()])
    validated = validate_extracted_package(root, recording, labels)
    assert validated.manifest.pipeline.id == "dummy"
    assert len(validated.detections) == 1
    assert session.query(AnalysisRunModel).count() == 0
    assert session.query(DetectionResultModel).count() == 0


def test_validate_duplicate_source_detection_ids_rejected(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [
        _valid_detection(),
        _valid_detection(t_start_s=0.0006, t_end_s=0.0036, f_low_hz=2441060000.0, f_high_hz=2441100000.0),
    ])
    from app.core.errors import PlatformError
    with pytest.raises(PlatformError) as exc:
        validate_extracted_package(root, recording, labels)
    assert exc.value.code == "INVALID_IMPORT_PACKAGE"


def test_validate_invalid_bbox_rejected(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection(t_end_s=0.009)])
    from app.core.errors import PlatformError
    with pytest.raises(PlatformError) as exc:
        validate_extracted_package(root, recording, labels)
    assert exc.value.code == "INVALID_IMPORT_PACKAGE"


def test_validate_invalid_label_rejected(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection(class_id=99, class_name="Nope")])
    from app.core.errors import PlatformError
    with pytest.raises(PlatformError) as exc:
        validate_extracted_package(root, recording, labels)
    assert exc.value.code == "INVALID_IMPORT_PACKAGE"


def test_validate_invalid_confidence_rejected(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection(confidence=1.5)])
    from app.core.errors import PlatformError
    with pytest.raises(PlatformError) as exc:
        validate_extracted_package(root, recording, labels)
    assert exc.value.code == "INVALID_IMPORT_PACKAGE"


def test_build_imported_run_models_creates_standard_run(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection()])
    validated = validate_extracted_package(root, recording, labels)
    built = build_imported_run_models(
        recording, validated, run_id="run_new", detection_ids=["det_new_1"])
    assert built.run.executor == "imported"
    assert built.run.status == "completed"
    assert built.run.pipeline_id == "dummy"
    assert built.run.parameters_json["package"]["pipeline_id"] == "dummy"
    assert built.run.parameters_json["detection_count"] == 1
    assert built.run.parameters_json["source_detection_ids"] == {"det_001": "det_new_1"}
    assert "batch_import" not in built.run.parameters_json
    assert len(built.detections) == 1
    assert built.detections[0].run_id == "run_new"
    assert session.query(AnalysisRunModel).count() == 0
    assert session.query(DetectionResultModel).count() == 0


def test_build_imported_run_models_adds_batch_import_when_provided(session, tmp_path, settings):
    labels = LabelSpaceService(settings.label_space_root)
    recording = _recording(session)
    session.commit()
    root = _write_package(tmp_path, [_valid_detection()])
    validated = validate_extracted_package(root, recording, labels)
    built = build_imported_run_models(
        recording, validated, run_id="run_new", detection_ids=["det_new_1"],
        batch_import={"schema_version": 1, "batch_id": "b1", "item_key": "000000"},
    )
    assert built.run.parameters_json["batch_import"] == {
        "schema_version": 1, "batch_id": "b1", "item_key": "000000"}