import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from research.m9_legacy_bridge.batch_cli import main


def _write_spacebin(path: Path, num_samples: int) -> None:
    interleaved = np.zeros(num_samples * 2, dtype="<f2")
    interleaved.tofile(path)


def _build_dataset(tmp_path: Path):
    split = tmp_path / "advanced" / "test"
    split.mkdir(parents=True, exist_ok=True)
    for name in ("a", "b"):
        _write_spacebin(split / f"{name}.bin", 6000)
        (split / f"{name}.json").write_text(json.dumps({
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0,
                "start_frequency": 2417.0, "end_frequency": 2417.1,
                "start_time": 0.02, "end_time": 0.1, "class": 9,
            }],
        }), encoding="utf-8")
    return split


def _build_assets(tmp_path: Path):
    predictions = tmp_path / "preds.jsonl"
    with predictions.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "sample_id": "a", "t0_s": 2e-5, "t1_s": 1e-4,
            "f0_hz": 2417.0e6, "f1_hz": 2417.1e6, "class_id": 9, "score": 0.9,
        }) + "\n")
        handle.write(json.dumps({
            "sample_id": "a", "t0_s": 3e-5, "t1_s": 9e-5,
            "f0_hz": 2417.2e6, "f1_hz": 2417.3e6, "class_id": 9, "score": 0.8,
        }) + "\n")
    test_manifest = tmp_path / "test_manifest.json"
    test_manifest.write_text(json.dumps({"test_ids": ["a", "b"]}), encoding="utf-8")
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({
        "images": 2, "canonical_ground_truth": 2, "predictions": 2,
        "map_50": 0.5, "map_50_95": 0.4,
    }), encoding="utf-8")
    for name in ("detector.pt", "frn.pt", "config.yaml"):
        (tmp_path / name).write_bytes(b"dummy-content-" + name.encode())
    label_space = tmp_path / "spacenet_14.json"
    label_space.write_text(json.dumps({"id": "spacenet_14", "version": 1, "classes": [
        {"id": 9, "name": "LoRa 250kHz"},
    ]}), encoding="utf-8")
    return predictions, test_manifest, metrics, label_space


def _argv(tmp_path, *, output="out.zip", extra=None):
    predictions, test_manifest, metrics, label_space = _build_assets(tmp_path)
    _build_dataset(tmp_path)
    argv = [
        "--predictions", str(predictions),
        "--test-manifest", str(test_manifest),
        "--dataset-dir", str(tmp_path / "advanced" / "test"),
        "--metrics", str(metrics),
        "--detector-checkpoint", str(tmp_path / "detector.pt"),
        "--frn-checkpoint", str(tmp_path / "frn.pt"),
        "--config", str(tmp_path / "config.yaml"),
        "--label-space", str(label_space),
        "--output", str(tmp_path / output),
    ]
    if extra:
        argv.extend(extra)
    return argv


def _summary(capsys):
    out = capsys.readouterr().out
    return json.loads(out)


def test_complete_split_export(tmp_path, capsys):
    exit_code = main(_argv(tmp_path))
    assert exit_code == 0
    summary = _summary(capsys)
    assert summary["expected_samples"] == 2
    assert summary["exported_items"] == 2
    assert summary["source_prediction_rows"] == 2
    assert summary["zero_detection_items"] == 1
    assert summary["unexpected_sample_ids"] == 0
    assert summary["missing_dataset_samples"] == 0
    assert len(summary["recording_manifest_hash"]) == 64
    assert len(summary["batch_import_fingerprint"]) == 64
    assert len(summary["archive_sha256"]) == 64
    assert Path(tmp_path / "out.zip").is_file()


def test_transport_provenance_is_populated(tmp_path, capsys, monkeypatch):
    from datetime import datetime, timezone
    import research.m9_legacy_bridge.batch_cli as cli
    monkeypatch.setattr(cli, "_get_platform_repo_commit", lambda: "abc123")
    exit_code = main(_argv(tmp_path))
    assert exit_code == 0
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        outer = json.loads(archive.read("batch_manifest.json").decode("utf-8"))
    transport = outer["transport_provenance"]
    assert transport["exporter_version"] == "batch_analysis_package_v1"
    assert transport["platform_repo_commit"] == "abc123"
    stamp = transport["export_timestamp"]
    assert stamp is not None
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


def test_unexpected_sample_id_rejected(tmp_path, capsys):
    argv = _argv(tmp_path)
    predictions = Path(argv[1])
    with predictions.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "sample_id": "zzz", "t0_s": 2e-5, "t1_s": 1e-4,
            "f0_hz": 2417.0e6, "f1_hz": 2417.1e6, "class_id": 9, "score": 0.9,
        }) + "\n")
    assert main(argv) != 0


def test_missing_dataset_sample_rejected(tmp_path, capsys):
    argv = _argv(tmp_path)
    test_manifest = Path(argv[3])
    test_manifest.write_text(json.dumps({"test_ids": ["a", "b", "missing"]}), encoding="utf-8")
    assert main(argv) != 0


def test_zero_detection_item_retained(tmp_path, capsys):
    exit_code = main(_argv(tmp_path))
    assert exit_code == 0
    summary = _summary(capsys)
    assert summary["zero_detection_items"] == 1
    import zipfile
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        b_dets = json.loads(archive.read("items/000001/detections.json").decode("utf-8"))
        assert b_dets == {"detections": []}


def test_expected_hash_mismatch_rejected(tmp_path, capsys):
    argv = _argv(tmp_path, extra=["--expected-predictions-sha256", "0" * 64])
    assert main(argv) != 0
    assert not (tmp_path / "out.zip").exists()


def test_non_test_dataset_directory_is_rejected(tmp_path, capsys):
    predictions, test_manifest, metrics, label_space = _build_assets(tmp_path)
    _build_dataset(tmp_path)
    train_dir = tmp_path / "advanced" / "train"
    train_dir.mkdir(parents=True, exist_ok=True)
    for name in ("a", "b"):
        (train_dir / f"{name}.bin").write_bytes(bytes(24000))
        (train_dir / f"{name}.json").write_text(json.dumps({
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0, "start_frequency": 2417.0, "end_frequency": 2417.1,
                "start_time": 0.02, "end_time": 0.1, "class": 9,
            }],
        }), encoding="utf-8")
    argv = [
        "--predictions", str(predictions),
        "--test-manifest", str(test_manifest),
        "--dataset-dir", str(train_dir),
        "--metrics", str(metrics),
        "--detector-checkpoint", str(tmp_path / "detector.pt"),
        "--frn-checkpoint", str(tmp_path / "frn.pt"),
        "--config", str(tmp_path / "config.yaml"),
        "--label-space", str(label_space),
        "--output", str(tmp_path / "train.zip"),
    ]
    assert main(argv) != 0
    assert not (tmp_path / "train.zip").exists()
