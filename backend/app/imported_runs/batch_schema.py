"""Batch Analysis Package v1 strict wire contract."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.imported_runs.schema import ExecutionMetadata, Name, Number, PackageObject, PipelineMetadata

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DatasetMetadata(PackageObject):
    name: Name
    split: Name


class ResultProvenance(PackageObject):
    code_commit: str | None = None
    config_sha256: Sha256Hex | None = None
    split_manifest_sha256: Sha256Hex | None = None
    source_predictions_sha256: Sha256Hex | None = None
    artifact_sha256: dict[str, Sha256Hex] = Field(default_factory=dict)


class TransportProvenance(PackageObject):
    exporter_version: Name
    platform_repo_commit: str | None = None
    export_timestamp: str | None = None


class RecordingFingerprintMetadata(PackageObject):
    data_format: Name
    sample_rate_hz: Number
    center_frequency_hz: Number
    frequency_low_hz: Number
    frequency_high_hz: Number
    num_samples: Annotated[int, Field(ge=1)]
    duration_s: Annotated[Number, Field(gt=0)]


class RecordingFingerprintWire(PackageObject):
    schema: Literal["recording_fingerprint_v1"]
    metadata: RecordingFingerprintMetadata
    ground_truth_sha256: Sha256Hex
    sha256: Sha256Hex


class BatchItemRecording(PackageObject):
    name: Name
    fingerprint: RecordingFingerprintWire


class BatchItem(PackageObject):
    key: Name
    package_path: Name
    recording: BatchItemRecording


class HistoricalReference(PackageObject):
    reference_only: Literal[True]
    report_sha256: Sha256Hex
    images: Annotated[int, Field(ge=0)] | None = None
    canonical_ground_truth: Annotated[int, Field(ge=0)] | None = None
    predictions: Annotated[int, Field(ge=0)] | None = None
    recorded_map50: Annotated[Number, Field(ge=0, le=1)] | None = None
    recorded_map50_95: Annotated[Number, Field(ge=0, le=1)] | None = None


class BatchManifest(PackageObject):
    schema_version: Literal[1]
    batch_id: Name
    pipeline: PipelineMetadata
    label_space: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=128)]
    dataset: DatasetMetadata
    expected_items: Annotated[int, Field(ge=1, le=10_000)]
    execution: ExecutionMetadata
    result_provenance: ResultProvenance
    transport_provenance: TransportProvenance
    recording_manifest_hash: Sha256Hex | None = None
    historical_reference: HistoricalReference | None = None
    items: Annotated[list[BatchItem], Field(min_length=1, max_length=10_000)]


class BatchRunMapping(PackageObject):
    recording_id: Name
    recording_name: Name
    analysis_run_id: Name


class BatchImportSummary(PackageObject):
    batch_id: Name
    import_fingerprint: Sha256Hex
    archive_sha256: Sha256Hex
    dataset_name: Name
    dataset_split: Name
    pipeline_id: Name
    pipeline_version: Name
    label_space: Name
    item_count: Annotated[int, Field(ge=0)]
    detection_count: Annotated[int, Field(ge=0)]
    already_imported: bool
    created_runs: Annotated[int, Field(ge=0)]
    existing_runs: Annotated[int, Field(ge=0)]
    created_detections: Annotated[int, Field(ge=0)]
    matched_recordings: Annotated[int, Field(ge=0)]
    missing_recordings: Annotated[int, Field(ge=0)]
    ambiguous_recordings: Annotated[int, Field(ge=0)]
    fingerprint_mismatches: Annotated[int, Field(ge=0)]
    recording_run_mapping: list[BatchRunMapping]