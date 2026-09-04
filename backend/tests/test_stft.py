from pathlib import Path

import numpy as np

from app.dsp.iq import read_iq
from app.dsp.stft import compute_stft
from app.recordings.model import RecordingModel

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "tiny_iq_complex64.bin"


def _recording() -> RecordingModel:
    return RecordingModel(
        id="rec_stft",
        name="stft-demo",
        data_path=str(FIXTURE),
        data_format="complex64_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0,
        frequency_high_hz=2_441_500_000.0,
        num_samples=4096,
        duration_s=0.004096,
        dataset_name=None,
        dataset_split=None,
        label_space="spacenet_14",
        has_ground_truth=False,
    )


def test_compute_stft_returns_physical_frequency_axis_and_expected_peaks():
    iq = np.fromfile(FIXTURE, dtype="<c8")
    result = compute_stft(
        iq,
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        nperseg=512,
        noverlap=256,
        nfft=512,
    )

    assert result.magnitude_db.shape[0] == 512
    assert result.frequency_axis_hz[0] == 2_440_500_000.0
    assert np.isclose(result.frequency_axis_hz[-1], 2_441_498_046.875)

    average_power = result.magnitude_db.mean(axis=1)
    strongest = np.argsort(average_power)[-8:]
    strongest_hz = result.frequency_axis_hz[strongest]
    bin_hz = 1_000_000.0 / 512

    assert np.min(np.abs(strongest_hz - 2_441_080_000.0)) <= bin_hz
    assert np.min(np.abs(strongest_hz - 2_441_220_000.0)) <= bin_hz


def test_spectrogram_api_returns_cached_preview_and_physical_bounds(client):
    with FIXTURE.open("rb") as handle:
        imported = client.post(
            "/api/recordings",
            data={
                "name": "stft-api-demo",
                "sample_rate_hz": "1000000",
                "center_frequency_hz": "2441000000",
                "data_format": "complex64_le",
            },
            files={"file": ("tiny.bin", handle, "application/octet-stream")},
        ).json()

    response = client.get(f"/api/recordings/{imported['id']}/spectrogram?representation=stft")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["representation"] == "stft"
    assert payload["t_start_s"] == 0.0
    assert payload["t_end_s"] == 0.004096
    assert payload["f_low_hz"] == 2440500000.0
    assert payload["f_high_hz"] == 2441500000.0
    assert payload["image_url"].startswith("/media/spectrograms/")

    image = client.get(payload["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"

    cache_files = list((client.app.state.settings.data_root / "cache" / "spectrograms").glob("*"))
    assert any(path.suffix == ".npz" for path in cache_files)
    assert any(path.suffix == ".png" for path in cache_files)
