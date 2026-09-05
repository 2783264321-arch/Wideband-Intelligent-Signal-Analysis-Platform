from copy import deepcopy

from app.benchmarks.manifest import canonical_number
from app.imported_runs.batch_schema import BatchManifest
from app.imported_runs.fingerprint import CanonicalBatchItem, build_batch_import_fingerprint
from app.imported_runs.schema import PackageDetection


def _detection(**overrides):
    value = {
        "id": "det_001",
        "t_start_s": 0.0005, "t_end_s": 0.0035,
        "f_low_hz": 2441060000.0, "f_high_hz": 2441100000.0,
        "class_id": 6, "class_name": "BLE LE1M", "confidence": 0.94,
    }
    value.update(overrides)
    return PackageDetection.model_validate(value)


def _manifest(**overrides):
    base = {
        "schema_version": 1,
        "batch_id": "batch_a",
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": "spacenet_14",
        "dataset": {"name": "SpaceNet", "split": "test"},
        "expected_items": 1,
        "execution": {"executor": "historical_import"},
        "result_provenance": {"config_sha256": "a" * 64, "source_predictions_sha256": "b" * 64},
        "transport_provenance": {"exporter_version": "batch_analysis_package_v1", "export_timestamp": "2026-01-01T00:00:00Z"},
        "items": [{"key": "000000", "package_path": "items/000000", "recording": {
            "name": "0",
            "fingerprint": {"schema": "recording_fingerprint_v1",
                            "metadata": {"data_format": "f", "sample_rate_hz": 1.0, "center_frequency_hz": 2.0,
                                         "frequency_low_hz": 3.0, "frequency_high_hz": 4.0,
                                         "num_samples": 5, "duration_s": 6.0},
                            "ground_truth_sha256": "c" * 64, "sha256": "d" * 64},
        }}],
    }
    base.update(overrides)
    return BatchManifest.model_validate(base)


def _items(fingerprint="d" * 64, detections=(_detection(),)):
    return (
        CanonicalBatchItem(key="000000", recording_fingerprint=fingerprint, parameters={"p": 1}, detections=detections),
    )


def test_batch_fingerprint_ignores_transport_variation():
    first_manifest = _manifest()
    first = build_batch_import_fingerprint(first_manifest, _items())
    second_manifest = _manifest(
        batch_id="batch_b",
        transport_provenance={"exporter_version": "batch_analysis_package_v1", "export_timestamp": "2026-02-02T00:00:00Z"},
    )
    second = build_batch_import_fingerprint(second_manifest, _items())
    assert first == second


def test_batch_fingerprint_changes_on_pipeline_version():
    base = build_batch_import_fingerprint(_manifest(), _items())
    changed = _manifest()
    changed.pipeline.version = "1.1"
    assert build_batch_import_fingerprint(changed, _items()) != base


def test_batch_fingerprint_changes_on_result_provenance():
    base = build_batch_import_fingerprint(_manifest(), _items())
    changed = _manifest()
    changed.result_provenance.config_sha256 = "e" * 64
    assert build_batch_import_fingerprint(changed, _items()) != base


def test_batch_fingerprint_changes_on_recording_fingerprint():
    base = build_batch_import_fingerprint(_manifest(), _items())
    assert build_batch_import_fingerprint(_manifest(), _items(fingerprint="f" * 64)) != base


def test_batch_fingerprint_changes_on_child_parameters():
    base = build_batch_import_fingerprint(_manifest(), _items())
    items = (_items()[0] if False else None)
    import dataclasses
    item = dataclasses.replace(_items()[0], parameters={"p": 2})
    assert build_batch_import_fingerprint(_manifest(), (item,)) != base


def test_batch_fingerprint_changes_on_bbox():
    base = build_batch_import_fingerprint(_manifest(), _items())
    assert build_batch_import_fingerprint(_manifest(), _items(detections=(_detection(t_start_s=0.0006),))) != base


def test_batch_fingerprint_changes_on_class():
    base = build_batch_import_fingerprint(_manifest(), _items())
    assert build_batch_import_fingerprint(_manifest(), _items(detections=(_detection(class_id=13, class_name="FM"),))) != base


def test_batch_fingerprint_changes_on_confidence():
    base = build_batch_import_fingerprint(_manifest(), _items())
    assert build_batch_import_fingerprint(_manifest(), _items(detections=(_detection(confidence=0.5),))) != base


def test_batch_fingerprint_changes_on_score_component():
    base = build_batch_import_fingerprint(_manifest(), _items())
    scored = _detection(scores={"detection": 0.9})
    assert build_batch_import_fingerprint(_manifest(), _items(detections=(scored,))) != base


def test_canonical_number_reused_for_floats():
    assert canonical_number(0.0) == "0"
    assert canonical_number(-0.0) == "0"