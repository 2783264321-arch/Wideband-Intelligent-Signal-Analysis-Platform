import json
from pathlib import Path

import pytest

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.imported_runs.batch_validation import validate_batch
from app.labels.service import LabelSpaceService

import batch_import_fixture as fix


@pytest.fixture
def labels(settings):
    return LabelSpaceService(settings.label_space_root)


def test_validate_batch_resolves_every_recording_without_writing(session, tmp_path, labels):
    root, _ = fix.build_complete_batch(session, tmp_path)
    before = session.query(AnalysisRunModel).count()
    validated = validate_batch(root, session, labels)
    assert len(validated.items) == 2
    assert validated.total_detections == 1
    assert session.query(AnalysisRunModel).count() == before


def test_zero_local_candidate_rejected(session, tmp_path, labels):
    root, _ = fix.build_complete_batch(session, tmp_path)
    # Rename local recordings so no candidate matches the outer names.
    from app.recordings.model import RecordingModel
    for row in session.query(RecordingModel).all():
        row.name = "renamed"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "BATCH_RECORDING_NOT_FOUND"
    assert "item_key" in exc.value.details
    assert "recording_name" in exc.value.details


def test_ambiguous_local_candidate_rejected(session, tmp_path, labels):
    fix.seed_local_recordings(session)
    from app.recordings.model import RecordingModel
    session.add(RecordingModel(
        id="rec_dup", name="a", data_path="recordings/rec_dup/raw.iq", data_format="complex64_le",
        source="custom", sample_rate_hz=fix.SAMPLE_RATE_HZ, center_frequency_hz=fix.CENTER_FREQUENCY_HZ,
        frequency_low_hz=fix.FREQUENCY_LOW_HZ, frequency_high_hz=fix.FREQUENCY_HIGH_HZ,
        num_samples=fix.NUM_SAMPLES, duration_s=fix.DURATION_S,
        dataset_name=fix.DATASET_NAME, dataset_split=fix.DATASET_SPLIT, label_space=fix.LABEL_SPACE,
        has_ground_truth=True,
    ))
    session.commit()
    root = Path(tmp_path) / "batch"
    root.mkdir(parents=True)
    fingerprints = fix.local_fingerprints(session)
    for index, name in enumerate(("a", "b")):
        fix.write_child_package(root, f"{index:06d}", name=name,
                                fingerprint_sha256=fingerprints[name].sha256, detections=[])
    outer = fix.build_outer_manifest(session, fingerprints=fingerprints)
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "BATCH_RECORDING_AMBIGUOUS"


def test_recording_metadata_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["items"][0]["recording"]["fingerprint"]["metadata"]["sample_rate_hz"] = 999.0
    outer["items"][0]["recording"]["fingerprint"]["sha256"] = "1" * 64
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"
    assert "metadata_mismatches" in exc.value.details


def test_recording_ground_truth_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["items"][0]["recording"]["fingerprint"]["ground_truth_sha256"] = "f" * 64
    outer["items"][0]["recording"]["fingerprint"]["sha256"] = "2" * 64
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "RECORDING_FINGERPRINT_MISMATCH"
    assert exc.value.details.get("ground_truth_mismatch") is True


def test_dataset_manifest_hash_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path, outer_overrides={
        "recording_manifest_hash": "0" * 64,
    })
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "DATASET_MANIFEST_MISMATCH"


def test_expected_items_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["expected_items"] = 3
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_duplicate_item_key_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["items"][1]["key"] = "000000"
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_duplicate_package_path_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["items"][1]["package_path"] = "items/000000"
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_duplicate_recording_name_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    outer["items"][1]["recording"]["name"] = "a"
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_child_recording_name_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    fix.write_child_package(root, "000000", name="WRONG", fingerprint_sha256="x" * 64, detections=[])
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_child_dataset_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    fix.write_child_package(root, "000000", name="a", fingerprint_sha256="x" * 64,
                            detections=[], dataset_name="Other")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_child_pipeline_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    fix.write_child_package(root, "000000", name="a", fingerprint_sha256="x" * 64,
                            detections=[], pipeline_id="other_pipeline")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_child_pipeline_version_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    fix.write_child_package(root, "000000", name="a", fingerprint_sha256="x" * 64,
                            detections=[], pipeline_version="9.9")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_child_label_space_mismatch_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    fix.write_child_package(root, "000000", name="a", fingerprint_sha256="x" * 64,
                            detections=[], label_space="other_space")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_missing_child_manifest_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    (root / "items" / "000000" / "manifest.json").unlink()
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_missing_child_detections_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    (root / "items" / "000000" / "detections.json").unlink()
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_malformed_child_rejected(session, tmp_path, labels):
    root, outer = fix.build_complete_batch(session, tmp_path)
    (root / "items" / "000000" / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"


def test_total_detections_upper_bound_rejected(session, tmp_path, labels, monkeypatch):
    monkeypatch.setattr(fix, "MAX_TOTAL_DETECTIONS", 1)
    root, outer = fix.build_complete_batch(session, tmp_path)
    # Add a second detection to push total to 2 > bound
    fix.write_child_package(root, "000001", name="b", fingerprint_sha256="x" * 64,
                            detections=[fix.detection(), fix.detection(t_start_s=0.0006)])
    with pytest.raises(PlatformError) as exc:
        validate_batch(root, session, labels)
    assert exc.value.code == "INVALID_BATCH_IMPORT_PACKAGE"