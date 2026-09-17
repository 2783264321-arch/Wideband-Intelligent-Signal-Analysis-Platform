"""Portable dataset identity (P1).

The portable fingerprint is a pragmatic logical identity derived from an ordered
canonical sample manifest. It explicitly does NOT hash raw IQ bytes and does NOT
include any machine-local path, so the same logical dataset registered at
``D:\\SpaceNet`` and ``/data/SpaceNet`` yields the same fingerprint.

Canonicalization is reused from ``app.benchmarks.manifest`` (numbers, ground
truth ordering, and canonical JSON bytes) rather than duplicated here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from app.benchmarks.manifest import (
    ManifestGroundTruth,
    ManifestRecording,
    canonical_json_bytes,
    canonical_recording_payload,
)

# Label-space-less datasets use one explicit internal sentinel for identity;
# the DatasetModel.label_space field itself remains nullable.
NO_LABEL_SPACE_SENTINEL = "__no_label_space__"


@dataclass(frozen=True)
class PortableSample:
    """One dataset sample's path-independent identity inputs.

    Recording id, dataset id, external path, and local root are intentionally
    absent from this type.
    """

    sample_key: str
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    ground_truth: tuple[ManifestGroundTruth, ...]


def _sample_payload(sample: PortableSample) -> dict[str, object]:
    # recording_id is excluded from the canonical payload by the manifest builder.
    return canonical_recording_payload(
        ManifestRecording(
            recording_id="",
            name=sample.sample_key,
            data_format=sample.data_format,
            sample_rate_hz=sample.sample_rate_hz,
            center_frequency_hz=sample.center_frequency_hz,
            frequency_low_hz=sample.frequency_low_hz,
            frequency_high_hz=sample.frequency_high_hz,
            num_samples=sample.num_samples,
            duration_s=sample.duration_s,
            ground_truth=sample.ground_truth,
        )
    )


def compute_portable_dataset_fingerprint(
    *,
    name: str,
    split: str,
    label_space: str | None,
    samples: Sequence[PortableSample],
) -> str:
    """Return a 64-char sha256 over the ordered canonical sample manifest.

    Order independence is guaranteed by sorting on each sample's canonical JSON
    bytes (not on registry/insertion order). Samples without ground truth still
    participate with an empty canonical GT list.
    """
    payloads = [_sample_payload(sample) for sample in samples]
    ordered = sorted(payloads, key=canonical_json_bytes)
    payload = {
        "dataset_name": name,
        "dataset_split": split,
        "label_space": label_space if label_space else NO_LABEL_SPACE_SENTINEL,
        "samples": ordered,
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()


def fingerprint_for_recordings(
    session,
    *,
    name: str,
    split: str,
    label_space: str | None,
    recordings: Sequence,
) -> str:
    """Build the portable fingerprint from persisted Recording/GT rows."""
    from sqlalchemy import select

    from app.ground_truth.model import GroundTruthModel

    recording_ids = [recording.id for recording in recordings]
    grouped_gt: dict[str, list[ManifestGroundTruth]] = {}
    if recording_ids:
        rows = session.scalars(
            select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(recording_ids))
        ).all()
        for row in rows:
            grouped_gt.setdefault(row.recording_id, []).append(
                ManifestGroundTruth(
                    t_start_s=row.t_start_s,
                    t_end_s=row.t_end_s,
                    f_low_hz=row.f_low_hz,
                    f_high_hz=row.f_high_hz,
                    class_id=row.class_id,
                    class_name=row.class_name,
                )
            )

    samples = [
        PortableSample(
            sample_key=recording.sample_key or recording.name,
            data_format=recording.data_format,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            frequency_low_hz=recording.frequency_low_hz,
            frequency_high_hz=recording.frequency_high_hz,
            num_samples=recording.num_samples,
            duration_s=recording.duration_s,
            ground_truth=tuple(grouped_gt.get(recording.id, [])),
        )
        for recording in recordings
    ]
    return compute_portable_dataset_fingerprint(
        name=name, split=split, label_space=label_space, samples=samples
    )
