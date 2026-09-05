from dataclasses import dataclass
from hashlib import sha256

from app.benchmarks.manifest import (
    ManifestRecording,
    canonical_ground_truth_payload,
    canonical_json_bytes,
    canonical_number,
    canonical_recording_payload,
)
from app.imported_runs.batch_schema import BatchManifest
from app.imported_runs.schema import PackageDetection

RECORDING_FINGERPRINT_SCHEMA = "recording_fingerprint_v1"
BATCH_IMPORT_FINGERPRINT_SCHEMA = "batch_import_fingerprint_v1"


@dataclass(frozen=True)
class RecordingFingerprintValue:
    schema: str
    metadata: dict[str, object]
    ground_truth_sha256: str
    sha256: str


@dataclass(frozen=True)
class CanonicalBatchItem:
    key: str
    recording_fingerprint: str
    parameters: dict[str, object]
    detections: tuple[PackageDetection, ...]


def build_recording_fingerprint(
    dataset_name: str,
    dataset_split: str,
    label_space: str,
    recording: ManifestRecording,
) -> RecordingFingerprintValue:
    recording_payload = canonical_recording_payload(recording)
    gt_payload = canonical_ground_truth_payload(recording.ground_truth)
    payload = {
        "schema": RECORDING_FINGERPRINT_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "label_space": label_space,
        "recording": recording_payload,
    }
    metadata = {
        "data_format": recording.data_format,
        "sample_rate_hz": recording.sample_rate_hz,
        "center_frequency_hz": recording.center_frequency_hz,
        "frequency_low_hz": recording.frequency_low_hz,
        "frequency_high_hz": recording.frequency_high_hz,
        "num_samples": recording.num_samples,
        "duration_s": recording.duration_s,
    }
    return RecordingFingerprintValue(
        schema=RECORDING_FINGERPRINT_SCHEMA,
        metadata=metadata,
        ground_truth_sha256=sha256(canonical_json_bytes(gt_payload)).hexdigest(),
        sha256=sha256(canonical_json_bytes(payload)).hexdigest(),
    )


def canonical_detection_payload(item: PackageDetection) -> dict[str, object]:
    return {
        "id": item.id,
        "t_start_s": canonical_number(item.t_start_s),
        "t_end_s": canonical_number(item.t_end_s),
        "f_low_hz": canonical_number(item.f_low_hz),
        "f_high_hz": canonical_number(item.f_high_hz),
        "class_id": item.class_id,
        "class_name": item.class_name,
        "confidence": canonical_number(item.confidence),
        "scores": None if item.scores is None else {
            key: canonical_number(item.scores[key]) for key in sorted(item.scores)
        },
    }


def _detection_sort_key(detection: PackageDetection) -> bytes:
    return canonical_json_bytes(canonical_detection_payload(detection))


def build_batch_import_fingerprint(
    manifest: BatchManifest,
    items: tuple[CanonicalBatchItem, ...],
) -> str:
    by_key = {item.key: item for item in items}
    if len(by_key) != len(items):
        raise ValueError("duplicate item key in batch fingerprint input")
    ordered = [by_key[key] for key in sorted(by_key)]
    canonical_items = []
    for item in ordered:
        canonical_items.append({
            "key": item.key,
            "recording_fingerprint": item.recording_fingerprint,
            "parameters": item.parameters,
            "detections": [
                canonical_detection_payload(detection)
                for detection in sorted(item.detections, key=_detection_sort_key)
            ],
        })
    payload = {
        "schema": BATCH_IMPORT_FINGERPRINT_SCHEMA,
        "batch_schema_version": manifest.schema_version,
        "pipeline": manifest.pipeline.model_dump(),
        "label_space": manifest.label_space,
        "dataset": manifest.dataset.model_dump(),
        "result_provenance": manifest.result_provenance.model_dump(),
        "items": canonical_items,
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()