"""Unified lazy Recording IQ reader.

Format dispatch lives only here. Pipelines and DSP endpoints consume
``read_segment`` / ``read_segment_from_path`` and never parse SpaceNet
``.bin`` files directly.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel


def resolve_recording_path(recording: RecordingModel, data_root: Path) -> Path:
    if recording.external_path:
        return Path(recording.external_path).resolve()
    path = (data_root / recording.data_path).resolve()
    root = data_root.resolve()
    if root not in path.parents:
        raise PlatformError("INVALID_RECORDING", "Recording path escapes the data root.")
    return path


def _validate_segment(available_samples: int, start_sample: int, sample_count: int | None) -> int:
    if start_sample < 0:
        raise PlatformError("INVALID_RECORDING", "start_sample must be non-negative.")
    if sample_count is not None and sample_count <= 0:
        raise PlatformError("INVALID_RECORDING", "sample_count must be positive.")
    count = sample_count if sample_count is not None else available_samples - start_sample
    if count < 0 or start_sample + count > available_samples:
        raise PlatformError("INVALID_RECORDING", "Requested IQ segment is outside the recording.")
    return count


def read_segment_from_path(
    path: Path,
    data_format: str,
    start_sample: int = 0,
    sample_count: int | None = None,
) -> np.ndarray:
    path = Path(path).resolve()
    if not path.is_file():
        raise PlatformError("INVALID_RECORDING", "Recording IQ file is missing.")

    if data_format == "complex64_le":
        byte_size = path.stat().st_size
        if byte_size % 8:
            raise PlatformError("INVALID_RECORDING", "complex64_le IQ byte length is not divisible by 8.")
        available = byte_size // 8
        count = _validate_segment(available, start_sample, sample_count)
        data = np.memmap(path, dtype="<c8", mode="r", shape=(available,))
        try:
            return np.asarray(data[start_sample:start_sample + count], dtype=np.complex64).copy()
        finally:
            del data

    if data_format == "float16_interleaved_le":
        byte_size = path.stat().st_size
        if byte_size % 4:
            raise PlatformError("INVALID_RECORDING", "float16 interleaved IQ byte length is not divisible by 4.")
        available = byte_size // 4
        count = _validate_segment(available, start_sample, sample_count)
        data = np.memmap(path, mode="r", dtype="<f2", shape=(available * 2,))
        try:
            segment = np.asarray(data[start_sample * 2:(start_sample + count) * 2], dtype=np.float32).copy()
        finally:
            del data
        pairs = segment.reshape(-1, 2)
        return (pairs[:, 0] + 1j * pairs[:, 1]).astype(np.complex64, copy=False)

    raise PlatformError("INVALID_RECORDING", f"Unsupported IQ format: {data_format}")


def read_segment(
    recording: RecordingModel,
    data_root: Path,
    start_sample: int = 0,
    sample_count: int | None = None,
) -> np.ndarray:
    path = resolve_recording_path(recording, data_root)
    return read_segment_from_path(path, recording.data_format, start_sample, sample_count)