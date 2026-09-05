from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.imported_runs.archive import extract_package
from app.imported_runs.batch_archive import extract_batch_package
from app.imported_runs.batch_schema import BatchImportSummary, BatchRunMapping
from app.imported_runs.batch_validation import ValidatedBatch, validate_batch
from app.imported_runs.factory import build_imported_run_models
from app.labels.service import LabelSpaceService
from app.storage.service import StorageService


def sha256_fileobj(source: BinaryIO) -> str:
    source.seek(0)
    digest = sha256()
    while True:
        block = source.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    source.seek(0)
    return digest.hexdigest()


class BatchPackageImportService:
    def __init__(self, session: Session, storage: StorageService, labels: LabelSpaceService):
        self.session = session
        self.storage = storage
        self.labels = labels

    def import_batch(self, source: BinaryIO) -> BatchImportSummary:
        archive_sha256 = sha256_fileobj(source)
        temp_root = self.storage.data_root / "imports"
        temp_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="batch-staging-", dir=temp_root) as temporary:
            root = extract_batch_package(source, Path(temporary))
            validated = validate_batch(root, self.session, self.labels)
            return self._import_validated(root, validated, archive_sha256)

    def _import_validated(
        self, root: Path, validated: ValidatedBatch, archive_sha256: str
    ) -> BatchImportSummary:
        manifest = validated.manifest
        items = validated.items

        existing = self._find_existing(validated)
        existing_keys = set(existing.keys())
        expected_keys = {resolved.item.key for resolved in items}

        if existing_keys:
            if existing_keys != expected_keys or len(existing) != len(expected_keys):
                raise PlatformError(
                    "BATCH_IMPORT_STATE_INCONSISTENT",
                    "Partial or conflicting prior semantic import state exists.",
                    409,
                )
            # Complete idempotent re-import: return existing mapping, create nothing.
            mapping = [
                BatchRunMapping(
                    recording_id=resolved.recording.id,
                    recording_name=resolved.recording.name,
                    analysis_run_id=existing[resolved.item.key]["run_id"],
                )
                for resolved in items
            ]
            return BatchImportSummary(
                batch_id=manifest.batch_id,
                import_fingerprint=validated.import_fingerprint,
                archive_sha256=archive_sha256,
                dataset_name=manifest.dataset.name,
                dataset_split=manifest.dataset.split,
                pipeline_id=manifest.pipeline.id,
                pipeline_version=manifest.pipeline.version,
                label_space=manifest.label_space,
                item_count=len(items),
                detection_count=validated.total_detections,
                already_imported=True,
                created_runs=0,
                existing_runs=len(existing),
                created_detections=0,
                matched_recordings=len(items),
                missing_recordings=0,
                ambiguous_recordings=0,
                fingerprint_mismatches=0,
                recording_run_mapping=mapping,
            )

        # Build all ORM objects in memory, then commit once.
        runs = []
        detections = []
        mapping = []
        for resolved in items:
            run_id = f"run_{uuid4().hex}"
            detection_ids = [f"det_{uuid4().hex}" for _ in resolved.validated_package.detections]
            built = build_imported_run_models(
                resolved.recording,
                resolved.validated_package,
                run_id=run_id,
                detection_ids=detection_ids,
                batch_import={
                    "schema_version": 1,
                    "batch_id": manifest.batch_id,
                    "item_key": resolved.item.key,
                    "package_path": resolved.item.package_path,
                    "import_fingerprint": validated.import_fingerprint,
                    "recording_fingerprint": resolved.recording_fingerprint.sha256,
                    "archive_sha256": archive_sha256,
                },
            )
            runs.append(built.run)
            detections.extend(built.detections)
            mapping.append(BatchRunMapping(
                recording_id=resolved.recording.id,
                recording_name=resolved.recording.name,
                analysis_run_id=run_id,
            ))

        try:
            self.session.add_all(runs)
            if detections:
                self.session.add_all(detections)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return BatchImportSummary(
            batch_id=manifest.batch_id,
            import_fingerprint=validated.import_fingerprint,
            archive_sha256=archive_sha256,
            dataset_name=manifest.dataset.name,
            dataset_split=manifest.dataset.split,
            pipeline_id=manifest.pipeline.id,
            pipeline_version=manifest.pipeline.version,
            label_space=manifest.label_space,
            item_count=len(items),
            detection_count=validated.total_detections,
            already_imported=False,
            created_runs=len(runs),
            existing_runs=0,
            created_detections=len(detections),
            matched_recordings=len(items),
            missing_recordings=0,
            ambiguous_recordings=0,
            fingerprint_mismatches=0,
            recording_run_mapping=mapping,
        )

    def _find_existing(self, validated: ValidatedBatch) -> dict[str, dict]:
        recording_ids = [resolved.recording.id for resolved in validated.items]
        if not recording_ids:
            return {}
        runs = list(self.session.scalars(
            select(AnalysisRunModel).where(
                AnalysisRunModel.recording_id.in_(recording_ids),
                AnalysisRunModel.pipeline_id == validated.manifest.pipeline.id,
                AnalysisRunModel.pipeline_version == validated.manifest.pipeline.version,
                AnalysisRunModel.executor == "imported",
                AnalysisRunModel.status == "completed",
            )
        ).all())
        by_key: dict[str, dict] = {}
        for run in runs:
            batch_import = (run.parameters_json or {}).get("batch_import") or {}
            if batch_import.get("import_fingerprint") != validated.import_fingerprint:
                continue
            item_key = batch_import.get("item_key")
            if item_key is None:
                continue
            if item_key in by_key:
                raise PlatformError(
                    "BATCH_IMPORT_STATE_INCONSISTENT",
                    "Duplicate item-key mapping in prior semantic import state.",
                    409,
                )
            by_key[item_key] = {"run_id": run.id}
        return by_key