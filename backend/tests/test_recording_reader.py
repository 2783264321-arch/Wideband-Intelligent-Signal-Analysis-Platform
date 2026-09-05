from pathlib import Path

import numpy as np
import pytest

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel
from app.recordings.reader import read_segment, read_segment_from_path


def _complex64_file(path: Path) -> None:
    values = np.array([1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j, 9 + 10j], dtype=np.complex64)
    values.tofile(path)


def _float16_file(path: Path) -> None:
    # Interleaved [I0, Q0, I1, Q1, ...] little-endian float16.
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], dtype="<f2")
    values.tofile(path)


def _recording(path: Path, data_format: str, num_samples: int, *, external: bool = True) -> RecordingModel:
    return RecordingModel(
        id="rec_reader",
        name="reader-demo",
        data_path=str(path) if external else "recordings/rec_reader/raw.iq",
        external_path=str(path) if external else None,
        data_format=data_format,
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0,
        frequency_high_hz=2_441_500_000.0,
        num_samples=num_samples,
        duration_s=num_samples / 1_000_000.0,
        dataset_name=None,
        dataset_split=None,
        label_space="spacenet_14",
        has_ground_truth=False,
    )


def test_complex64_segment_read_returns_exact_values(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    iq = read_segment_from_path(path, "complex64_le", start_sample=1, sample_count=3)
    assert iq.dtype == np.complex64
    np.testing.assert_array_equal(iq, np.array([3 + 4j, 5 + 6j, 7 + 8j], dtype=np.complex64))


def test_complex64_read_to_end_when_count_none(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    iq = read_segment_from_path(path, "complex64_le", start_sample=2)
    np.testing.assert_array_equal(iq, np.array([5 + 6j, 7 + 8j, 9 + 10j], dtype=np.complex64))


def test_float16_interleaved_segment_read_returns_complex64(tmp_path: Path):
    path = tmp_path / "sample.bin"
    _float16_file(path)
    iq = read_segment_from_path(path, "float16_interleaved_le", start_sample=1, sample_count=3)
    assert iq.dtype == np.complex64
    np.testing.assert_array_equal(iq, np.array([3 + 4j, 5 + 6j, 7 + 8j], dtype=np.complex64))


def test_float16_interleaved_offset_read_matches_sample_offsets(tmp_path: Path):
    path = tmp_path / "sample.bin"
    _float16_file(path)
    iq = read_segment_from_path(path, "float16_interleaved_le", start_sample=4, sample_count=1)
    np.testing.assert_array_equal(iq, np.array([9 + 10j], dtype=np.complex64))


def test_read_segment_resolves_external_recording_path(tmp_path: Path):
    path = tmp_path / "sample.bin"
    _float16_file(path)
    recording = _recording(path, "float16_interleaved_le", num_samples=5)
    iq = read_segment(recording, tmp_path, start_sample=0, sample_count=2)
    np.testing.assert_array_equal(iq, np.array([1 + 2j, 3 + 4j], dtype=np.complex64))


def test_read_segment_rejects_negative_start_sample(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    with pytest.raises(PlatformError):
        read_segment_from_path(path, "complex64_le", start_sample=-1)


def test_read_segment_rejects_nonpositive_count(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    with pytest.raises(PlatformError):
        read_segment_from_path(path, "complex64_le", sample_count=0)


def test_read_segment_rejects_beyond_available_samples(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    with pytest.raises(PlatformError):
        read_segment_from_path(path, "complex64_le", start_sample=4, sample_count=2)


def test_read_segment_rejects_truncated_float16_length(tmp_path: Path):
    path = tmp_path / "truncated.bin"
    np.array([1.0, 2.0, 3.0], dtype="<f2").tofile(path)  # 3 floats -> not a full I/Q pair
    with pytest.raises(PlatformError):
        read_segment_from_path(path, "float16_interleaved_le")


def test_read_segment_rejects_unsupported_format(tmp_path: Path):
    path = tmp_path / "raw.iq"
    _complex64_file(path)
    with pytest.raises(PlatformError):
        read_segment_from_path(path, "sigmf_cf32")