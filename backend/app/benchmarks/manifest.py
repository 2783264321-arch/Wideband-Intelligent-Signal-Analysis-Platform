from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class ManifestGroundTruth:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class ManifestRecording:
    recording_id: str  # local lookup only; excluded from hash payload
    name: str
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    ground_truth: tuple[ManifestGroundTruth, ...]


@dataclass(frozen=True)
class FrozenRecordingManifest:
    dataset_name: str
    dataset_split: str
    label_space: str
    entries: tuple[ManifestRecording, ...]
    sha256: str


def _number(value: float) -> str:
    value = float(value)
    if value == 0.0:
        return "0"
    return format(value, ".17g")


def _gt_sort_key(gt: ManifestGroundTruth):
    return (gt.t_start_s, gt.t_end_s, gt.f_low_hz, gt.f_high_hz, gt.class_id, gt.class_name)


def build_recording_manifest(
    dataset_name: str,
    dataset_split: str,
    label_space: str,
    recordings: list[ManifestRecording],
) -> FrozenRecordingManifest:
    by_name: dict[str, ManifestRecording] = {}
    for recording in recordings:
        if recording.name in by_name:
            raise ValueError("duplicate Recording.name in dataset snapshot")
        by_name[recording.name] = recording
    ordered = [by_name[name] for name in sorted(by_name)]

    payload = {
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "label_space": label_space,
        "recordings": [
            {
                "name": r.name,
                "data_format": r.data_format,
                "sample_rate_hz": _number(r.sample_rate_hz),
                "center_frequency_hz": _number(r.center_frequency_hz),
                "frequency_low_hz": _number(r.frequency_low_hz),
                "frequency_high_hz": _number(r.frequency_high_hz),
                "num_samples": r.num_samples,
                "duration_s": _number(r.duration_s),
                "ground_truth": [
                    {
                        "t_start_s": _number(gt.t_start_s),
                        "t_end_s": _number(gt.t_end_s),
                        "f_low_hz": _number(gt.f_low_hz),
                        "f_high_hz": _number(gt.f_high_hz),
                        "class_id": gt.class_id,
                        "class_name": gt.class_name,
                    }
                    for gt in sorted(r.ground_truth, key=_gt_sort_key)
                ],
            }
            for r in ordered
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return FrozenRecordingManifest(
        dataset_name=dataset_name,
        dataset_split=dataset_split,
        label_space=label_space,
        entries=tuple(ordered),
        sha256=sha256(serialized).hexdigest(),
    )