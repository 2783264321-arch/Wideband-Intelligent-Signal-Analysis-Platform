"""M9.1-B Task 9 SpaceNet remote recording resolver.

Resolves a logical SpaceNet ``(split, key)`` identity into a sanitized
inference input after verifying TWO independent identities:

- ``recording_fingerprint_v1`` — semantic (dataset/split/label-space +
  recording metadata + GroundTruth semantics). It does NOT hash IQ bytes.
- ``source_data_sha256`` — SHA256 of the exact raw ``.bin`` bytes.

GroundTruth is inspected ONLY during fingerprint verification and is never
exposed on the returned inference-facing object. No arbitrary client data
path is ever accepted; ``SpaceNetAdapter`` is the only dataset resolver.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.pipelines.base import RecordingInput
from app.remote_execution.source_hash import compute_file_sha256

_DATASET_NAME = "SpaceNet"
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


@dataclass(frozen=True)
class ResolvedSpaceNetInput:
    recording_fingerprint: str
    source_data_sha256: str
    recording_input: RecordingInput


def _invalid_key() -> PlatformError:
    return PlatformError("REMOTE_REQUEST_INVALID", "SpaceNet dataset key is invalid.")


def resolve_space_net(
    dataset_root: Path,
    split: str,
    key: str,
    label_space: str,
    expected_fingerprint: str,
    expected_source_hash: str,
    label_space_root: Path,
) -> ResolvedSpaceNetInput:
    """Resolve and verify one SpaceNet sample's double identity.

    ``key`` is treated purely as a logical identifier and never as a
    filesystem path. ``SpaceNetAdapter`` performs the strict split validation
    (``train`` / ``test``) and is the only dataset resolver here.
    """
    if _KEY_RE.fullmatch(key) is None:
        raise _invalid_key()

    adapter = SpaceNetAdapter(dataset_root, label_space_root, label_space_id=label_space)
    sample = adapter.load(split, key)

    ground_truth = tuple(
        ManifestGroundTruth(
            t_start_s=signal.t_start_s,
            t_end_s=signal.t_end_s,
            f_low_hz=signal.f_low_hz,
            f_high_hz=signal.f_high_hz,
            class_id=signal.class_id,
            class_name=signal.class_name,
        )
        for signal in sample.signals
    )
    manifest_recording = ManifestRecording(
        recording_id="local-dummy",  # local-only; excluded from the hash payload
        name=sample.id,
        data_format=sample.data_format,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        frequency_low_hz=sample.frequency_low_hz,
        frequency_high_hz=sample.frequency_high_hz,
        num_samples=sample.num_samples,
        duration_s=sample.duration_s,
        ground_truth=ground_truth,
    )
    fingerprint = build_recording_fingerprint(
        _DATASET_NAME,
        split,
        label_space,
        manifest_recording,
    ).sha256
    if fingerprint != expected_fingerprint:
        raise PlatformError(
            "RECORDING_FINGERPRINT_MISMATCH",
            "Recording fingerprint does not match the expected identity.",
        )

    source_sha = compute_file_sha256(sample.data_path)
    if source_sha != expected_source_hash:
        raise PlatformError(
            "SOURCE_DATA_HASH_MISMATCH",
            "Raw source data hash does not match the expected identity.",
        )

    recording_input = RecordingInput(
        id=sample.id,
        data_path=sample.data_path,
        data_format=sample.data_format,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        frequency_low_hz=sample.frequency_low_hz,
        frequency_high_hz=sample.frequency_high_hz,
        duration_s=sample.duration_s,
        label_space=label_space,
    )

    return ResolvedSpaceNetInput(
        recording_fingerprint=fingerprint,
        source_data_sha256=source_sha,
        recording_input=recording_input,
    )