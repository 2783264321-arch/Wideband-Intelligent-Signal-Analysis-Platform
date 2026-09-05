import json
from pathlib import Path
import time

import numpy as np
import pytest


def _write_burst_sample(root: Path, *, split: str = "test", stem: str = "0") -> None:
    fs = 30_000_000.0
    total = 200_000
    rng = np.random.default_rng(5)
    iq = (rng.standard_normal(total) + 1j * rng.standard_normal(total)).astype(np.complex64)
    a, b = 40_000, 140_000
    n = np.arange(b - a)
    iq[a:b] += 2.0 * np.exp(2j * np.pi * 5_000_000.0 / fs * n).astype(np.complex64)

    split_root = root / split
    split_root.mkdir(parents=True, exist_ok=True)
    interleaved = np.empty(2 * total, dtype="<f2")
    interleaved[0::2] = iq.real.astype(np.float16)
    interleaved[1::2] = iq.imag.astype(np.float16)
    interleaved.tofile(split_root / f"{stem}.bin")

    start_time_ms = a / fs * 1000.0
    end_time_ms = b / fs * 1000.0
    metadata = {
        "observation_range": [2401.0, 2431.0],
        "signals": [{
            "signal_id": 0,
            "start_frequency": 2420.9,
            "end_frequency": 2421.1,
            "start_time": start_time_ms,
            "end_time": end_time_ms,
            "class": 9,
        }],
    }
    (split_root / f"{stem}.json").write_text(json.dumps(metadata), encoding="utf-8")


def _register(client, tmp_path: Path):
    response = client.post("/api/datasets/spacenet/register", json={"dataset_path": str(tmp_path), "split": "test"})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["created"] == 1, summary
    return client.get("/api/recordings").json()["items"][0]


def test_registered_spacenet_recording_renders_stft(client, tmp_path):
    _write_burst_sample(tmp_path)
    recording = _register(client, tmp_path)
    response = client.get(f"/api/recordings/{recording['id']}/spectrogram?representation=stft")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["representation"] == "stft"
    assert payload["t_start_s"] == 0.0
    assert payload["t_end_s"] == pytest.approx(recording["duration_s"])
    assert payload["f_low_hz"] == recording["frequency_low_hz"]
    assert payload["f_high_hz"] == recording["frequency_high_hz"]


def test_registered_spacenet_recording_runs_stft_energy_detector(client, tmp_path):
    _write_burst_sample(tmp_path)
    recording = _register(client, tmp_path)

    run_response = client.post(
        "/api/analysis-runs",
        json={
            "recording_id": recording["id"],
            "pipeline_id": "stft_energy_detector",
            "executor": "local_cpu",
            "parameters": {},
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    assert run["pipeline_id"] == "stft_energy_detector"

    deadline = time.time() + 25
    while time.time() < deadline:
        current = client.get(f"/api/analysis-runs/{run['id']}").json()
        if current["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.1)
    assert current["status"] == "completed", current

    detections = client.get(f"/api/analysis-runs/{run['id']}/detections")
    assert detections.status_code == 200
    items = detections.json()
    assert items, "detector should find the injected burst in a SpaceNet recording"
    for item in items:
        assert item["recording_id"] == recording["id"]
        assert item["class_id"] == 0
        assert item["class_name"] == "Signal"
        assert 0.0 <= item["confidence"] <= 1.0
        assert recording["frequency_low_hz"] <= item["f_low_hz"] < item["f_high_hz"] <= recording["frequency_high_hz"]
        assert 0.0 <= item["t_start_s"] < item["t_end_s"] <= recording["duration_s"]

    gt = client.get(f"/api/recordings/{recording['id']}/ground-truth").json()
    assert len(gt) == 1
    best = max(items, key=lambda item: item["confidence"])
    assert best["t_start_s"] < gt[0]["t_end_s"]
    assert best["t_end_s"] > gt[0]["t_start_s"]
    assert best["f_low_hz"] < gt[0]["f_high_hz"]
    assert best["f_high_hz"] > gt[0]["f_low_hz"]