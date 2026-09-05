from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording, canonical_number
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.ground_truth.model import GroundTruthModel
from app.imported_runs.batch_archive import (
    MAX_TOTAL_DETECTIONS,
    invalid_batch,
    read_batch_json,
    _safe_path,
)
from app.imported_runs.batch_schema import BatchItem, BatchManifest
from app.imported_runs.fingerprint import (
    CanonicalBatchItem,
    RecordingFingerprintValue,
    build_batch_import_fingerprint,
    build_recording_fingerprint,
)
from app.imported_runs.validation import ValidatedAnalysisPackage, validate_extracted_package
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel

_SENTINEL_LABEL_SPACE = "__none__"


@dataclass(frozen=True)
class ResolvedBatchItem:
    item: BatchItem
    recording: RecordingModel
    manifest_recording: ManifestRecording
    recording_fingerprint: RecordingFingerprintValue
    child_root: Path
    validated_package: ValidatedAnalysisPackage


@dataclass(frozen=True)
class ValidatedBatch:
    manifest: BatchManifest
    items: tuple[ResolvedBatchItem, ...]
    total_detections: int
    import_fingerprint: str
    local_recording_manifest_hash: str | None


def _manifest_recording_for(recording: RecordingModel, gt_rows: list[GroundTruthModel]) -> ManifestRecording:
    ground_truth = tuple(
        ManifestGroundTruth(
            t_start_s=gt.t_start_s, t_end_s=gt.t_end_s,
            f_low_hz=gt.f_low_hz, f_high_hz=gt.f_high_hz,
            class_id=gt.class_id, class_name=gt.class_name,
        )
        for gt in gt_rows
    )
    return ManifestRecording(
        recording_id=recording.id,
        name=recording.name,
        data_format=recording.data_format,
        sample_rate_hz=recording.sample_rate_hz,
        center_frequency_hz=recording.center_frequency_hz,
        frequency_low_hz=recording.frequency_low_hz,
        frequency_high_hz=recording.frequency_high_hz,
        num_samples=recording.num_samples,
        duration_s=recording.duration_s,
        ground_truth=ground_truth,
    )


def validate_batch(
    root: Path,
    session: Session,
    labels: LabelSpaceService,
) -> ValidatedBatch:
    manifest = BatchManifest.model_validate(read_batch_json(root / "batch_manifest.json"))

    # ---- cross-item structural invariants ----
    if manifest.expected_items != len(manifest.items):
        raise invalid_batch("expected_items must equal the number of items.")
    keys = [item.key for item in manifest.items]
    if len(keys) != len(set(keys)):
        raise invalid_batch("Duplicate item key.")
    package_paths = [item.package_path for item in manifest.items]
    if len(package_paths) != len(set(package_paths)):
        raise invalid_batch("Duplicate package path.")
    recording_names = [item.recording.name for item in manifest.items]
    if len(recording_names) != len(set(recording_names)):
        raise invalid_batch("Duplicate Recording name within the batch.")

    # ---- one bulk Recording query ----
    recording_names = [item.recording.name for item in manifest.items]
    recordings = list(session.scalars(
        select(RecordingModel).where(
            RecordingModel.dataset_name == manifest.dataset.name,
            RecordingModel.dataset_split == manifest.dataset.split,
            RecordingModel.name.in_(recording_names),
        )
    ).all())
    recordings_by_name: dict[str, list[RecordingModel]] = {}
    for recording in recordings:
        recordings_by_name.setdefault(recording.name, []).append(recording)

    # ---- one bulk GroundTruth query ----
    candidate_ids = [recording.id for recording in recordings]
    gt_rows = list(session.scalars(
        select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(candidate_ids))
    ).all()) if candidate_ids else []
    gt_by_recording: dict[str, list[GroundTruthModel]] = {}
    for gt in gt_rows:
        gt_by_recording.setdefault(gt.recording_id, []).append(gt)

    resolved_items: list[ResolvedBatchItem] = []
    resolved_recording_ids: set[str] = set()
    total_detections = 0
    for item in manifest.items:
        candidates = recordings_by_name.get(item.recording.name, [])
        if not candidates:
            raise PlatformError(
                "BATCH_RECORDING_NOT_FOUND",
                f"No local Recording matches dataset+split+name for item {item.key}.",
                422,
                details={"item_key": item.key, "recording_name": item.recording.name},
            )
        if len(candidates) > 1:
            raise PlatformError(
                "BATCH_RECORDING_AMBIGUOUS",
                f"More than one local Recording matches dataset+split+name for item {item.key}.",
                409,
                details={"item_key": item.key, "recording_name": item.recording.name},
            )
        recording = candidates[0]
        if recording.id in resolved_recording_ids:
            raise invalid_batch("Two batch items resolve to the same local Recording.",
                                details={"recording_id": recording.id})
        resolved_recording_ids.add(recording.id)

        manifest_recording = _manifest_recording_for(recording, gt_by_recording.get(recording.id, []))
        local_label_space = recording.label_space if recording.label_space is not None else _SENTINEL_LABEL_SPACE
        local_fingerprint = build_recording_fingerprint(
            manifest.dataset.name, manifest.dataset.split, local_label_space, manifest_recording)

        _verify_fingerprint(item, local_fingerprint, manifest_recording)

        child_root = _safe_path(root, item.package_path)
        if not child_root.is_dir():
            raise invalid_batch("Child package path is not a directory.",
                                details={"item_key": item.key, "recording_name": item.recording.name})
        try:
            validated = validate_extracted_package(child_root, recording, labels)
        except PlatformError as exc:
            raise invalid_batch(
                f"Child package validation failed: {exc.message}",
                details={"item_key": item.key, "recording_name": item.recording.name},
            ) from exc

        _verify_outer_child_consistency(manifest, item, validated)

        total_detections += len(validated.detections)
        resolved_items.append(ResolvedBatchItem(
            item=item, recording=recording, manifest_recording=manifest_recording,
            recording_fingerprint=local_fingerprint, child_root=child_root,
            validated_package=validated,
        ))

    if total_detections > MAX_TOTAL_DETECTIONS:
        raise invalid_batch(f"Total detections exceed the {MAX_TOTAL_DETECTIONS} limit.",
                            details={"total_detections": total_detections})

    local_manifest_hash = None
    if manifest.recording_manifest_hash is not None:
        local_manifest_hash = DatasetBenchmarkService(session).prepare_manifest(
            manifest.dataset.name, manifest.dataset.split, manifest.label_space).recording_manifest_hash
        if local_manifest_hash != manifest.recording_manifest_hash:
            raise PlatformError(
                "DATASET_MANIFEST_MISMATCH",
                "Outer recording manifest hash does not match the local dataset snapshot.",
                409,
                details={"outer": manifest.recording_manifest_hash, "local": local_manifest_hash},
            )

    canonical_items = tuple(
        CanonicalBatchItem(
            key=resolved.item.key,
            recording_fingerprint=resolved.recording_fingerprint.sha256,
            parameters=dict(resolved.validated_package.manifest.parameters),
            detections=resolved.validated_package.detections,
        )
        for resolved in resolved_items
    )
    import_fingerprint = build_batch_import_fingerprint(manifest, canonical_items)

    return ValidatedBatch(
        manifest=manifest,
        items=tuple(resolved_items),
        total_detections=total_detections,
        import_fingerprint=import_fingerprint,
        local_recording_manifest_hash=local_manifest_hash,
    )


