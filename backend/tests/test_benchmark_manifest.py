import pytest

from app.benchmarks.manifest import (
    FrozenRecordingManifest,
    ManifestGroundTruth,
    ManifestRecording,
    build_recording_manifest,
)


def _gt(t0, t1, f0, f1, class_id, class_name):
    return ManifestGroundTruth(t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1, class_id=class_id, class_name=class_name)


def _recording(recording_id, name, *ground_truth):
    return ManifestRecording(
        recording_id=recording_id,
        name=name,
        data_format="complex64_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0,
        frequency_high_hz=2_441_500_000.0,
        num_samples=100000,
        duration_s=0.1,
        ground_truth=tuple(ground_truth),
    )


def test_manifest_hash_is_independent_of_local_ids_paths_and_input_order():
    gt_a = _gt(0.01, 0.02, 2_440_600_000.0, 2_440_700_000.0, 9, "LoRa 250kHz")
    gt_b = _gt(0.03, 0.04, 2_440_800_000.0, 2_440_900_000.0, 6, "BLE LE1M")
    manifest_one = build_recording_manifest(
        "SpaceNet", "test", "spacenet_14",
        [_recording("rec_local_1", "sample_a", gt_a), _recording("rec_local_2", "sample_b", gt_b)],
    )
    manifest_two = build_recording_manifest(
        "SpaceNet", "test", "spacenet_14",
        [_recording("rec_other_2", "sample_b", gt_b), _recording("rec_other_1", "sample_a", gt_a)],
    )
    assert manifest_one.sha256 == manifest_two.sha256
    assert [e.name for e in manifest_one.entries] == ["sample_a", "sample_b"]


def test_gt_annotation_change_changes_manifest_hash():
    base = _gt(0.01, 0.02, 2_440_600_000.0, 2_440_700_000.0, 9, "LoRa 250kHz")
    changed = _gt(0.01, 0.02, 2_440_600_001.0, 2_440_700_000.0, 9, "LoRa 250kHz")  # +1 Hz
    manifest_one = build_recording_manifest("SpaceNet", "test", "spacenet_14", [_recording("r1", "sample", base)])
    manifest_two = build_recording_manifest("SpaceNet", "test", "spacenet_14", [_recording("r2", "sample", changed)])
    assert manifest_one.sha256 != manifest_two.sha256


def test_duplicate_recording_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate Recording.name"):
        build_recording_manifest(
            "SpaceNet", "test", "spacenet_14",
            [_recording("r1", "sample"), _recording("r2", "sample")],
        )


def test_manifest_order_is_deterministic_by_recording_name():
    manifest = build_recording_manifest(
        "SpaceNet", "test", "spacenet_14",
        [_recording("r2", "zeta"), _recording("r1", "alpha"), _recording("r0", "beta")],
    )
    assert [e.name for e in manifest.entries] == ["alpha", "beta", "zeta"]
    assert [e.recording_id for e in manifest.entries] == ["r1", "r0", "r2"]


def test_numeric_canonicalization_treats_equivalent_float_values_stably():
    a = _recording("r1", "sample", _gt(0.01, 0.02, 2_440_600_000.0, 2_440_700_000.0, 9, "LoRa 250kHz"))
    b = _recording("r2", "sample", _gt(0.01, 0.02, 2_440_600_000.0 + 1e-9, 2_440_700_000.0, 9, "LoRa 250kHz"))
    m1 = build_recording_manifest("SpaceNet", "test", "spacenet_14", [a])
    m2 = build_recording_manifest("SpaceNet", "test", "spacenet_14", [b])
    assert m1.sha256 == m2.sha256


def test_manifest_carries_dataset_identity_and_gt():
    gt = _gt(0.01, 0.02, 2_440_600_000.0, 2_440_700_000.0, 9, "LoRa 250kHz")
    manifest = build_recording_manifest("SpaceNet", "test", "spacenet_14", [_recording("r1", "sample", gt)])
    assert isinstance(manifest, FrozenRecordingManifest)
    assert manifest.dataset_name == "SpaceNet"
    assert manifest.dataset_split == "test"
    assert manifest.label_space == "spacenet_14"
    assert len(manifest.entries[0].ground_truth) == 1
    assert manifest.entries[0].ground_truth[0].class_name == "LoRa 250kHz"