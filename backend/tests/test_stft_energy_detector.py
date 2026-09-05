from pathlib import Path

import numpy as np

from app.dsp.stft import compute_stft
from app.labels.service import LabelSpaceService
from app.pipelines.registry import create_pipeline_registry


def _noise_iq(num_samples: int = 32768, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(num_samples) + 1j * rng.standard_normal(num_samples)).astype(np.complex64)


def _burst_tone_iq(num_samples: int, sample_rate_hz: float, offset_hz: float,
                   start_s: float, end_s: float, amplitude: float = 1.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    iq = (rng.standard_normal(num_samples) + 1j * rng.standard_normal(num_samples)).astype(np.complex64)
    n_start = int(start_s * sample_rate_hz)
    n_end = int(end_s * sample_rate_hz)
    n = np.arange(n_end - n_start)
    tone = amplitude * np.exp(2j * np.pi * offset_hz / sample_rate_hz * n)
    iq[n_start:n_end] = iq[n_start:n_end] + tone.astype(np.complex64)
    return iq


def test_detector_returns_no_regions_for_pure_noise():
    from app.pipelines.stft_energy.detector import detect_stft_energy

    iq = _noise_iq()
    regions = detect_stft_energy(iq, sample_rate_hz=1_000_000.0, center_frequency_hz=2_441_000_000.0)
    assert regions == []


def test_detector_finds_single_burst_tone_within_stft_resolution():
    from app.pipelines.stft_energy.detector import detect_stft_energy

    sample_rate_hz = 1_000_000.0
    center_frequency_hz = 2_441_000_000.0
    offset_hz = 100_000.0
    start_s, end_s = 0.05, 0.12
    iq = _burst_tone_iq(200_000, sample_rate_hz, offset_hz, start_s, end_s, amplitude=2.0)

    regions = detect_stft_energy(iq, sample_rate_hz=sample_rate_hz, center_frequency_hz=center_frequency_hz)
    assert len(regions) >= 1
    region = max(regions, key=lambda r: r.confidence)
    assert region.t_start_s <= start_s + 0.002
    assert region.t_end_s >= end_s - 0.002
    assert region.f_low_hz <= center_frequency_hz + offset_hz + 5_000.0
    assert region.f_high_hz >= center_frequency_hz + offset_hz - 5_000.0


def test_detector_separates_two_burst_tones():
    from app.pipelines.stft_energy.detector import detect_stft_energy

    sample_rate_hz = 2_000_000.0
    center_frequency_hz = 2_441_000_000.0
    rng = np.random.default_rng(11)
    iq = (rng.standard_normal(400_000) + 1j * rng.standard_normal(400_000)).astype(np.complex64)
    n = np.arange(60_000)
    iq[40_000:100_000] += 2.0 * np.exp(2j * np.pi * 150_000.0 / sample_rate_hz * n).astype(np.complex64)
    n = np.arange(60_000)
    iq[200_000:260_000] += 2.0 * np.exp(2j * np.pi * -120_000.0 / sample_rate_hz * n).astype(np.complex64)

    regions = detect_stft_energy(iq, sample_rate_hz=sample_rate_hz, center_frequency_hz=center_frequency_hz)
    assert len(regions) == 2


def test_detector_confidences_are_finite_in_unit_interval_and_stronger_burst_is_not_lower():
    from app.pipelines.stft_energy.detector import detect_stft_energy

    sample_rate_hz = 1_000_000.0
    center_frequency_hz = 2_441_000_000.0
    weak = _burst_tone_iq(200_000, sample_rate_hz, 80_000.0, 0.04, 0.10, amplitude=1.5, seed=3)
    strong = _burst_tone_iq(200_000, sample_rate_hz, 80_000.0, 0.04, 0.10, amplitude=4.0, seed=3)

    weak_regions = detect_stft_energy(weak, sample_rate_hz=sample_rate_hz, center_frequency_hz=center_frequency_hz)
    strong_regions = detect_stft_energy(strong, sample_rate_hz=sample_rate_hz, center_frequency_hz=center_frequency_hz)
    assert all(0.0 <= r.confidence <= 1.0 for r in weak_regions)
    assert all(np.isfinite(r.confidence) for r in weak_regions)
    assert all(0.0 <= r.confidence <= 1.0 for r in strong_regions)
    if weak_regions and strong_regions:
        assert max(strong_regions, key=lambda r: r.confidence).confidence >= \
            max(weak_regions, key=lambda r: r.confidence).confidence


def test_stft_energy_pipeline_contract_emits_generic_signal_detections(tmp_path):
    from app.pipelines.base import RecordingInput
    from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline

    sample_rate_hz = 1_000_000.0
    center = 2_441_000_000.0
    iq = _burst_tone_iq(200_000, sample_rate_hz, 100_000.0, 0.04, 0.12, amplitude=2.0, seed=5)
    path = tmp_path / "burst.iq"
    iq.astype("<c8").tofile(path)
    recording = RecordingInput(
        id="rec_burst",
        data_path=path,
        data_format="complex64_le",
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center,
        frequency_low_hz=center - 500_000.0,
        frequency_high_hz=center + 500_000.0,
        duration_s=0.2,
        label_space="spacenet_14",
    )
    output = STFTEnergyDetectorPipeline().run(recording, {}, tmp_path / "workspace")
    assert output.detections
    for det in output.detections:
        assert det.f_high_hz > det.f_low_hz
        assert det.t_end_s > det.t_start_s
        assert recording.frequency_low_hz <= det.f_low_hz < det.f_high_hz <= recording.frequency_high_hz
        assert 0.0 <= det.t_start_s < det.t_end_s <= recording.duration_s
        assert det.class_id == 0
        assert det.class_name == "Signal"
        assert 0.0 <= det.confidence <= 1.0
        assert det.scores is not None and "detection" in det.scores and "classification" not in det.scores
    assert output.run_metadata["task_capability"] == "detection_localization"


def test_stft_energy_detector_runs_through_subprocess_and_persists_results(client):
    import time

    sample_rate_hz = 1_000_000.0
    center = 2_441_000_000.0
    iq = _burst_tone_iq(200_000, sample_rate_hz, 100_000.0, 0.04, 0.12, amplitude=3.0, seed=9)
    iq_path = Path(__file__).with_name(".burst_iq.bin")
    iq.astype("<c8").tofile(iq_path)
    with iq_path.open("rb") as handle:
        response = client.post(
            "/api/recordings",
            data={
                "name": "burst-demo",
                "sample_rate_hz": str(sample_rate_hz),
                "center_frequency_hz": str(center),
                "data_format": "complex64_le",
                "label_space": "spacenet_14",
            },
            files={"file": ("burst.bin", handle, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text
    recording = response.json()

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

    deadline = time.time() + 20
    while time.time() < deadline:
        current = client.get(f"/api/analysis-runs/{run['id']}").json()
        if current["status"] in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.1)
    assert current["status"] == "completed", current

    detections = client.get(f"/api/analysis-runs/{run['id']}/detections")
    assert detections.status_code == 200
    items = detections.json()
    assert items, "detector should find the injected burst"
    assert items[0]["recording_id"] == recording["id"]
    assert items[0]["class_id"] == 0
    assert items[0]["class_name"] == "Signal"
    assert 0.0 <= items[0]["confidence"] <= 1.0


def test_stft_energy_detector_is_registered_with_detection_only_metadata():
    definition = create_pipeline_registry().get("stft_energy_detector").definition
    assert definition.id == "stft_energy_detector"
    assert definition.name == "STFT Energy Detector"
    assert definition.cpu_supported is True
    assert definition.task_capability == "detection_localization"
    assert definition.label_space == "signal_presence_v1"


def test_signal_presence_label_space_is_a_single_generic_class(settings):
    labels = LabelSpaceService(settings.label_space_root).get("signal_presence_v1")
    assert len(labels.classes) == 1
    assert labels.classes[0].id == 0
    assert labels.classes[0].name == "Signal"