def _verify_fingerprint(item: BatchItem, local: RecordingFingerprintValue, manifest_recording: ManifestRecording) -> None:
    wire = item.recording.fingerprint
    if local.sha256 == wire.sha256:
        return
    metadata_mismatches = {}
    wire_meta = wire.metadata
    local_fields = {
        "data_format": manifest_recording.data_format,
        "sample_rate_hz": manifest_recording.sample_rate_hz,
        "center_frequency_hz": manifest_recording.center_frequency_hz,
        "frequency_low_hz": manifest_recording.frequency_low_hz,
        "frequency_high_hz": manifest_recording.frequency_high_hz,
        "num_samples": manifest_recording.num_samples,
        "duration_s": manifest_recording.duration_s,
    }
    for field, wire_value in wire_meta.model_dump().items():
        local_value = local_fields.get(field)
        if isinstance(wire_value, float):
            if canonical_number(wire_value) != canonical_number(float(local_value)):
                metadata_mismatches[field] = {"wire": wire_value, "local": local_value}
        elif wire_value != local_value:
            metadata_mismatches[field] = {"wire": wire_value, "local": local_value}
    details = {"item_key": item.key, "recording_name": item.recording.name}
    if metadata_mismatches:
        details["metadata_mismatches"] = metadata_mismatches
    if local.ground_truth_sha256 != wire.ground_truth_sha256:
        details["ground_truth_mismatch"] = True
    raise PlatformError(
        "RECORDING_FINGERPRINT_MISMATCH",
        "Recording metadata/ground-truth does not match the batch fingerprint.",
        409,
        details=details,
    )


def _verify_outer_child_consistency(manifest: BatchManifest, item: BatchItem, validated: ValidatedAnalysisPackage) -> None:
    child = validated.manifest
    if child.recording.name != item.recording.name:
        raise invalid_batch("Child Recording name does not match the outer item.",
                            details={"item_key": item.key, "recording_name": item.recording.name})
    if child.recording.dataset != manifest.dataset.name:
        raise invalid_batch("Child Recording dataset does not match the outer dataset.",
                            details={"item_key": item.key})
    if child.pipeline.id != manifest.pipeline.id:
        raise invalid_batch("Child pipeline id does not match the outer manifest.",
                            details={"item_key": item.key})
    if child.pipeline.version != manifest.pipeline.version:
        raise invalid_batch("Child pipeline version does not match the outer manifest.",
                            details={"item_key": item.key})
    if child.label_space != manifest.label_space:
        raise invalid_batch("Child label space does not match the outer manifest.",
                            details={"item_key": item.key})