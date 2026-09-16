"""Deterministic research-result -> Batch Analysis Package v1 adapter.

Layer contract:

    generic deterministic writer   research/m9_legacy_bridge/batch_exporter.py
            ^
    this research-result adapter   build_batch_manifest / export_research_batch
            ^
    CLI / operator entrypoint      research/v1_artifact_exporter/cli.py

The adapter knows Batch Analysis Package v1 only; it never knows a specific
model's internals and never performs inference.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.benchmarks.manifest import ManifestRecording
from app.imported_runs.batch_schema import (
    BatchItem,
    BatchItemRecording,
    BatchManifest,
    DatasetMetadata,
    HistoricalReference,
    RecordingFingerprintWire,
    ResultProvenance,
    TransportProvenance,
)
from app.imported_runs.fingerprint import (
    CanonicalBatchItem,
    build_batch_import_fingerprint,
    build_recording_fingerprint,
)
from app.imported_runs.schema import (
    ExecutionMetadata,
    Manifest,
    PackageDetection,
    PipelineMetadata,
    RecordingMetadata,
    ResultPaths,
)
from research.m9_legacy_bridge.batch_exporter import BatchExportItem, export_batch_package


@dataclass(frozen=True)
class ResearchSample:
    manifest_recording: ManifestRecording
    detections: tuple[PackageDetection, ...]


@dataclass(frozen=True)
class ResearchBatchRequest:
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline: PipelineMetadata
    execution: ExecutionMetadata
    result_provenance: ResultProvenance
    transport_provenance: TransportProvenance
    batch_id: str
    samples: tuple[ResearchSample, ...]
    output_path: Path
    recording_manifest_hash: str | None = None
    historical_reference: HistoricalReference | None = None


@dataclass(frozen=True)
class ResearchBatchResult:
    output_path: Path
    archive_sha256: str
    import_fingerprint: str
    recording_manifest_hash: str | None
    item_count: int
    detection_count: int
    zero_detection_items: int


def build_batch_manifest(
    request: ResearchBatchRequest,
) -> tuple[BatchManifest, tuple[BatchExportItem, ...], tuple[CanonicalBatchItem, ...]]:
    """Build the outer manifest and per-item export payloads in lexical order."""
    ordered = sorted(request.samples, key=lambda sample: sample.manifest_recording.name)
    names = [sample.manifest_recording.name for sample in ordered]
    if not ordered:
        raise ValueError("a research batch must contain at least one sample")
    if len(names) != len(set(names)):
        raise ValueError("duplicate Recording name in research batch")

    items: list[BatchItem] = []
    export_items: list[BatchExportItem] = []
    canonical_items: list[CanonicalBatchItem] = []
    for index, sample in enumerate(ordered):
        manifest_recording = sample.manifest_recording
        fingerprint = build_recording_fingerprint(
            request.dataset_name, request.dataset_split, request.label_space, manifest_recording
        )
        key = f"{index:06d}"
        package_path = f"items/{key}"
        item = BatchItem(
            key=key,
            package_path=package_path,
            recording=BatchItemRecording(
                name=manifest_recording.name,
                fingerprint=RecordingFingerprintWire(
                    schema="recording_fingerprint_v1",
                    metadata=fingerprint.metadata,
                    ground_truth_sha256=fingerprint.ground_truth_sha256,
                    sha256=fingerprint.sha256,
                ),
            ),
        )
        child_manifest = Manifest(
            schema_version=1,
            pipeline=request.pipeline,
            label_space=request.label_space,
            recording=RecordingMetadata(
                name=manifest_recording.name, dataset=request.dataset_name
            ),
            execution=request.execution,
            results=ResultPaths(detections="detections.json"),
            parameters={},
        )
        items.append(item)
        export_items.append(
            BatchExportItem(
                item=item, child_manifest=child_manifest, detections=sample.detections
            )
        )
        canonical_items.append(
            CanonicalBatchItem(
                key=key,
                recording_fingerprint=fingerprint.sha256,
                parameters={},
                detections=sample.detections,
            )
        )

    manifest = BatchManifest(
        schema_version=1,
        batch_id=request.batch_id,
        pipeline=request.pipeline,
        label_space=request.label_space,
        dataset=DatasetMetadata(name=request.dataset_name, split=request.dataset_split),
        expected_items=len(items),
        execution=request.execution,
        result_provenance=request.result_provenance,
        transport_provenance=request.transport_provenance,
        recording_manifest_hash=request.recording_manifest_hash,
        historical_reference=request.historical_reference,
        items=items,
    )
    return manifest, tuple(export_items), tuple(canonical_items)


def export_research_batch(request: ResearchBatchRequest) -> ResearchBatchResult:
    """Write the BAPv1 ZIP and report the post-build archive SHA256."""
    manifest, export_items, canonical_items = build_batch_manifest(request)
    import_fingerprint = build_batch_import_fingerprint(manifest, canonical_items)
    output_path = Path(request.output_path)
    export_batch_package(output_path, manifest, export_items)
    archive_sha256 = sha256(output_path.read_bytes()).hexdigest()
    detection_count = sum(len(sample.detections) for sample in request.samples)
    zero_detection_items = sum(1 for sample in request.samples if not sample.detections)
    return ResearchBatchResult(
        output_path=output_path,
        archive_sha256=archive_sha256,
        import_fingerprint=import_fingerprint,
        recording_manifest_hash=request.recording_manifest_hash,
        item_count=len(manifest.items),
        detection_count=detection_count,
        zero_detection_items=zero_detection_items,
    )
