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


def canonical_number(value: float) -> str:
    value = float(value)
    if value == 0.0:
        return "0"
    return format(value, ".17g")


def _gt_sort_key(gt: ManifestGroundTruth):
    return (gt.t_start_s, gt.t_end_s, gt.f_low_hz, gt.f_high_hz, gt.class_id, gt.class_name)


def canonical_ground_truth_payload(ground_truth: tuple[ManifestGroundTruth, ...]) -> list[dict[str, object]]:
    return [
        {
            "t_start_s": canonical_number(gt.t_start_s),
            "t_end_s": canonical_number(gt.t_end_s),
            "f_low_hz": canonical_number(gt.f_low_hz),
            "f_high_hz": canonical_number(gt.f_high_hz),
            "class_id": gt.class_id,
            "class_name": gt.class_name,
        }
        for gt in sorted(ground_truth, key=_gt_sort_key)
    ]


def canonical_recording_payload(recording: ManifestRecording) -> dict[str, object]:
    return {
        "name": recording.name,
        "data_format": recording.data_format,
        "sample_rate_hz": canonical_number(recording.sample_rate_hz),
        "center_frequency_hz": canonical_number(recording.center_frequency_hz),
        "frequency_low_hz": canonical_number(recording.frequency_low_hz),
        "frequency_high_hz": canonical_number(recording.frequency_high_hz),
        "num_samples": recording.num_samples,
        "duration_s": canonical_number(recording.duration_s),
        "ground_truth": canonical_ground_truth_payload(recording.ground_truth),
    }


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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
        "recordings": [canonical_recording_payload(r) for r in ordered],
    }
    serialized = canonical_json_bytes(payload)
    return FrozenRecordingManifest(
        dataset_name=dataset_name,
        dataset_split=dataset_split,
        label_space=label_space,
        entries=tuple(ordered),
        sha256=sha256(serialized).hexdigest(),
    )