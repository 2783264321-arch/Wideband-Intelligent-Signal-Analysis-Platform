"""M9.2-C2 generic recording resolution + deprecated SpaceNet shim.

Recording resolution is now a thin generic dispatch over the
``DatasetAdapterRegistry``. The adapter is the only component that reads
dataset-specific layout and verifies the double identity:

- ``recording_fingerprint_v1`` — semantic identity (dataset/split + the
  recording's dataset label space + recording metadata + GroundTruth semantics);
  GroundTruth is inspected only for fingerprint construction and never reaches
  the inference-facing ``RecordingInput``.
- ``source_data_sha256`` — SHA256 over the exact raw IQ bytes.

``resolve_space_net`` is retained as a deprecated compatibility shim for the
existing M9.1 remote worker call path. It constructs the built-in SpaceNet
adapter from its trusted ``dataset_root`` and delegates through
``resolve_recording``; no dataset path is ever accepted from the wire.
"""
from __future__ import annotations

from pathlib import Path

from app.datasets.adapter import (
    DatasetAdapterRegistry,
    ResolvedRecordingInput,
    SpaceNetDatasetAdapter,
    create_dataset_adapter_registry,
)

_DATASET_NAME = "SpaceNet"

# Backward-compatible alias for the pre-C2 type name.
ResolvedSpaceNetInput = ResolvedRecordingInput


def resolve_recording(
    adapter_registry: DatasetAdapterRegistry,
    *,
    dataset_name: str,
    split: str,
    key: str,
    label_space: str,
    expected_fingerprint: str,
    expected_source_hash: str,
    label_space_root: Path,
) -> ResolvedRecordingInput:
    """Resolve and verify one recording by dispatching to its dataset adapter.

    ``key`` is treated purely as a logical identifier and never as a filesystem
    path; ``expected_fingerprint`` / ``expected_source_hash`` are the frozen
    request identities verified by the adapter. The adapter's dataset root is an
    instance (trusted composition) dependency, never a request argument.
    """
    return adapter_registry.get(dataset_name).resolve(
        split=split,
        key=key,
        label_space=label_space,
        expected_fingerprint=expected_fingerprint,
        expected_source_hash=expected_source_hash,
        label_space_root=label_space_root,
    )


def resolve_space_net(
    dataset_root: Path,
    split: str,
    key: str,
    label_space: str,
    expected_fingerprint: str,
    expected_source_hash: str,
    label_space_root: Path,
) -> ResolvedRecordingInput:
    """Deprecated SpaceNet shim; delegates through the generic adapter path.

    Kept for the existing M9.1 callers. The trusted ``dataset_root`` is injected
    into a SpaceNet adapter at this composition point; there is no global or
    hardcoded dataset path.
    """
    registry = create_dataset_adapter_registry([SpaceNetDatasetAdapter(dataset_root)])
    return resolve_recording(
        registry,
        dataset_name=_DATASET_NAME,
        split=split,
        key=key,
        label_space=label_space,
        expected_fingerprint=expected_fingerprint,
        expected_source_hash=expected_source_hash,
        label_space_root=label_space_root,
    )
