"""P1 portable dataset identity: ordering/path independence, manifest sensitivity."""
from app.benchmarks.manifest import ManifestGroundTruth
from app.datasets.identity import (
    PortableSample,
    compute_portable_dataset_fingerprint,
    fingerprint_for_recordings,
)
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

GT = (ManifestGroundTruth(t_start_s=0.1, t_end_s=0.2, f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi"),)


def _sample(key: str, *, rate: float = 1.0, gt: tuple = GT) -> PortableSample:
    return PortableSample(
        sample_key=key, data_format="float16_interleaved_le", sample_rate_hz=rate,
        center_frequency_hz=2.0, frequency_low_hz=1.5, frequency_high_hz=2.5,
        num_samples=4, duration_s=0.4, ground_truth=gt,
    )


def _fingerprint(samples, *, name="SpaceNet", split="test", label_space="spacenet_14"):
    return compute_portable_dataset_fingerprint(
        name=name, split=split, label_space=label_space, samples=samples
    )


def test_manifest_ordering_does_not_change_fingerprint():
    a = [_sample("a"), _sample("b"), _sample("c")]
    b = [_sample("c"), _sample("a"), _sample("b")]
    assert _fingerprint(a) == _fingerprint(b)


def test_metadata_change_changes_fingerprint():
    base = [_sample("a"), _sample("b")]
    changed = [_sample("a"), _sample("b", rate=3.0)]
    assert _fingerprint(base) != _fingerprint(changed)


def test_ground_truth_change_changes_fingerprint():
    base = [_sample("a")]
    changed = [_sample("a", gt=(
        ManifestGroundTruth(t_start_s=0.1, t_end_s=0.3, f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi"),
    ))]
    assert _fingerprint(base) != _fingerprint(changed)


def test_samples_without_ground_truth_still_participate():
    base = [_sample("a", gt=()), _sample("b", gt=())]
    changed = [_sample("a", gt=()), _sample("b", gt=(), rate=2.0)]
    assert _fingerprint(base) != _fingerprint(changed)
    assert _fingerprint(base) == _fingerprint([_sample("b", gt=()), _sample("a", gt=())])


def test_label_space_none_uses_a_single_sentinel():
    assert _fingerprint([_sample("a")], label_space=None) == _fingerprint([_sample("a")], label_space=None)


def _add_recording(session, external_path: str, name: str, *, rate: float = 1.0):
    recording = RecordingModel(
        id=f"rec_{name}_{abs(hash(external_path)) % 100000}", name=name,
        data_path=external_path, data_format="float16_interleaved_le", source="spacenet",
        external_path=external_path, sample_rate_hz=rate, center_frequency_hz=2.0,
        frequency_low_hz=1.5, frequency_high_hz=2.5, num_samples=4, duration_s=0.4,
        dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
        has_ground_truth=True, sample_key=name,
    )
    session.add(recording)
    session.add(GroundTruthModel(
        id=f"gt_{recording.id}", recording_id=recording.id, t_start_s=0.1, t_end_s=0.2,
        f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi",
    ))
    return recording


def test_windows_and_posix_roots_produce_the_same_fingerprint(session):
    windows = _add_recording(session, "D:\\SpaceNet\\test\\a.bin", "a")
    posix = _add_recording(session, "/data/SpaceNet/test/a.bin", "a")
    session.commit()

    fp_windows = fingerprint_for_recordings(
        session, name="SpaceNet", split="test", label_space="spacenet_14", recordings=[windows]
    )
    fp_posix = fingerprint_for_recordings(
        session, name="SpaceNet", split="test", label_space="spacenet_14", recordings=[posix]
    )
    assert fp_windows == fp_posix
    assert len(fp_windows) == 64
