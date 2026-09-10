"""M9.2-C1 DatasetAdapter seam: dataset layout + GroundTruth -> verified input.

A ``DatasetAdapter`` is the only component that reads dataset-specific layout. It
resolves a logical ``(split, key)`` into an inference-facing ``RecordingInput``
after verifying the SAME double identity as the M9.1 SpaceNet resolver:

- ``recording_fingerprint_v1`` — semantic identity built from the dataset name,
  split, the recording's dataset label space, the recording metadata, and the
  GroundTruth semantics. GroundTruth is inspected only for fingerprint
  construction and is never placed on ``RecordingInput``.
- ``source_data_sha256`` — SHA256 over the exact raw IQ bytes.

Dataset roots are trusted/platform-owned construction dependencies of the adapter
instance; they are never accepted from the wire/request. Model-facing inference
code sees only the verified ``RecordingInput`` and never the dataset ``.bin`` /
``.json`` layout or GroundTruth.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Protocol, runtime_checkable

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.pipelines.base import RecordingInput
from app.remote_execution.source_hash import compute_file_sha256

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


@dataclass(frozen=True)
class ResolvedRecordingInput:
    """Verified inference input: double identity + sanitized RecordingInput."""

    recording_fingerprint: str
    source_data_sha256: str
    recording_input: RecordingInput


@runtime_checkable
class DatasetAdapter(Protocol):
    dataset_name: str

    def resolve(
        self,
        *,
        split: str,
        key: str,
        label_space: str,
        expected_fingerprint: str,
        expected_source_hash: str,
        label_space_root: Path,
    ) -> ResolvedRecordingInput: ...


class SpaceNetDatasetAdapter:
    """Built-in ``SpaceNet`` implementation of the DatasetAdapter contract.

    Ports the M9.1 ``resolve_space_net`` identity logic behind the adapter seam.
    ``root`` is the trusted dataset root captured at construction; ``resolve``
    takes the recording's dataset label space (never the plugin output label
    space) for fingerprint identity.
    """

    dataset_name = "SpaceNet"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def resolve(
        self,
        *,
        split: str,
        key: str,
        label_space: str,
        expected_fingerprint: str,
        expected_source_hash: str,
        label_space_root: Path,
    ) -> ResolvedRecordingInput:
        if _KEY_RE.fullmatch(key) is None:
            raise PlatformError("REMOTE_REQUEST_INVALID", "SpaceNet dataset key is invalid.")

        adapter = SpaceNetAdapter(self.root, label_space_root, label_space_id=label_space)
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
            self.dataset_name,
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

        return ResolvedRecordingInput(
            recording_fingerprint=fingerprint,
            source_data_sha256=source_sha,
            recording_input=recording_input,
        )


class DatasetAdapterRegistry:
    """Selects a DatasetAdapter by the recording's ``dataset_name``."""

    def __init__(self, adapters: Iterable[DatasetAdapter] = ()) -> None:
        self._adapters: dict[str, DatasetAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: DatasetAdapter) -> None:
        self._adapters[adapter.dataset_name] = adapter

    def get(self, dataset_name: str) -> DatasetAdapter:
        adapter = self._adapters.get(dataset_name)
        if adapter is None:
            raise PlatformError(
                "DATASET_ADAPTER_NOT_FOUND",
                f"No dataset adapter is registered for '{dataset_name}'.",
            )
        return adapter


def create_dataset_adapter_registry(
    adapters: Iterable[DatasetAdapter] = (),
) -> DatasetAdapterRegistry:
    """Build a registry from already-constructed, root-bound adapters.

    The zero-argument form is valid and yields an empty registry. A built-in
    adapter (e.g. ``SpaceNet``) requires a trusted dataset root, so it is
    constructed by the composition root that owns that root and injected here;
    this factory never invents, hardcodes, or reads a global dataset path.
    """
    return DatasetAdapterRegistry(adapters)
