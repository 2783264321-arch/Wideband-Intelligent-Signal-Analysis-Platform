"""SpaceNet sample source for the V1 research batch exporter.

Loads dataset recording bounds and ground truth through the existing platform
SpaceNet adapter (never full-IQ content hashing; the adapter only stats the
``.bin``), computes ``recording_fingerprint_v1`` from that canonical material,
and attaches already-computed detections.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.core.errors import PlatformError
from app.datasets.spacenet import SpaceNetAdapter
from research.m9_legacy_bridge.schema import RecordingContext
from research.v1_artifact_exporter.exporter import ResearchSample
from research.v1_artifact_exporter.predictions import adapt_detections

_SPLIT_NAMES = ("test", "train")


class UnexpectedSampleError(ValueError):
    """Predictions reference a sample id that is not part of the export set."""


class MissingDatasetSampleError(ValueError):
    """A requested sample id is absent from the dataset split."""


def resolve_split_dir(dataset_dir: Path, dataset_split: str) -> tuple[Path, str, Path]:
    """Return ``(root, split, split_dir)`` for a dataset root or split directory."""
    directory = Path(dataset_dir)
    if directory.name in _SPLIT_NAMES:
        return directory.parent, directory.name, directory
    return directory, dataset_split, directory / dataset_split


def list_split_sample_ids(dataset_dir: Path, dataset_split: str = "test") -> list[str]:
    """Return the sorted ``.bin`` stems present in the dataset split."""
    _, _, split_dir = resolve_split_dir(dataset_dir, dataset_split)
    if not split_dir.is_dir():
        raise MissingDatasetSampleError(f"dataset split directory not found: {split_dir}")
    stems = sorted({path.stem for path in split_dir.glob("*.bin")})
    if not stems:
        raise MissingDatasetSampleError(f"dataset split contains no samples: {split_dir}")
    return stems


def build_research_samples(
    *,
    dataset_dir: Path,
    label_space_root: Path,
    label_space_id: str,
    label_classes: Mapping[int, str],
    sample_ids: Sequence[str],
    predictions_by_sample: Mapping[str, Sequence[Mapping[str, Any]]],
    dataset_name: str,
    dataset_split: str,
) -> tuple[ResearchSample, ...]:
    """Build canonical research samples in lexical sample-id order."""
    root, split, _ = resolve_split_dir(dataset_dir, dataset_split)
    adapter = SpaceNetAdapter(root, Path(label_space_root), label_space_id)
    wanted = list(sample_ids)
    known = set(wanted)

    unexpected = sorted({sid for sid in predictions_by_sample if sid not in known})
    if unexpected:
        raise UnexpectedSampleError(
            f"predictions reference sample ids outside the export set: {unexpected}"
        )

    samples: list[ResearchSample] = []
    for sample_id in wanted:
        try:
            sample = adapter.load(split, sample_id)
        except PlatformError as exc:
            raise MissingDatasetSampleError(
                f"dataset sample {split}/{sample_id} was not found"
            ) from exc
        ground_truth = tuple(
            ManifestGroundTruth(
                t_start_s=signal.t_start_s, t_end_s=signal.t_end_s,
                f_low_hz=signal.f_low_hz, f_high_hz=signal.f_high_hz,
                class_id=signal.class_id, class_name=signal.class_name,
            )
            for signal in sample.signals
        )
        manifest_recording = ManifestRecording(
            recording_id=sample.id, name=sample.id, data_format=sample.data_format,
            sample_rate_hz=sample.sample_rate_hz, center_frequency_hz=sample.center_frequency_hz,
            frequency_low_hz=sample.frequency_low_hz, frequency_high_hz=sample.frequency_high_hz,
            num_samples=sample.num_samples, duration_s=sample.duration_s,
            ground_truth=ground_truth,
        )
        context = RecordingContext(
            name=sample.id, duration_s=sample.duration_s,
            frequency_low_hz=sample.frequency_low_hz, frequency_high_hz=sample.frequency_high_hz,
            dataset=f"{dataset_name} advanced/{split}",
        )
        detections = adapt_detections(
            predictions_by_sample.get(sample_id, ()),
            recording=context,
            label_space=label_classes,
        )
        samples.append(ResearchSample(manifest_recording=manifest_recording, detections=detections))
    return tuple(samples)
