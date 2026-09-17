"""P1 Batch B: standalone IQ registration by local absolute path (no copy)."""
from pathlib import Path

import numpy as np

from app.recordings.model import RecordingModel


def _write_iq(path: Path, *, fmt: str, samples: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "complex64_le":
        np.arange(samples, dtype=np.float32).astype("<c8").tofile(path)
    else:
        np.zeros(samples * 2, dtype="<f2").tofile(path)
    return path


def _register(client, path: Path, *, fmt="complex64_le", name="local-sample", fs=1_000_000.0, fc=2_441_000_000.0):
    return client.post("/api/recordings/register-path", json={
        "path": str(path), "name": name, "data_format": fmt,
        "sample_rate_hz": fs, "center_frequency_hz": fc,
    })


def test_register_complex64_path_without_copy(client, tmp_path):
    iq = _write_iq(tmp_path / "local" / "unknown.iq", fmt="complex64_le", samples=8)
    data_root = client.app.state.settings.data_root

    response = _register(client, iq)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "local-sample"
    assert body["data_format"] == "complex64_le"
    assert body["num_samples"] == 8
    assert body["duration_s"] == 8 / 1_000_000.0
    assert body["frequency_low_hz"] == 2_441_000_000.0 - 500_000.0
    assert body["frequency_high_hz"] == 2_441_000_000.0 + 500_000.0
    assert body["source"] == "custom"
    assert body["has_ground_truth"] is False
    assert body["external_path"] == str(iq.resolve())
    assert body["dataset_id"] is None
    assert body["sample_key"] is None

    # No copy into WISA managed storage.
    assert not (data_root / "recordings" / body["id"]).exists()
    assert iq.exists()


def test_register_float16_interleaved_path(client, tmp_path):
    iq = _write_iq(tmp_path / "local" / "sn.bin", fmt="float16_interleaved_le", samples=12)
    response = _register(client, iq, fmt="float16_interleaved_le")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data_format"] == "float16_interleaved_le"
    assert body["num_samples"] == 12
    assert body["duration_s"] == 12 / 1_000_000.0


def test_missing_path_rejected(client, tmp_path):
    response = _register(client, tmp_path / "nope.iq")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RECORDING_PATH_NOT_FOUND"


def test_relative_path_rejected(client, tmp_path):
    response = client.post("/api/recordings/register-path", json={
        "path": "relative/unknown.iq", "name": "x", "data_format": "complex64_le",
        "sample_rate_hz": 1.0, "center_frequency_hz": 0.0,
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RECORDING"


def test_unsupported_format_rejected(client, tmp_path):
    iq = _write_iq(tmp_path / "local" / "x.bin", fmt="complex64_le", samples=4)
    response = _register(client, iq, fmt="int8_raw")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RECORDING"


def test_invalid_byte_length_rejected(client, tmp_path):
    path = tmp_path / "local" / "bad.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00")  # 3 bytes: not a multiple of 8 or 4
    response = _register(client, path)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RECORDING"


def test_same_standalone_path_rejected_as_already_registered(client, tmp_path):
    iq = _write_iq(tmp_path / "local" / "dup.iq", fmt="complex64_le", samples=4)
    assert _register(client, iq).status_code == 201
    second = _register(client, iq)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ALREADY_REGISTERED"


def test_dataset_member_path_collision_rejected(client, session, tmp_path):
    iq = _write_iq(tmp_path / "local" / "member.bin", fmt="complex64_le", samples=4)
    session.add(RecordingModel(
        id="rec_member_path", name="member", data_path=str(iq.resolve()),
        data_format="complex64_le", source="spacenet", external_path=str(iq.resolve()),
        sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5, frequency_high_hz=2.5,
        num_samples=4, duration_s=4.0, dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", has_ground_truth=False, dataset_id="ds_member", sample_key="member",
    ))
    session.commit()

    response = _register(client, iq)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_PATH_IS_DATASET_MEMBER"
