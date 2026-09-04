from pathlib import Path

import numpy as np

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel


def read_iq(
    recording: RecordingModel,
    data_root: Path,
    start_sample: int = 0,
    count: int | None = None,
) -> np.ndarray:
    if recording.data_format != "complex64_le":
        raise PlatformError("INVALID_RECORDING", f"Unsupported IQ format: {recording.data_format}")
    if start_sample < 0:
        raise PlatformError("INVALID_RECORDING", "start_sample must be non-negative.")

    path = (data_root / recording.data_path).resolve()
    root = data_root.resolve()
    if root not in path.parents:
        raise PlatformError("INVALID_RECORDING", "Recording path escapes the data root.")
    if not path.exists():
        raise PlatformError("INVALID_RECORDING", "Recording IQ file is missing.")

    data = np.memmap(path, dtype="<c8", mode="r")
    stop = None if count is None else start_sample + max(count, 0)
    return np.asarray(data[start_sample:stop], dtype=np.complex64).copy()
