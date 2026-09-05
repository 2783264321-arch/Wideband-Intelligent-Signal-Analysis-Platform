from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from pydantic import TypeAdapter

from app.imported_runs.batch_schema import BatchManifest
from app.imported_runs.fingerprint import CanonicalBatchItem, build_batch_import_fingerprint
from app.imported_runs.schema import Manifest, PackageDetection
from research.m9_legacy_bridge.batch_exporter import BatchExportItem, export_batch_package


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
        "batch_id": "batch_x",
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": "spacenet_14",
        "dataset": {"name": "SpaceNet", "split": "test"},
        "expected_items": 2,
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "result_provenance": {"config_sha256": "a" * 64},
        "transport_provenance": {"exporter_version": "batch_analysis_package_v1", "export_timestamp": "2026-01-01T00:00:00Z"},
        "items": [
            {"key": "000000", "package_path": "items/000000", "recording": {
                "name": "a", "fingerprint": {"schema": "recording_fingerprint_v1",
                                             "metadata": {"data_format": "f", "sample_rate_hz": 1.0,
                                                          "center_frequency_hz": 2.0, "frequency_low_hz": 3.0,
                                                          "frequency_high_hz": 4.0, "num_samples": 5, "duration_s": 6.0},
                                             "ground_truth_sha256": "c" * 64, "sha256": "d" * 64}}},
            {"key": "000001", "package_path": "items/000001", "recording": {
                "name": "b", "fingerprint": {"schema": "recording_fingerprint_v1",
                                             "metadata": {"data_format": "f", "sample_rate_hz": 1.0,
                                                          "center_frequency_hz": 2.0, "frequency_low_hz": 3.0,
                                                          "frequency_high_hz": 4.0, "num_samples": 5, "duration_s": 6.0},
                                             "ground_truth_sha256": "e" * 64, "sha256": "f" * 64}}},
        ],
    }
    base.update(overrides)
    return BatchManifest.model_validate(base)


def _child_manifest(name, pipeline_id="pipeline_x", pipeline_version="1.0"):
    return Manifest.model_validate({
        "schema_version": 1,
        "pipeline": {"id": pipeline_id, "name": "Pipeline X", "version": pipeline_version},
        "label_space": "spacenet_14",
        "recording": {"name": name, "dataset": "SpaceNet"},
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "results": {"detections": "detections.json"},
    })


def _items(manifest, dets_a, dets_b):
    return (
        BatchExportItem(item=manifest.items[0], child_manifest=_child_manifest("a"), detections=dets_a),
        BatchExportItem(item=manifest.items[1], child_manifest=_child_manifest("b"), detections=dets_b),
    )


def test_writer_emits_exact_members_and_empty_child(tmp_path):
    manifest = _manifest()
    output = export_batch_package(
        tmp_path / "batch.zip", manifest,
        _items(manifest, (_detection(), _detection(t_start_s=0.0006)), ()),
    )
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert names == [
            "batch_manifest.json",
            "items/000000/manifest.json",
            "items/000000/detections.json",
            "items/000001/manifest.json",
            "items/000001/detections.json",
        ]
        import json
        b_dets = json.loads(archive.read("items/000001/detections.json").decode("utf-8"))
        assert b_dets == {"detections": []}


def test_child_schema_compatible_with_existing_contract(tmp_path):
    manifest = _manifest()
    output = export_batch_package(
        tmp_path / "batch.zip", manifest,
        _items(manifest, (_detection(),), ()),
    )
    with zipfile.ZipFile(output) as archive:
        for index, key in enumerate(("000000", "000001")):
            child = Manifest.model_validate(
                __import__("json").loads(archive.read(f"items/{key}/manifest.json").decode("utf-8")))
            dets = TypeAdapter(list[PackageDetection]).validate_python(
                __import__("json").loads(archive.read(f"items/{key}/detections.json").decode("utf-8"))["detections"])
            assert child.pipeline.id == manifest.pipeline.id
            assert child.pipeline.version == manifest.pipeline.version
            assert child.label_space == manifest.label_space
            assert child.recording.name == manifest.items[index].recording.name
            assert child.recording.dataset == manifest.dataset.name
            assert child.results.detections == "detections.json"
            assert child.results.metrics is None


def test_writer_is_deterministic_and_fingerprint_transport_stable(tmp_path):
    manifest = _manifest()
    items = _items(manifest, (_detection(),), ())
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    export_batch_package(first, manifest, items)
    export_batch_package(second, manifest, items)
    assert first.read_bytes() == second.read_bytes()
    # Semantic fingerprint independent of transport timestamp.
    changed_transport = _manifest()
    changed_transport.transport_provenance.export_timestamp = "2026-02-02T00:00:00Z"
    third = tmp_path / "c.zip"
    export_batch_package(third, changed_transport, items)
    fp_a = build_batch_import_fingerprint(
        manifest,
        (CanonicalBatchItem(key=manifest.items[0].key, recording_fingerprint="d" * 64, parameters={}, detections=(_detection(),)),
         CanonicalBatchItem(key=manifest.items[1].key, recording_fingerprint="f" * 64, parameters={}, detections=())),
    )
    fp_c = build_batch_import_fingerprint(
        changed_transport,
        (CanonicalBatchItem(key=changed_transport.items[0].key, recording_fingerprint="d" * 64, parameters={}, detections=(_detection(),)),
         CanonicalBatchItem(key=changed_transport.items[1].key, recording_fingerprint="f" * 64, parameters={}, detections=())),
    )
    assert fp_a == fp_c