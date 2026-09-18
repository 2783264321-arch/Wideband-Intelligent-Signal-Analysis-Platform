"""SpaceNet sidecar metadata for standalone uploads (B2).

When a user uploads an IQ file together with its SpaceNet ``.json`` sidecar, the
platform derives sampling rate, center frequency, label space, and ground truth
from the verified dataset contract instead of trusting hand-typed values.

This reuses ``app.datasets.spacenet.SpaceNetAdapter`` for metadata parsing so the
standalone path and the dataset path cannot drift: the same
``observation_range`` semantics (bandwidth = sample rate, midpoint = center
frequency) and the same label-space/class resolution apply.

Ground truth is parsed faithfully here but is only persisted once IQ length is
known (the loader skips bounds checks when ``num_samples`` is unknown).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter
from app.labels.service import LabelSpaceService

SPACENET_UPLOAD_LABEL_SPACE = "spacenet_14"
SPACENET_UPLOAD_MEDIA_TYPES = frozenset(
    {"application/json", "text/json", "text/plain", "application/octet-stream", ""}
)
_MAX_METADATA_BYTES = 1_048_576
# Placeholder IQ length used only to satisfy the adapter's duration-bounds check
# while parsing the sidecar; ground truth is re-validated against the real IQ.
_MIN_PROBE_SAMPLES = 1_000_000


@dataclass(frozen=True)
class UploadGroundTruth:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class UploadMetadata:
    sample_id: str | None = None
    sample_rate_hz: float | None = None
    center_frequency_hz: float | None = None
    label_space: str | None = None
    ground_truth: tuple[UploadGroundTruth, ...] = field(default_factory=tuple)


def _read_json(document: str, *, media_type: str | None) -> dict | None:
    if media_type is not None and media_type not in SPACENET_UPLOAD_MEDIA_TYPES:
        return None
    text = document.strip()
    if not text:
        return None
    if not text.startswith("{"):
        return None
    if len(text.encode("utf-8")) > _MAX_METADATA_BYTES:
        raise PlatformError(
            "INVALID_RECORDING_METADATA", "Uploaded IQ metadata file is too large.", 422
        )
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or "observation_range" not in payload:
        return None
    return payload


def strip_space_net_json(document: str) -> str:
    """Return the SpaceNet JSON sidecar text itself, or raise for anything else.

    A user may pick either the ``.json`` or the ``.bin`` file: if they picked the
    ``.bin``, its paired ``.json`` is embedded and recovered from here.
    """
    text = document.lstrip("\ufeff").strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise PlatformError(
            "INVALID_RECORDING_METADATA",
            "Uploaded metadata is neither a SpaceNet JSON sidecar nor a .bin embedding one.",
            422,
        )
    candidate = text[start : end + 1]
    try:
        payload = json.loads(candidate)
    except ValueError as error:
        raise PlatformError(
            "INVALID_RECORDING_METADATA", "Embedded SpaceNet metadata is not valid JSON.", 422
        ) from error
    if not isinstance(payload, dict) or "observation_range" not in payload:
        raise PlatformError(
            "INVALID_RECORDING_METADATA", "Embedded metadata is not a SpaceNet sidecar.", 422
        )
    return candidate


def parse_upload_metadata(
    document: str | None,
    *,
    label_space_root: Path,
    num_samples: int | None = None,
    sample_rate_hz: float | None = None,
) -> UploadMetadata:
    """Derive Fs/Fc/GT from an uploaded SpaceNet JSON sidecar, when present."""
    if not document:
        return UploadMetadata()
    sidecar = strip_space_net_json(document)
    payload = _read_json(sidecar, media_type=None)
    if payload is None:
        raise PlatformError(
            "INVALID_RECORDING_METADATA",
            "Uploaded IQ metadata is not a SpaceNet JSON sidecar.",
            422,
        )

    # Parse with the verified adapter. The adapter infers the IQ length from the
    # ``.bin`` size and rejects ground truth outside that duration, so the stub is
    # sized to the REAL upload length when it is known. This keeps the standalone
    # upload path on the exact same validation as the registered dataset path.
    probe_samples = num_samples if num_samples else _MIN_PROBE_SAMPLES
    with TemporaryDirectory(prefix="wisa-upload-meta-") as temporary:
        root = Path(temporary)
        split_root = root / "test"
        split_root.mkdir(parents=True, exist_ok=True)
        sample_id = "sample"
        (split_root / f"{sample_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        (split_root / f"{sample_id}.bin").write_bytes(b"\x00\x00\x00\x00" * probe_samples)
        adapter = SpaceNetAdapter(root, Path(label_space_root), SPACENET_UPLOAD_LABEL_SPACE)
        sample = adapter.load("test", sample_id)

    ground_truth = tuple(
        UploadGroundTruth(
            t_start_s=signal.t_start_s,
            t_end_s=signal.t_end_s,
            f_low_hz=signal.f_low_hz,
            f_high_hz=signal.f_high_hz,
            class_id=signal.class_id,
            class_name=signal.class_name,
        )
        for signal in sample.signals
    )
    return UploadMetadata(
        sample_id=sample_id,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        label_space=SPACENET_UPLOAD_LABEL_SPACE,
        ground_truth=ground_truth,
    )


def rebuild_ground_truth(
    metadata: UploadMetadata, *, num_samples: int, sample_rate_hz: float
):
    """Rebuild GT rows with exact duration bounds once the IQ length is known."""
    duration_s = num_samples / sample_rate_hz
    rows = []
    for index, signal in enumerate(metadata.ground_truth):
        if not (0.0 <= signal.t_start_s < signal.t_end_s <= duration_s + 1e-9):
            raise PlatformError(
                "INVALID_RECORDING_METADATA",
                "SpaceNet JSON ground truth falls outside the uploaded IQ duration.",
                422,
                {"signal_index": index, "iq_duration_s": duration_s},
            )
        rows.append(signal)
    return rows
