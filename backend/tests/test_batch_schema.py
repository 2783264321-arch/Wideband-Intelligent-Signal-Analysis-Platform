import pytest
from pydantic import ValidationError

from app.imported_runs.batch_schema import BatchManifest, BatchImportSummary


def valid_manifest(**overrides):
    value = {
        "schema_version": 1,
        "batch_id": "batch_test",
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": "spacenet_14",
        "dataset": {"name": "SpaceNet", "split": "test"},
        "expected_items": 2,
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "result_provenance": {"config_sha256": "a" * 64},
        "transport_provenance": {"exporter_version": "batch_analysis_package_v1"},
        "recording_manifest_hash": "b" * 64,
        "historical_reference": {"reference_only": True, "report_sha256": "c" * 64},
        "items": [
            {
                "key": "000000",
                "package_path": "items/000000",
                "recording": {
                    "name": "0",
                    "fingerprint": {
                        "schema": "recording_fingerprint_v1",
                        "metadata": {
                            "data_format": "float16_interleaved_le",
                            "sample_rate_hz": 50000000.0,
                            "center_frequency_hz": 2455000000.0,
                            "frequency_low_hz": 2430000000.0,
                            "frequency_high_hz": 2480000000.0,
                            "num_samples": 7500000,
                            "duration_s": 0.15,
                        },
                        "ground_truth_sha256": "d" * 64,
                        "sha256": "e" * 64,
                    },
                },
            },
            {
                "key": "000001",
                "package_path": "items/000001",
                "recording": {
                    "name": "1",
                    "fingerprint": {
                        "schema": "recording_fingerprint_v1",
                        "metadata": {
                            "data_format": "float16_interleaved_le",
                            "sample_rate_hz": 50000000.0,
                            "center_frequency_hz": 2455000000.0,
                            "frequency_low_hz": 2430000000.0,
                            "frequency_high_hz": 2480000000.0,
                            "num_samples": 7500000,
                            "duration_s": 0.15,
                        },
                        "ground_truth_sha256": "f" * 64,
                        "sha256": "a0" * 32,
                    },
                },
            },
        ],
    }
    value.update(overrides)
    return value


def test_batch_manifest_accepts_v1_fixture():
    manifest = BatchManifest.model_validate(valid_manifest())
    assert manifest.schema_version == 1
    assert manifest.expected_items == 2
    assert manifest.items[1].recording.fingerprint.schema == "recording_fingerprint_v1"


def test_batch_manifest_rejects_unknown_fields():
    payload = valid_manifest()
    payload["surprise"] = True
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_more_than_10000_items():
    payload = valid_manifest()
    payload["expected_items"] = 10001
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_invalid_sha256():
    payload = valid_manifest()
    payload["recording_manifest_hash"] = "not-a-hash"
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_non_finite_numbers():
    payload = valid_manifest()
    payload["items"][0]["recording"]["fingerprint"]["metadata"]["sample_rate_hz"] = float("inf")
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_schema_version_not_one():
    payload = valid_manifest(schema_version=2)
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_reference_only_not_true():
    payload = valid_manifest()
    payload["historical_reference"]["reference_only"] = False
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_negative_counts_in_summary():
    with pytest.raises(ValidationError):
        BatchImportSummary.model_validate({
            "batch_id": "b", "import_fingerprint": "a" * 64, "archive_sha256": "b" * 64,
            "dataset_name": "SpaceNet", "dataset_split": "test", "pipeline_id": "p",
            "pipeline_version": "1.0", "label_space": "spacenet_14",
            "item_count": -1, "detection_count": 0, "already_imported": False,
            "created_runs": 0, "existing_runs": 0, "created_detections": 0,
            "matched_recordings": 0, "missing_recordings": 0, "ambiguous_recordings": 0,
            "fingerprint_mismatches": 0, "recording_run_mapping": [],
        })