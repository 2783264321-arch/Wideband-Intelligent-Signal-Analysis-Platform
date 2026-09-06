from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar

from app.benchmarks.loader import LoadedBenchmark
from app.benchmarks.manifest import canonical_number
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL_V1, PHYSICAL_TF_PROTOCOL_V2
from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import EvaluationSample

GT = TypeVar("GT")


@dataclass(frozen=True)
class GroundTruthAccounting:
    raw_count: int
    canonical_count: int
    removed_count: int


@dataclass(frozen=True)
class ProtocolBenchmarkView:
    samples: tuple[EvaluationSample, ...]
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]
    runs_by_recording: dict
    recordings_by_id: dict
    ground_truth_accounting: GroundTruthAccounting


def _semantic_key(gt) -> tuple[str, str, str, str, int]:
    return (
        canonical_number(gt.t_start_s),
        canonical_number(gt.t_end_s),
        canonical_number(gt.f_low_hz),
        canonical_number(gt.f_high_hz),
        int(gt.class_id),
    )


def _representative_sort_key(gt):
    # class_name is NOT part of duplicate identity. It is only the final deterministic
    # tie-break for choosing the display representative when duplicate rows disagree
    # on class_name. Local IDs / insertion order never participate.
    return (
        int(getattr(gt, "manifest_order", 0)),
        *_semantic_key(gt),
        str(gt.class_name),
    )


def canonicalize_ground_truths(ground_truths: Sequence[GT]) -> tuple[GT, ...]:
    # Called for one Recording at a time: recording_id is intentionally not part of the semantic key.
    by_key: dict[tuple[str, str, str, str, int], GT] = {}
    for gt in sorted(ground_truths, key=_representative_sort_key):
        by_key.setdefault(_semantic_key(gt), gt)
    return tuple(sorted(by_key.values(), key=_representative_sort_key))


def count_ground_truths_for_protocol(evaluation_protocol: str, ground_truths: Sequence[GT]) -> int:
    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V1:
        return len(ground_truths)
    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V2:
        return len(canonicalize_ground_truths(ground_truths))
    raise ValueError(f"Unsupported evaluation protocol: {evaluation_protocol}")


def build_protocol_view(evaluation_protocol: str, loaded: LoadedBenchmark) -> ProtocolBenchmarkView:
    if evaluation_protocol not in {PHYSICAL_TF_PROTOCOL_V1, PHYSICAL_TF_PROTOCOL_V2}:
        raise ValueError(f"Unsupported evaluation protocol: {evaluation_protocol}")

    if evaluation_protocol == PHYSICAL_TF_PROTOCOL_V1:
        samples = loaded.samples
    else:
        samples = tuple(
            EvaluationSample(
                recording_id=sample.recording_id,
                manifest_order=sample.manifest_order,
                ground_truths=canonicalize_ground_truths(sample.ground_truths),
                predictions=sample.predictions,
            )
            for sample in loaded.samples
        )

    ground_truths = tuple(gt for sample in samples for gt in sample.ground_truths)
    raw_count = len(loaded.ground_truths)
    canonical_count = len(ground_truths)
    return ProtocolBenchmarkView(
        samples=samples,
        ground_truths=ground_truths,
        predictions=loaded.predictions,
        runs_by_recording=loaded.runs_by_recording,
        recordings_by_id=loaded.recordings_by_id,
        ground_truth_accounting=GroundTruthAccounting(
            raw_count=raw_count,
            canonical_count=canonical_count,
            removed_count=raw_count - canonical_count,
        ),
    )