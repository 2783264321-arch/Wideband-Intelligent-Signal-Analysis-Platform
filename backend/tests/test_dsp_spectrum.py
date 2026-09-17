"""P2 spectrum preview: averaged PSD, absolute RF axis, bounded IO, formats."""
from pathlib import Path

import numpy as np

from app.recordings.model import RecordingModel

FS = 1_000_000.0
FC = 2_441_000_000.0
TONE_HZ = 100_000.0


def _write_complex64_tone(path: Path, *, samples: int, tone_hz: float, dc: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = np.arange(samples, dtype=np.float64)
    iq = np.ones(samples, dtype=np.complex128) if dc else np.exp(2j * np.pi * tone_hz * n / FS)
    iq.astype("<c8").tofile(path)
    return path


def _write_float16_tone(path: Path, *, samples: int, tone_hz: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = np.arange(samples, dtype=np.float64)
    iq = np.exp(2j * np.pi * tone_hz * n / FS)
    interleaved = np.empty(samples * 2, dtype="<f2")
    interleaved[0::2] = iq.real.astype("<f2")
    interleaved[1::2] = iq.imag.astype("<f2")
    interleaved.tofile(path)
    return path


def _register(client, path: Path, fmt: str = "complex64_le") -> dict:
    response = client.post("/api/recordings/register-path", json={
        "path": str(path), "name": path.stem, "data_format": fmt,
        "sample_rate_hz": FS, "center_frequency_hz": FC,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _peak(spectrum: dict) -> tuple[float, float]:
    frequencies = np.asarray(spectrum["frequency_hz"])
    power = np.asarray(spectrum["power_db"])
    index = int(np.argmax(power))
    return float(frequencies[index]), float(power[index])


def test_complex_tone_peaks_at_absolute_rf_frequency(client, tmp_path):
    iq = _write_complex64_tone(tmp_path / "tone.iq", samples=8192, tone_hz=TONE_HZ)
    recording = _register(client, iq)

    spectrum = client.get(f"/api/recordings/{recording['id']}/spectrum?fft_size=4096&segments=4").json()
    peak_hz, _ = _peak(spectrum)
    resolution = FS / 4096
    assert abs(peak_hz - (FC + TONE_HZ)) <= 2 * resolution
    assert len(spectrum["frequency_hz"]) == 4096
    assert len(spectrum["power_db"]) == 4096
    assert spectrum["fft_size"] == 4096
    assert spectrum["segment_count"] == 4


def test_dc_tone_peaks_near_center_frequency(client, tmp_path):
    iq = _write_complex64_tone(tmp_path / "dc.iq", samples=8192, tone_hz=0.0, dc=True)
    recording = _register(client, iq)

    spectrum = client.get(f"/api/recordings/{recording['id']}/spectrum").json()
    peak_hz, _ = _peak(spectrum)
    assert abs(peak_hz - FC) <= FS / 4096


def test_short_recording_does_not_crash(client, tmp_path):
    iq = _write_complex64_tone(tmp_path / "short.iq", samples=1000, tone_hz=TONE_HZ)
    recording = _register(client, iq)

    spectrum = client.get(f"/api/recordings/{recording['id']}/spectrum?fft_size=4096").json()
    assert len(spectrum["frequency_hz"]) == 4096
    assert spectrum["segment_count"] == 1
    peak_hz, _ = _peak(spectrum)
    assert abs(peak_hz - (FC + TONE_HZ)) <= 2 * (FS / 4096)


def test_float16_interleaved_recording(client, tmp_path):
    iq = _write_float16_tone(tmp_path / "f16.bin", samples=8192, tone_hz=TONE_HZ)
    recording = _register(client, iq, fmt="float16_interleaved_le")
    assert recording["data_format"] == "float16_interleaved_le"

    spectrum = client.get(f"/api/recordings/{recording['id']}/spectrum?fft_size=4096&segments=2").json()
    peak_hz, _ = _peak(spectrum)
    assert abs(peak_hz - (FC + TONE_HZ)) <= 2 * (FS / 4096)


def test_large_logical_recording_reads_bounded_segments(client, session, monkeypatch):
    session.add(RecordingModel(
        id="rec_huge", name="huge", data_path="recordings/rec_huge/raw.iq",
        data_format="complex64_le", source="custom", external_path=None,
        sample_rate_hz=FS, center_frequency_hz=FC, frequency_low_hz=FC - FS / 2,
        frequency_high_hz=FC + FS / 2, num_samples=2_000_000_000, duration_s=2000.0,
        dataset_name=None, dataset_split=None, label_space=None, has_ground_truth=False,
    ))
    session.commit()

    requested: list[int] = []

    def fake_read_iq(recording, data_root, start_sample=0, count=None):
        count = int(count)
        requested.append(count)
        return np.zeros(count, dtype=np.complex64)

    monkeypatch.setattr("app.dsp.spectrum.read_iq", fake_read_iq)

    spectrum = client.get("/api/recordings/rec_huge/spectrum?fft_size=4096&segments=8").json()
    assert spectrum["segment_count"] == 8
    assert len(requested) <= 8
    assert all(count <= 4096 for count in requested)
    assert sum(requested) <= 8 * 4096


def test_invalid_query_bounds_rejected(client):
    assert client.get("/api/recordings/whatever/spectrum?fft_size=100").status_code == 422
    assert client.get("/api/recordings/whatever/spectrum?segments=0").status_code == 422
    assert client.get("/api/recordings/whatever/spectrum?segments=64").status_code == 422


def test_empty_recording_returns_platform_error(client, session):
    session.add(RecordingModel(
        id="rec_empty", name="empty", data_path="recordings/rec_empty/raw.iq",
        data_format="complex64_le", source="custom", external_path=None,
        sample_rate_hz=FS, center_frequency_hz=FC, frequency_low_hz=FC - FS / 2,
        frequency_high_hz=FC + FS / 2, num_samples=0, duration_s=0.0,
        dataset_name=None, dataset_split=None, label_space=None, has_ground_truth=False,
    ))
    session.commit()
    response = client.get("/api/recordings/rec_empty/spectrum")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RECORDING"
