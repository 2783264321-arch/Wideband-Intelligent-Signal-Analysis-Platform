"""P1: SpaceNet registration creates/reuses a first-class DatasetModel."""
import json
from pathlib import Path

import numpy as np


def _write_sample(root: Path, *, split: str = "test", stem: str = "0", signals=None) -> None:
    split_root = root / split
    split_root.mkdir(parents=True, exist_ok=True)
    np.asarray([1.0, 2.0, 3.0, 4.0], dtype="<f2").tofile(split_root / f"{stem}.bin")
    payload = {
        "observation_range": [2401.0, 2431.0],
        "signals": signals if signals is not None else [{
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


def test_registration_creates_dataset_and_links_members(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    _write_sample(tmp_path, stem="1", signals=[])

    response = _register(client, str(tmp_path))
    assert response.status_code == 200, response.text
    summary = response.json()
    # Existing summary fields remain; dataset_id is additive.
    assert summary["created"] == 2 and summary["skipped"] == 0 and summary["invalid"] == 0
    assert summary["total"] == 2
    assert summary["dataset_id"]

    listing = client.get("/api/datasets").json()
    assert listing["total"] == 1
    dataset = listing["items"][0]
    assert dataset["name"] == "SpaceNet"
    assert dataset["split"] == "test"
    assert dataset["adapter_id"] == "spacenet"
    assert dataset["label_space"] == "spacenet_14"
    assert dataset["sample_count"] == 2
    assert dataset["ground_truth_sample_count"] == 1
    assert dataset["portable_fingerprint"] and len(dataset["portable_fingerprint"]) == 64
    assert dataset["local_root"]

    samples = client.get(f"/api/datasets/{dataset['id']}/samples").json()
    assert samples["total"] == 2
    keys = sorted(item["sample_key"] for item in samples["items"])
    assert keys == ["0", "1"]
    assert all(item["data_format"] == "float16_interleaved_le" for item in samples["items"])

    # External IQ is not copied and GT is retained.
    assert list((client.app.state.settings.data_root / "recordings").glob("*")) == []
    recordings = client.get("/api/recordings").json()["items"]
    gt_recording = next(item for item in recordings if item["name"] == "0")
    assert gt_recording["dataset_id"] == dataset["id"]
    assert gt_recording["sample_key"] == "0"
    assert Path(gt_recording["external_path"]).name == "0.bin"
    assert Path(gt_recording["external_path"]).parent.name == "test"
    gt = client.get(f"/api/recordings/{gt_recording['id']}/ground-truth").json()
    assert len(gt) == 1 and gt[0]["class_name"] == "LoRa 250kHz"


def test_reregistration_is_idempotent(client, tmp_path):
    _write_sample(tmp_path, stem="0")
    _write_sample(tmp_path, stem="1")
    first = _register(client, str(tmp_path)).json()
    second = _register(client, str(tmp_path)).json()
    assert second["created"] == 0 and second["skipped"] == 2
    assert first["dataset_id"] == second["dataset_id"]

    listing = client.get("/api/datasets").json()
    assert listing["total"] == 1
    dataset = listing["items"][0]
    assert dataset["sample_count"] == 2
    assert dataset["ground_truth_sample_count"] == 2
    samples = client.get(f"/api/datasets/{dataset['id']}/samples").json()
    assert samples["total"] == 2


def test_different_roots_are_distinct_local_datasets_with_possibly_equal_fingerprints(client, tmp_path):
    root_a = tmp_path / "SpaceNetCopyA"
    root_b = tmp_path / "SpaceNetCopyB"
    for root in (root_a, root_b):
        _write_sample(root, stem="0")
    id_a = _register(client, str(root_a)).json()["dataset_id"]
    id_b = _register(client, str(root_b)).json()["dataset_id"]
    assert id_a != id_b

    listing = client.get("/api/datasets").json()
    assert listing["total"] == 2
    fingerprints = {item["portable_fingerprint"] for item in listing["items"]}
    assert len(fingerprints) == 1  # identical logical contents
    roots = {item["local_root"] for item in listing["items"]}
    assert len(roots) == 2
