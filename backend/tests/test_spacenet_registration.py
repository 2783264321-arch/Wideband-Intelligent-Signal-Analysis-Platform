import json
from pathlib import Path

import numpy as np
import pytest


def _write_sample(root: Path, *, split: str = "test", stem: str = "0",
                  values: list[float] | None = None, metadata: dict | None = None) -> None:
    split_root = root / split
    split_root.mkdir(parents=True, exist_ok=True)
    np.asarray(values or [1.0, 2.0, 3.0, 4.0], dtype="<f2").tofile(split_root / f"{stem}.bin")
    payload = metadata or {
        "observation_range": [2401.0, 2431.0],
        "signals": [{
            "signal_id": 0,
            "start_frequency": 2417.97385,
            "end_frequency": 2418.02615,
            "start_time": 0.0,
            "end_time": 0.0000001,
            "class": 9,
        }],
    }
    (split_root / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")


def _register(client, dataset_path: str, split: str = "test"):
    return client.post("/api/datasets/spacenet/register", json={"dataset_path": dataset_path, "split": split})


def test_registration_does_not_read_or_copy_bin_contents(client, tmp_path, monkeypatch):
    _write_sample(tmp_path, stem="0")
    _write_sample(tmp_path, stem="1", metadata={"observation_range": [2430.0, 2480.0], "signals": []})

    def _fail(*args, **kwargs):
        raise AssertionError("registration must not read .bin sample contents")

    monkeypatch.setattr(np, "memmap", _fail)
    monkeypatch.setattr(np, "fromfile", _fail)

    response = _register(client, str(tmp_path))
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["created"] == 2
    assert summary["skipped"] == 0
    assert summary["invalid"] == 0
    assert summary["total"] == 2
    assert list((client.app.state.settings.data_root / "recordings").glob("*")) == []


def test_registration_derives_physical_metadata(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    response = _register(client, str(tmp_path))
    assert response.status_code == 200, response.text
    listing = client.get("/api/recordings").json()["items"]
    recording = next(item for item in listing if item["name"] == "0")
    assert recording["data_format"] == "float16_interleaved_le"
    assert recording["source"] == "spacenet"
    assert recording["sample_rate_hz"] == 30_000_000.0
    assert recording["center_frequency_hz"] == 2_416_000_000.0
    assert recording["frequency_low_hz"] == 2_401_000_000.0
    assert recording["frequency_high_hz"] == 2_431_000_000.0
    assert recording["num_samples"] == 2
    assert recording["duration_s"] == pytest.approx(2 / 30_000_000)
    assert recording["label_space"] == "spacenet_14"
    assert recording["has_ground_truth"] is True


def test_ground_truth_units_and_class_mapping(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    _register(client, str(tmp_path))
    listing = client.get("/api/recordings").json()["items"]
    recording = next(item for item in listing if item["name"] == "0")
    gt = client.get(f"/api/recordings/{recording['id']}/ground-truth").json()
    assert len(gt) == 1
    assert gt[0]["t_start_s"] == 0.0
    assert gt[0]["f_low_hz"] == pytest.approx(2_417_973_850.0)
    assert gt[0]["class_id"] == 9
    assert gt[0]["class_name"] == "LoRa 250kHz"


def test_boundary_truncated_ground_truth_retained(client, tmp_path):
    # start_time == 0 and end_time == duration (2 samples / 30 MHz) are legal.
    _write_sample(
        tmp_path,
        stem="0",
        metadata={
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0,
                "start_frequency": 2401.0,
                "end_frequency": 2431.0,
                "start_time": 0.0,
                "end_time": 0.0000666666667,
                "class": 2,
            }],
        },
    )
    _register(client, str(tmp_path))
    listing = client.get("/api/recordings").json()["items"]
    recording = next(item for item in listing if item["name"] == "0")
    gt = client.get(f"/api/recordings/{recording['id']}/ground-truth").json()
    assert len(gt) == 1
    assert gt[0]["t_end_s"] == pytest.approx(recording["duration_s"])


def test_orphan_bin_and_json_counted_invalid(client, tmp_path):
    split_root = tmp_path / "test"
    split_root.mkdir(parents=True)
    np.asarray([1.0, 2.0], dtype="<f2").tofile(split_root / "orphan_bin.bin")
    (split_root / "orphan_json.json").write_text(json.dumps({"observation_range": [2401.0, 2431.0], "signals": []}))
    response = _register(client, str(tmp_path))
    summary = response.json()
    assert summary["created"] == 0
    assert summary["invalid"] == 2


def test_invalid_file_size_rejected(client, tmp_path):
    split_root = tmp_path / "test"
    split_root.mkdir(parents=True)
    np.asarray([1.0, 2.0, 3.0], dtype="<f2").tofile(split_root / "0.bin")  # 3 floats, not a full I/Q pair
    (split_root / "0.json").write_text(json.dumps({"observation_range": [2401.0, 2431.0], "signals": []}))
    response = _register(client, str(tmp_path))
    summary = response.json()
    assert summary["created"] == 0
    assert summary["invalid"] == 1


def test_invalid_class_id_rejected(client, tmp_path):
    _write_sample(
        tmp_path,
        stem="0",
        metadata={
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0,
                "start_frequency": 2417.0,
                "end_frequency": 2418.0,
                "start_time": 0.0,
                "end_time": 0.0001,
                "class": 99,
            }],
        },
    )
    response = _register(client, str(tmp_path))
    summary = response.json()
    assert summary["created"] == 0
    assert summary["invalid"] == 1


def test_out_of_range_signal_rejected(client, tmp_path):
    _write_sample(
        tmp_path,
        stem="0",
        metadata={
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0,
                "start_frequency": 2399.0,
                "end_frequency": 2402.0,
                "start_time": 0.0,
                "end_time": 0.0001,
                "class": 9,
            }],
        },
    )
    response = _register(client, str(tmp_path))
    summary = response.json()
    assert summary["created"] == 0
    assert summary["invalid"] == 1


def test_duplicate_registration_is_idempotent(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    _write_sample(tmp_path, stem="1")
    first = _register(client, str(tmp_path)).json()
    assert first["created"] == 2
    second = _register(client, str(tmp_path)).json()
    assert second["created"] == 0
    assert second["skipped"] == 2
    assert second["invalid"] == 0
    listing = client.get("/api/recordings").json()["items"]
    assert len(listing) == 2


def test_registering_split_directory_directly(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    response = _register(client, str(tmp_path / "test"))
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
