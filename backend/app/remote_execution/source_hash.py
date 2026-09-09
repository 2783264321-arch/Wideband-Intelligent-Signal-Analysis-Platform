"""Exact raw-IQ byte identity hashing and the Recording source-data cache.

``source_data_sha256`` is the exact SHA256 over the raw source file bytes and is
completely independent of ``recording_fingerprint_v1``. Once a value is cached on
the Recording it is treated as a snapshot identity cache and is never silently
re-hashed.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.recordings.model import RecordingModel


def compute_file_sha256(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_recording_source_path(
    recording: RecordingModel,
    data_root: Path,
) -> Path:
    if recording.external_path:
        return Path(recording.external_path).resolve()
    return (data_root / recording.data_path).resolve()


def resolve_source_data_sha256(
    session: Session,
    recording: RecordingModel,
    data_root: Path,
) -> str:
    if recording.source_data_sha256:
        return recording.source_data_sha256

    path = _resolve_recording_source_path(recording, data_root)

    if not path.exists():
        raise PlatformError("SOURCE_DATA_NOT_FOUND", "Recording source data file was not found.")
    if not path.is_file():
        raise PlatformError("SOURCE_DATA_NOT_FILE", "Recording source data path is not a regular file.")

    value = compute_file_sha256(path)
    recording.source_data_sha256 = value
    session.commit()
    return value