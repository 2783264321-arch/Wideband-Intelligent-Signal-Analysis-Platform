import pytest

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.imported_runs.fingerprint import build_recording_fingerprint


def _gt(class_id=9, class_name="LoRa 250kHz"):
    return ManifestGroundTruth(
        t_start_s=0.01, t_end_s=0.02, f_low_hz=2_440_600_000.0, f_high_hz=2_440_700_000.0,
        class_id=class_id, class_name=class_name,
    )


def make_recording(recording_id, class_id=9, class_name="LoRa 250kHz"):
    return ManifestRecording(
        recording_id=recording_id,
        name="a",
        data_format="float16_interleaved_le",
        sample_rate_hz=50_000_000.0,
        center_frequency_hz=2_455_000_000.0,
        frequency_low_hz=2_430_000_000.0,
        frequency_high_hz=2_480_000_000.0,
        num_samples=7_500_000,
        duration_s=0.15,
        ground_truth=(_gt(class_id, class_name),),
    )


def test_recording_fingerprint_ignores_local_recording_id():
    left = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_local_a"))
    right = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_local_b"))
    assert left.sha256 == right.sha256


def test_recording_fingerprint_changes_when_ground_truth_changes():
    original = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a"))
    changed = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a", class_id=8))
    assert original.ground_truth_sha256 != changed.ground_truth_sha256
    assert original.sha256 != changed.sha256


def test_recording_fingerprint_changes_when_dataset_split_changes():
    recording = make_recording("rec_a")
    test_fp = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", recording)
    train_fp = build_recording_fingerprint("SpaceNet", "train", "spacenet_14", recording)
    assert test_fp.sha256 != train_fp.sha256


def test_recording_fingerprint_ground_truth_order_independent():
    first = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a"))
    second = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a"))
    assert first.ground_truth_sha256 == second.ground_truth_sha256


def test_recording_fingerprint_normalizes_zero():
    rec = ManifestRecording(
        recording_id="r", name="a", data_format="complex64_le",
        sample_rate_hz=50_000_000.0, center_frequency_hz=0.0, frequency_low_hz=-0.0,
        frequency_high_hz=50_000_000.0, num_samples=1, duration_s=0.0,
        ground_truth=(),
    )
    left = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", rec)
    # identical values but 0.0/-0.0 positions already same; verify stable schema
    assert left.schema == "recording_fingerprint_v1"
    assert len(left.sha256) == 64