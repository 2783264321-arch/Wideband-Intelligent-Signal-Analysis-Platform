"""Deterministic two-Recording Batch Analysis Package test fixture.

Builds local Recordings + GT, writes child Analysis Package v1 directories, and
constructs an outer BatchManifest whose recording fingerprints match the local
semantic content so the real import path succeeds.
"""
import json
from pathlib import Path
from uuid import uuid4

from benchmark_fixture import add_ground_truth, add_recording

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.imported_runs.batch_archive import MAX_TOTAL_DETECTIONS
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.imported_runs.schema import PackageDetection

DATASET_NAME = "SpaceNet"
DATASET_SPLIT = "test"
LABEL_SPACE = "spacenet_14"

# Tiny 1 MHz Recording around 2.441 GHz (matches benchmark_fixture defaults).
SAMPLE_RATE_HZ = 1_000_000.0
CENTER_FREQUENCY_HZ = 2_441_000_000.0
FREQUENCY_LOW_HZ = 2_440_500_000.0
FREQUENCY_HIGH_HZ = 2_441_500_000.0
NUM_SAMPLES = 100_000
DURATION_S = 0.1


def make_gt(class_id=9, class_name="LoRa 250kHz"):
    return ManifestGroundTruth(
        t_start_s=0.01, t_end_s=0.02, f_low_hz=FREQUENCY_LOW_HZ, f_high_hz=FREQUENCY_LOW_HZ + 100_000.0,
        class_id=class_id, class_name=class_name,
    )


def make_manifest_recording(recording_id, name, gt=()):
    return ManifestRecording(
        recording_id=recording_id, name=name, data_format="complex64_le",
        sample_rate_hz=SAMPLE_RATE_HZ, center_frequency_hz=CENTER_FREQUENCY_HZ,
        frequency_low_hz=FREQUENCY_LOW_HZ, frequency_high_hz=FREQUENCY_HIGH_HZ,
        num_samples=NUM_SAMPLES, duration_s=DURATION_S, ground_truth=tuple(gt),
    )


def seed_local_recordings(session, *, names=("a", "b")):
    for name in names:
        add_recording(session, recording_id=f"rec_{name}", name=name,
                      dataset_name=DATASET_NAME, dataset_split=DATASET_SPLIT, label_space=LABEL_SPACE)
        add_ground_truth(session, gt_id=f"gt_{name}", recording_id=f"rec_{name}", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=FREQUENCY_LOW_HZ, f1=FREQUENCY_LOW_HZ + 100_000.0)
    session.commit()


def local_fingerprints(session):
    """Return {recording_name: RecordingFingerprintValue} for seeded local recordings."""
    from app.recordings.model import RecordingModel
    rows = session.query(RecordingModel).filter(
        RecordingModel.dataset_name == DATASET_NAME,
        RecordingModel.dataset_split == DATASET_SPLIT,
    ).all()
    result = {}
    for row in rows:
        gt = (make_gt(),)
        manifest_recording = make_manifest_recording(row.id, row.name, gt)
        result[row.name] = build_recording_fingerprint(DATASET_NAME, DATASET_SPLIT, LABEL_SPACE, manifest_recording)
    return result


def detection(**overrides):
    value = {
        "id": "det_001",
        "t_start_s": 0.0005, "t_end_s": 0.0035,
        "f_low_hz": FREQUENCY_LOW_HZ, "f_high_hz": FREQUENCY_LOW_HZ + 100_000.0,
        "class_id": 9, "class_name": "LoRa 250kHz", "confidence": 0.94,
    }
    value.update(overrides)
    return value


def write_child_package(root: Path, item_key: str, *, name: str, fingerprint_sha256: str,
                        detections: list[dict], pipeline_id="pipeline_x", pipeline_version="1.0",
                        label_space=LABEL_SPACE, dataset_name=DATASET_NAME, params: dict | None = None):
    package_dir = root / "items" / item_key
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "pipeline": {"id": pipeline_id, "name": "Pipeline X", "version": pipeline_version},
        "label_space": label_space,
        "recording": {"name": name, "dataset": dataset_name},
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "results": {"detections": "detections.json"},
    }
    if params is not None:
        manifest["parameters"] = params
    (package_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (package_dir / "detections.json").write_text(json.dumps({"detections": detections}), encoding="utf-8")
    return package_dir


def build_outer_manifest(session, *, fingerprints=None, item_order=None, **overrides):
    fingerprints = fingerprints if fingerprints is not None else local_fingerprints(session)
    item_order = item_order if item_order is not None else ["a", "b"]
    items = []
    for index, name in enumerate(item_order):
        fp = fingerprints[name]
        items.append({
            "key": f"{index:06d}",
            "package_path": f"items/{index:06d}",
            "recording": {
                "name": name,
                "fingerprint": {
                    "schema": "recording_fingerprint_v1",
                    "metadata": {
                        "data_format": fp.metadata["data_format"],
                        "sample_rate_hz": fp.metadata["sample_rate_hz"],
                        "center_frequency_hz": fp.metadata["center_frequency_hz"],
                        "frequency_low_hz": fp.metadata["frequency_low_hz"],
                        "frequency_high_hz": fp.metadata["frequency_high_hz"],
                        "num_samples": fp.metadata["num_samples"],
                        "duration_s": fp.metadata["duration_s"],
                    },
                    "ground_truth_sha256": fp.ground_truth_sha256,
                    "sha256": fp.sha256,
                },
            },
        })
    base = {
        "schema_version": 1,
        "batch_id": f"batch_{uuid4().hex[:8]}",
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": LABEL_SPACE,
        "dataset": {"name": DATASET_NAME, "split": DATASET_SPLIT},
        "expected_items": len(items),
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "result_provenance": {"config_sha256": "a" * 64},
        "transport_provenance": {"exporter_version": "batch_analysis_package_v1"},
        "items": items,
    }
    base.update(overrides)
    return base


def build_complete_batch(session, tmp_path, *, detections=None, outer_overrides=None,
                         zero_detection=True):
    """Seed recordings, write child packages, write outer manifest. Returns (root, manifest_dict)."""
    seed_local_recordings(session)
    fingerprints = local_fingerprints(session)
    root = Path(tmp_path) / "batch"
    root.mkdir(parents=True, exist_ok=True)
    detections = detections if detections is not None else {"a": [detection()], "b": []}
    for index, name in enumerate(("a", "b")):
        write_child_package(
            root, f"{index:06d}", name=name,
            fingerprint_sha256=fingerprints[name].sha256,
            detections=detections.get(name, []),
        )
    outer = build_outer_manifest(session, fingerprints=fingerprints, **(outer_overrides or {}))
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    return root, outer