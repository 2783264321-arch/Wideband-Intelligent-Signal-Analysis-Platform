from dataclasses import dataclass
from hashlib import sha256

from app.benchmarks.manifest import (
    ManifestRecording,
    canonical_ground_truth_payload,
    canonical_json_bytes,
    canonical_recording_payload,
)

RECORDING_FINGERPRINT_SCHEMA = "recording_fingerprint_v1"
BATCH_IMPORT_FINGERPRINT_SCHEMA = "batch_import_fingerprint_v1"


@dataclass(frozen=True)
class RecordingFingerprintValue:
    schema: str
    metadata: dict[str, object]
    ground_truth_sha256: str
    sha256: str


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