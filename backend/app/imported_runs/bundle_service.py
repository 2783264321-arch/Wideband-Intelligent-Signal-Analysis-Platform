"""Analysis Bundle export/import services.

Export packages the *results* of a completed Dataset Analysis into a portable
ZIP (versioned manifest + per-sample ``detections.json``). Import resolves the
bundle against a locally registered dataset using portable dataset identity and
stable sample fingerprints only — never absolute filesystem paths — and creates
ordinary completed imported ``AnalysisRunModel`` / ``DetectionResultModel`` rows
through the existing imported-run factory. No inference is rerun, and pipeline
availability is not required to view imported results.

Import additionally restores a DURABLE first-class Dataset Analysis identity:
an ordinary ``DatasetExperimentModel`` (executor="imported") plus its Items and
Attempts is created/backfilled around the imported runs, so Dataset → Analyses
lists the imported analysis and the existing detail UI works unchanged. An
optional completed evaluation snapshot is restored through the existing
``DatasetEvaluationModel`` rows when the bundle carries one and the local
preconditions (complete coverage + complete ground truth) hold.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4
import json
import zipfile

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.service import DEFAULT_PHYSICAL_TF_PROTOCOL, resolve_protocol_config
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.datasets.analysis_manifest import build_dataset_analysis_manifest
from app.datasets.identity import fingerprint_for_recordings
from app.ground_truth.model import GroundTruthModel
from app.datasets.model import DatasetModel
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.imported_runs.archive import safe_path
from app.imported_runs.batch_archive import extract_batch_package, read_batch_json
from app.imported_runs.bundle_fingerprint import (
    CanonicalBundleSample,
    build_analysis_bundle_import_fingerprint,
)
from app.imported_runs.bundle_schema import (
    ANALYSIS_BUNDLE_MANIFEST_FILENAME,
    ANALYSIS_BUNDLE_SCHEMA_VERSION,
    AnalysisBundleImportSummary,
    AnalysisBundleManifest,
    BundleAnalysis,
    BundleDataset,
    BundleProvenance,
    BundleRunMapping,
    BundleSample,
)
from app.imported_runs.factory import build_imported_run_models
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.imported_runs.schema import (
    ExecutionMetadata,
    Manifest,
    PackageDetection,
    PipelineMetadata,
    RecordingMetadata,
    ResultPaths,
)
from app.imported_runs.validation import ValidatedAnalysisPackage
from app.recordings.model import RecordingModel

EXPORTER_VERSION = "analysis_bundle_exporter_v1"
COMPLETED_EXPERIMENT_STATUSES = ("completed", "completed_with_failures")
_INTERNAL_LABEL_SPACE_FALLBACK = "__none__"
_RESULT_UNAVAILABLE_ERROR = "ANALYSIS_BUNDLE_RESULT_UNAVAILABLE"


@dataclass(frozen=True)
class _BuiltEvaluation:
    model: DatasetEvaluationModel
    items: list[DatasetEvaluationItemModel]


@dataclass(frozen=True)
class _BuiltImportedExperiment:
    """The durable imported Dataset Analysis plus all rows to persist with it."""

    model: DatasetExperimentModel
    rows: list
    evaluation: DatasetEvaluationModel | None = None


def invalid_bundle(message: str, *, details: dict[str, object] | None = None) -> PlatformError:
    return PlatformError(
        "INVALID_ANALYSIS_BUNDLE",
        message,
        400,
        details={} if details is None else details,
    )


def _matches_real_dataset(portable_fingerprint: str, name: str, split: str, session: Session) -> bool:
    """Whether the bundle's dataset fingerprint belongs to a real local dataset.

    Standalone single-sample exports derive their dataset fingerprint from one
    recording; a dataset fingerprint is derived from its full member list, so
    they only ever coincide for a genuine dataset-member export.
    """
    return session.scalar(
        select(func.count()).select_from(DatasetModel).where(
            DatasetModel.portable_fingerprint == portable_fingerprint,
            DatasetModel.name == name,
            DatasetModel.split == split,
        )
    ) > 0


def _stable_key(recording: RecordingModel) -> str:
    return recording.sample_key or recording.name


def _gt_rows_by_recording(session: Session, recording_ids: list[str]) -> dict[str, list]:
    if not recording_ids:
        return {}
    rows = list(
        session.scalars(
            select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(recording_ids))
        ).all()
    )
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.recording_id, []).append(row)
    return grouped


def _portable_recording_fingerprint(
    dataset_name: str,
    dataset_split: str,
    label_space: str,
    stable_key: str,
    recording: RecordingModel,
    gt_rows: list,
) -> str:
    manifest_recording = ManifestRecording(
        recording_id=recording.id,
        name=stable_key,
        data_format=recording.data_format,
        sample_rate_hz=recording.sample_rate_hz,
        center_frequency_hz=recording.center_frequency_hz,
        frequency_low_hz=recording.frequency_low_hz,
        frequency_high_hz=recording.frequency_high_hz,
        num_samples=recording.num_samples,
        duration_s=recording.duration_s,
        ground_truth=tuple(
            ManifestGroundTruth(
                t_start_s=row.t_start_s,
                t_end_s=row.t_end_s,
                f_low_hz=row.f_low_hz,
                f_high_hz=row.f_high_hz,
                class_id=row.class_id,
                class_name=row.class_name,
            )
            for row in gt_rows
        ),
    )
    return build_recording_fingerprint(
        dataset_name, dataset_split, label_space, manifest_recording
    ).sha256


def _detection_to_package(detection: DetectionResultModel) -> PackageDetection:
    return PackageDetection(
        id=detection.id,
        t_start_s=detection.t_start_s,
        t_end_s=detection.t_end_s,
        f_low_hz=detection.f_low_hz,
        f_high_hz=detection.f_high_hz,
        class_id=detection.class_id,
        class_name=detection.class_name,
        confidence=detection.confidence,
        scores=detection.scores_json,
    )


class AnalysisBundleExportService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def export_experiment(self, experiment_id: str) -> tuple[str, bytes]:
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        if experiment.status not in COMPLETED_EXPERIMENT_STATUSES:
            raise PlatformError(
                "ANALYSIS_BUNDLE_NOT_COMPLETED",
                "Only a completed Dataset Analysis can be exported as an Analysis Bundle.",
                409,
                details={"status": experiment.status},
            )

        items = list(
            self.session.scalars(
                select(DatasetExperimentItemModel)
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
                .order_by(DatasetExperimentItemModel.manifest_order)
            ).all()
        )
        if not items:
            raise PlatformError(
                "ANALYSIS_BUNDLE_NO_RESULTS", "Dataset Analysis has no items to export.", 409
            )
        recordings = {
            recording.id: recording
            for recording in self.session.scalars(
                select(RecordingModel).where(
                    RecordingModel.id.in_([item.recording_id for item in items])
                )
            ).all()
        }
        gt_by_recording = _gt_rows_by_recording(self.session, list(recordings))

        label_space = experiment.dataset_label_space or ""
        portable_fingerprint = self._portable_fingerprint(experiment, recordings)

        exported: list[tuple[BundleSample, tuple[PackageDetection, ...]]] = []
        for index, item in enumerate(items):
            recording = recordings.get(item.recording_id)
            if recording is None:
                continue
            run = self._latest_completed_run(item.id)
            if run is None:
                continue
            detections = tuple(
                _detection_to_package(row)
                for row in self.session.scalars(
                    select(DetectionResultModel)
                    .where(DetectionResultModel.run_id == run.id)
                    .order_by(DetectionResultModel.id)
                ).all()
            )
            stable_key = _stable_key(recording)
            fingerprint = _portable_recording_fingerprint(
                experiment.dataset_name,
                experiment.dataset_split,
                label_space,
                stable_key,
                recording,
                gt_by_recording.get(recording.id, []),
            )
            exported.append((
                BundleSample(
                    key=stable_key,
                    sample_name=recording.name,
                    recording_fingerprint=fingerprint,
                    detection_count=len(detections),
                    detections_path=f"samples/{index:06d}/detections.json",
                ),
                detections,
            ))

        if not exported:
            raise PlatformError(
                "ANALYSIS_BUNDLE_NO_RESULTS",
                "Dataset Analysis has no completed per-sample results to export.",
                409,
            )

        bundle_id = f"bundle_{uuid4().hex[:12]}"
        manifest = AnalysisBundleManifest(
            schema_version=ANALYSIS_BUNDLE_SCHEMA_VERSION,
            bundle_id=bundle_id,
            dataset=BundleDataset(
                name=experiment.dataset_name,
                split=experiment.dataset_split,
                label_space=label_space,
                portable_fingerprint=portable_fingerprint,
            ),
            pipeline=PipelineMetadata(
                id=experiment.plugin_id,
                name=experiment.plugin_id,
                version=experiment.plugin_version,
            ),
            parameters=dict(experiment.parameters_json or {}),
            analysis=BundleAnalysis(
                experiment_id=experiment.id,
                name=experiment.name,
                status=experiment.status,
                executor=experiment.executor,
                evaluation_id=experiment.dataset_evaluation_id,
            ),
            provenance=BundleProvenance(
                exporter_version=EXPORTER_VERSION,
                export_timestamp=datetime.now(timezone.utc).isoformat(),
                evaluation_summary=self._evaluation_summary(experiment),
            ),
            samples=[sample for sample, _ in exported],
        )
        return f"analysis_bundle_{bundle_id}.zip", self._render_zip(manifest, exported)

    def _latest_completed_run(self, item_id: str) -> AnalysisRunModel | None:
        return self.session.scalars(
            select(AnalysisRunModel)
            .join(
                DatasetExperimentAttemptModel,
                DatasetExperimentAttemptModel.analysis_run_id == AnalysisRunModel.id,
            )
            .where(
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                AnalysisRunModel.status == "completed",
            )
            .order_by(DatasetExperimentAttemptModel.attempt_number.desc())
        ).first()

    def export_run(self, run_id: str) -> tuple[str, bytes]:
        """Export ONE completed AnalysisRun as a single-sample Analysis Bundle.

        Same portable shape as the dataset-level export (dataset identity + one
        sample fingerprint + detections), so the importing side needs no special
        handling. Samples keep their dataset provenance when they have one;
        standalone samples build their identity from recording metadata alone.
        """
        run = self.session.get(AnalysisRunModel, run_id)
        if run is None:
            raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
        if run.status != "completed":
            raise PlatformError(
                "ANALYSIS_BUNDLE_NOT_COMPLETED",
                "Only a completed analysis run can be exported as an Analysis Bundle.",
                409,
                details={"status": run.status},
            )
        recording = self.session.get(RecordingModel, run.recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        detections = tuple(
            _detection_to_package(row)
            for row in self.session.scalars(
                select(DetectionResultModel)
                .where(DetectionResultModel.run_id == run.id)
                .order_by(DetectionResultModel.id)
            ).all()
        )
        if not detections:
            raise PlatformError(
                "ANALYSIS_BUNDLE_NO_RESULTS",
                "This analysis run has no detections to export.",
                409,
            )

        dataset = self.session.get(DatasetModel, recording.dataset_id) if recording.dataset_id else None
        dataset_name = dataset.name if dataset is not None else (
            recording.dataset_name or recording.name
        )
        dataset_split = dataset.split if dataset is not None else (
            recording.dataset_split or "standalone"
        )
        label_space = recording.label_space or ""

        stable_key = _stable_key(recording)
        gt_rows = list(
            self.session.scalars(
                select(GroundTruthModel).where(GroundTruthModel.recording_id == recording.id)
            ).all()
        )
        sample_fingerprint = _portable_recording_fingerprint(
            dataset_name,
            dataset_split,
            label_space,
            stable_key,
            recording,
            gt_rows,
        )

        # Standalone recordings have no dataset-authority fingerprint, so the
        # dataset fingerprint is derived from the recording itself. Two machines
        # holding the byte-identical sample still match because paths never enter
        # the fingerprint.
        bundle_id = f"bundle_{uuid4().hex[:12]}"
        manifest = AnalysisBundleManifest(
            schema_version=ANALYSIS_BUNDLE_SCHEMA_VERSION,
            bundle_id=bundle_id,
            dataset=BundleDataset(
                name=dataset_name,
                split=dataset_split,
                label_space=label_space or "",
                # For a dataset member this equals the dataset fingerprint
                # computed from its members, so the importing machine binds the
                # result to the same Dataset authority. For a standalone sample it
                # identifies the individual capture instead.
                portable_fingerprint=(
                    dataset.portable_fingerprint
                    if dataset is not None and dataset.portable_fingerprint
                    else fingerprint_for_recordings(
                        self.session,
                        name=dataset_name,
                        split=dataset_split,
                        label_space=label_space or None,
                        recordings=[recording],
                    )
                ),
            ),
            pipeline=PipelineMetadata(id=run.pipeline_id, name=run.pipeline_id, version=run.pipeline_version),
            parameters=dict(run.parameters_json or {}),
            analysis=BundleAnalysis(
                experiment_id=run.id,
                name=run.id,
                status=run.status,
                executor=run.executor,
                evaluation_id=None,
            ),
            provenance=BundleProvenance(
                exporter_version=EXPORTER_VERSION,
                export_timestamp=datetime.now(timezone.utc).isoformat(),
                evaluation_summary=None,
                single_sample_export=True,
            ),
            samples=[
                BundleSample(
                    key=stable_key,
                    sample_name=recording.name,
                    recording_fingerprint=sample_fingerprint,
                    detection_count=len(detections),
                    detections_path="samples/000000/detections.json",
                )
            ],
        )
        filename = f"analysis_bundle_{bundle_id}.zip"
        return filename, self._render_zip(manifest, [(manifest.samples[0], detections)])


    def _portable_fingerprint(
        self, experiment: DatasetExperimentModel, recordings: dict[str, RecordingModel]
    ) -> str:
        if experiment.dataset_id is not None:
            dataset = self.session.get(DatasetModel, experiment.dataset_id)
            if dataset is not None:
                if dataset.portable_fingerprint:
                    return dataset.portable_fingerprint
                members = list(
                    self.session.scalars(
                        select(RecordingModel).where(RecordingModel.dataset_id == dataset.id)
                    ).all()
                )
                return fingerprint_for_recordings(
                    self.session,
                    name=dataset.name,
                    split=dataset.split,
                    label_space=dataset.label_space,
                    recordings=members,
                )
        return fingerprint_for_recordings(
            self.session,
            name=experiment.dataset_name,
            split=experiment.dataset_split,
            label_space=experiment.dataset_label_space or None,
            recordings=list(recordings.values()),
        )

    def _evaluation_summary(self, experiment: DatasetExperimentModel) -> dict | None:
        if not experiment.dataset_evaluation_id:
            return None
        evaluation = self.session.get(DatasetEvaluationModel, experiment.dataset_evaluation_id)
        if evaluation is None:
            return None
        return {
            "protocol": evaluation.evaluation_protocol,
            "status": evaluation.status,
            "coverage": evaluation.coverage,
            "comparable": evaluation.comparable,
            "evaluated_recordings": evaluation.evaluated_recordings,
            "expected_recordings": evaluation.expected_recordings,
            "aggregate_metrics": dict(evaluation.aggregate_metrics_json or {}),
            "per_class_metrics": list(evaluation.per_class_metrics_json or []),
            "confusion": list(evaluation.confusion_json or []),
        }

    def _render_zip(
        self,
        manifest: AnalysisBundleManifest,
        exported: list[tuple[BundleSample, tuple[PackageDetection, ...]]],
    ) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                ANALYSIS_BUNDLE_MANIFEST_FILENAME,
                json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
            )
            for sample, detections in exported:
                archive.writestr(
                    sample.detections_path,
                    json.dumps(
                        {"detections": [detection.model_dump(mode="json") for detection in detections]},
                        sort_keys=True,
                    ),
                )
        return buffer.getvalue()


class AnalysisBundleImportService:
    def __init__(self, session: Session, storage) -> None:
        self.session = session
        self.storage = storage

    def import_bundle(self, source: BinaryIO) -> AnalysisBundleImportSummary:
        archive_sha256 = _sha256_fileobj(source)
        temp_root = self.storage.data_root / "imports"
        temp_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="bundle-staging-", dir=temp_root) as temporary:
            root = extract_batch_package(
                source,
                Path(temporary),
                manifest_filename=ANALYSIS_BUNDLE_MANIFEST_FILENAME,
            )
            manifest = self._read_manifest(root)
            validated = self._validate_samples(root, manifest)
            if (
                len(validated) == 1
                and manifest.provenance.single_sample_export
                and not _matches_real_dataset(
                    manifest.dataset.portable_fingerprint,
                    manifest.dataset.name,
                    manifest.dataset.split,
                    self.session,
                )
            ):
                return self._import_standalone(manifest, validated, archive_sha256)
            return self._import_validated(manifest, validated, archive_sha256)

    def _import_standalone(
        self,
        manifest: AnalysisBundleManifest,
        validated: list[tuple[BundleSample, tuple[PackageDetection, ...]]],
        archive_sha256: str,
    ) -> AnalysisBundleImportSummary:
        """Import a platform-exported single-sample run for a standalone sample.

        Matched purely by recording fingerprint — no dataset authority exists on
        either side, so no Dataset Experiment shell is fabricated and no
        evaluation is restored. Failure modes stay fail-closed.
        """
        dataset_name = manifest.dataset.name
        dataset_split = manifest.dataset.split
        label_space = manifest.dataset.label_space
        sample, detections = validated[0]
        if manifest.analysis.status != "completed":
            raise invalid_bundle(
                "Analysis Bundle analysis status is not importable.",
                details={"status": manifest.analysis.status},
            )

        # Candidates: standalone recordings with a compatible label space.
        candidates = list(
            self.session.scalars(
                select(RecordingModel).where(RecordingModel.label_space == label_space)
            ).all()
        )
        matches = []
        for recording in candidates:
            if recording.dataset_id is not None:
                continue
            local_fingerprint = _portable_recording_fingerprint(
                dataset_name,
                dataset_split,
                label_space,
                _stable_key(recording),
                recording,
                list(
                    self.session.scalars(
                        select(GroundTruthModel).where(
                            GroundTruthModel.recording_id == recording.id
                        )
                    ).all()
                ),
            )
            if local_fingerprint == sample.recording_fingerprint:
                matches.append(recording)
        if not matches:
            raise PlatformError(
                "ANALYSIS_BUNDLE_SAMPLE_NOT_FOUND",
                "No local standalone sample matches this bundle sample's fingerprint.",
                422,
                details={"sample_key": sample.key, "sample_name": sample.sample_name},
            )
        if len(matches) > 1:
            raise PlatformError(
                "ANALYSIS_BUNDLE_SAMPLE_AMBIGUOUS",
                "More than one local standalone sample matches this bundle sample.",
                409,
                details={"sample_key": sample.key, "sample_name": sample.sample_name},
            )
        recording = matches[0]

        canonical_samples = (
            CanonicalBundleSample(
                key=sample.key,
                recording_fingerprint=sample.recording_fingerprint,
                detections=detections,
            ),
        )
        import_fingerprint = build_analysis_bundle_import_fingerprint(manifest, canonical_samples)

        existing = self._find_existing(manifest, import_fingerprint, [(sample, recording, detections)])
        run_ids_by_key: dict[str, str] = {}
        created_runs: list[AnalysisRunModel] = []
        detections_to_add: list[DetectionResultModel] = []
        if existing:
            run_ids_by_key[sample.key] = existing[sample.key]["run_id"]
        else:
            internal_label_space = label_space or _INTERNAL_LABEL_SPACE_FALLBACK
            run_id = f"run_{uuid4().hex}"
            detection_ids = [f"det_{uuid4().hex}" for _ in detections]
            package_manifest = Manifest(
                schema_version=1,
                pipeline=manifest.pipeline,
                label_space=internal_label_space,
                recording=RecordingMetadata(name=sample.key, dataset=dataset_name),
                execution=ExecutionMetadata(executor="imported", device=None, environment=None),
                results=ResultPaths(detections="detections.json"),
                parameters=dict(manifest.parameters),
            )
            built = build_imported_run_models(
                recording,
                ValidatedAnalysisPackage(manifest=package_manifest, detections=tuple(detections)),
                run_id=run_id,
                detection_ids=detection_ids,
            )
            built.run.parameters_json = {
                **built.run.parameters_json,
                "analysis_bundle": {
                    "schema_version": ANALYSIS_BUNDLE_SCHEMA_VERSION,
                    "bundle_id": manifest.bundle_id,
                    "item_key": sample.key,
                    "import_fingerprint": import_fingerprint,
                    "recording_fingerprint": sample.recording_fingerprint,
                    "archive_sha256": archive_sha256,
                    "dataset_portable_fingerprint": manifest.dataset.portable_fingerprint,
                    "pipeline_id": manifest.pipeline.id,
                    "pipeline_version": manifest.pipeline.version,
                    "standalone": True,
                },
            }
            created_runs.append(built.run)
            detections_to_add.extend(built.detections)
            run_ids_by_key[sample.key] = run_id

        mapping = [
            BundleRunMapping(
                sample_key=sample.key,
                sample_name=sample.sample_name,
                recording_id=recording.id,
                analysis_run_id=run_ids_by_key[sample.key],
            )
        ]
        try:
            self.session.add_all(created_runs)
            if detections_to_add:
                self.session.add_all(detections_to_add)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return self._summary(
            manifest,
            import_fingerprint,
            archive_sha256,
            dataset_id=None,
            already_imported=bool(existing),
            created_runs=len(created_runs),
            existing_runs=len(run_ids_by_key) - len(created_runs),
            created_detections=len(detections_to_add),
            dataset_analysis_id=None,
            mapping=mapping,
        )

    def _read_manifest(self, root: Path) -> AnalysisBundleManifest:
        try:
            return AnalysisBundleManifest.model_validate(
                read_batch_json(root / ANALYSIS_BUNDLE_MANIFEST_FILENAME)
            )
        except ValidationError as exc:
            raise invalid_bundle("Analysis Bundle manifest schema is invalid.") from exc

    def _validate_samples(
        self, root: Path, manifest: AnalysisBundleManifest
    ) -> list[tuple[BundleSample, tuple[PackageDetection, ...]]]:
        keys = [sample.key for sample in manifest.samples]
        if len(keys) != len(set(keys)):
            raise invalid_bundle("Duplicate sample key in Analysis Bundle.")
        paths = [sample.detections_path for sample in manifest.samples]
        if len(paths) != len(set(paths)):
            raise invalid_bundle("Duplicate detections path in Analysis Bundle.")
        adapter = TypeAdapter(list[PackageDetection])
        validated: list[tuple[BundleSample, tuple[PackageDetection, ...]]] = []
        for sample in manifest.samples:
            if not sample.detections_path.startswith("samples/"):
                raise invalid_bundle("Sample detections must live under the samples/ directory.")
            try:
                target = safe_path(root, sample.detections_path)
            except PlatformError as exc:
                raise invalid_bundle(exc.message) from exc
            if not target.is_file():
                raise invalid_bundle(
                    "Sample detections file is missing.", details={"sample_key": sample.key}
                )
            document = read_batch_json(target)
            if not isinstance(document, dict) or not isinstance(document.get("detections"), list):
                raise invalid_bundle(
                    "Sample detections.json must contain a 'detections' array.",
                    details={"sample_key": sample.key},
                )
            try:
                detections = tuple(adapter.validate_python(document["detections"]))
            except ValidationError as exc:
                raise invalid_bundle(
                    "Sample detections are invalid.", details={"sample_key": sample.key}
                ) from exc
            if len(detections) != sample.detection_count:
                raise invalid_bundle(
                    "Sample detection_count does not match detections.json.",
                    details={"sample_key": sample.key},
                )
            validated.append((sample, detections))
        return validated

    def _import_validated(
        self,
        manifest: AnalysisBundleManifest,
        validated: list[tuple[BundleSample, tuple[PackageDetection, ...]]],
        archive_sha256: str,
    ) -> AnalysisBundleImportSummary:
        dataset_name = manifest.dataset.name
        dataset_split = manifest.dataset.split
        label_space = manifest.dataset.label_space
        fingerprint = manifest.dataset.portable_fingerprint

        candidate_datasets = list(
            self.session.scalars(
                select(DatasetModel).where(DatasetModel.portable_fingerprint == fingerprint)
            ).all()
        )
        if not candidate_datasets:
            raise PlatformError(
                "ANALYSIS_BUNDLE_DATASET_MISMATCH",
                "No local dataset matches this bundle's portable dataset identity.",
                409,
                details={
                    "portable_fingerprint": fingerprint,
                    "dataset_name": dataset_name,
                    "dataset_split": dataset_split,
                },
            )

        candidate_ids = [dataset.id for dataset in candidate_datasets]
        recordings = list(
            self.session.scalars(
                select(RecordingModel).where(RecordingModel.dataset_id.in_(candidate_ids))
            ).all()
        )
        gt_by_recording = _gt_rows_by_recording(self.session, [rec.id for rec in recordings])

        resolved: list[tuple[BundleSample, RecordingModel, tuple[PackageDetection, ...]]] = []
        used: set[str] = set()
        for sample, detections in validated:
            matches = []
            for recording in recordings:
                if recording.id in used:
                    continue
                stable_key = _stable_key(recording)
                if stable_key != sample.key and recording.name != sample.sample_name:
                    continue
                local_fingerprint = _portable_recording_fingerprint(
                    dataset_name,
                    dataset_split,
                    label_space,
                    stable_key,
                    recording,
                    gt_by_recording.get(recording.id, []),
                )
                if local_fingerprint == sample.recording_fingerprint:
                    matches.append(recording)
            if not matches:
                raise PlatformError(
                    "ANALYSIS_BUNDLE_SAMPLE_NOT_FOUND",
                    "No local sample matches this bundle sample's stable identity/fingerprint.",
                    422,
                    details={"sample_key": sample.key, "sample_name": sample.sample_name},
                )
            if len(matches) > 1:
                raise PlatformError(
                    "ANALYSIS_BUNDLE_SAMPLE_AMBIGUOUS",
                    "More than one local sample matches this bundle sample.",
                    409,
                    details={"sample_key": sample.key, "sample_name": sample.sample_name},
                )
            recording = matches[0]
            used.add(recording.id)
            resolved.append((sample, recording, detections))

        canonical_samples = tuple(
            CanonicalBundleSample(
                key=sample.key,
                recording_fingerprint=sample.recording_fingerprint,
                detections=detections,
            )
            for sample, _, detections in resolved
        )
        import_fingerprint = build_analysis_bundle_import_fingerprint(manifest, canonical_samples)

        # A durable imported Dataset Analysis is anchored to exactly one local
        # Dataset authority; matched samples spanning several datasets is a
        # semantic inconsistency (per-sample ambiguity is rejected above).
        dataset = self._resolve_target_dataset(resolved)
        local_manifest = build_dataset_analysis_manifest(self.session, dataset.id)
        member_ids = {entry.recording_id for entry in local_manifest.entries}
        resolved_ids = {recording.id for _, recording, _ in resolved}
        if not resolved_ids <= member_ids:
            raise PlatformError(
                "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                "Matched samples are not members of the local Dataset authority.",
                409,
                details={"dataset_id": dataset.id},
            )
        if manifest.analysis.status not in ("completed", "completed_with_failures"):
            raise invalid_bundle(
                "Analysis Bundle analysis status is not importable.",
                details={"status": manifest.analysis.status},
            )

        # A platform-marked single-sample export IS one completed run of a
        # dataset analysis: accept it as a partial result set instead of
        # demanding full Dataset Analysis coverage. User-crafted bundles lack the
        # marker and still fail closed.
        is_partial_single_sample = (
            len(resolved) == 1
            and manifest.provenance.single_sample_export
            and _matches_real_dataset(
                manifest.dataset.portable_fingerprint, dataset_name, dataset_split, self.session
            )
        )
        if manifest.analysis.status == "completed" and is_partial_single_sample:
            manifest = manifest.model_copy(deep=True)
            manifest.analysis.status = "completed_with_failures"
        if manifest.analysis.status == "completed" and not is_partial_single_sample and resolved_ids != member_ids:
            missing = sorted(member_ids - resolved_ids)
            raise PlatformError(
                "ANALYSIS_BUNDLE_INCOMPLETE_FOR_COMPLETED",
                "The bundle claims a complete analysis but does not cover the "
                "full current Dataset Analysis manifest.",
                409,
                details={
                    "dataset_id": dataset.id,
                    "expected_samples": len(member_ids),
                    "bundle_samples": len(resolved_ids),
                    "missing_recording_ids": missing[:20],
                },
            )

        existing = self._find_existing(manifest, import_fingerprint, resolved)
        expected_keys = {sample.key for sample, _, _ in resolved}
        run_ids_by_key: dict[str, str] = {}
        created_runs: list[AnalysisRunModel] = []
        detections_to_add: list[DetectionResultModel] = []
        if existing:
            if set(existing.keys()) != expected_keys or len(existing) != len(expected_keys):
                raise PlatformError(
                    "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                    "Partial or conflicting prior Analysis Bundle import state exists.",
                    409,
                )
            for sample, recording, _ in resolved:
                if existing[sample.key]["recording_id"] != recording.id:
                    raise PlatformError(
                        "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                        "Prior Analysis Bundle import maps a sample to a different Recording.",
                        409,
                    )
                run_ids_by_key[sample.key] = existing[sample.key]["run_id"]
        else:
            internal_label_space = label_space or _INTERNAL_LABEL_SPACE_FALLBACK
            for sample, recording, detections in resolved:
                run_id = f"run_{uuid4().hex}"
                detection_ids = [f"det_{uuid4().hex}" for _ in detections]
                package_manifest = Manifest(
                    schema_version=1,
                    pipeline=manifest.pipeline,
                    label_space=internal_label_space,
                    recording=RecordingMetadata(name=sample.key, dataset=dataset_name),
                    execution=ExecutionMetadata(executor="imported", device=None, environment=None),
                    results=ResultPaths(detections="detections.json"),
                    parameters=dict(manifest.parameters),
                )
                built = build_imported_run_models(
                    recording,
                    ValidatedAnalysisPackage(manifest=package_manifest, detections=tuple(detections)),
                    run_id=run_id,
                    detection_ids=detection_ids,
                )
                built.run.parameters_json = {
                    **built.run.parameters_json,
                    "analysis_bundle": {
                        "schema_version": ANALYSIS_BUNDLE_SCHEMA_VERSION,
                        "bundle_id": manifest.bundle_id,
                        "item_key": sample.key,
                        "import_fingerprint": import_fingerprint,
                        "recording_fingerprint": sample.recording_fingerprint,
                        "archive_sha256": archive_sha256,
                        "dataset_portable_fingerprint": fingerprint,
                        "pipeline_id": manifest.pipeline.id,
                        "pipeline_version": manifest.pipeline.version,
                    },
                }
                created_runs.append(built.run)
                detections_to_add.extend(built.detections)
                run_ids_by_key[sample.key] = run_id

        mapping = [
            BundleRunMapping(
                sample_key=sample.key,
                sample_name=sample.sample_name,
                recording_id=recording.id,
                analysis_run_id=run_ids_by_key[sample.key],
            )
            for sample, recording, _ in resolved
        ]

        experiment = self._ensure_imported_experiment(
            dataset=dataset,
            manifest=manifest,
            local_manifest=local_manifest,
            import_fingerprint=import_fingerprint,
            archive_sha256=archive_sha256,
            mapping=mapping,
            resolved=dict(zip((recording.id for _, recording, _ in resolved), resolved)),
        )

        try:
            self.session.add_all(created_runs)
            if detections_to_add:
                self.session.add_all(detections_to_add)
            self.session.add_all(experiment.rows)
            if experiment.evaluation is not None:
                self.session.add(experiment.evaluation)
            self.session.add(experiment.model)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return self._summary(
            manifest,
            import_fingerprint,
            archive_sha256,
            dataset_id=dataset.id,
            already_imported=bool(existing),
            created_runs=len(created_runs),
            existing_runs=len(run_ids_by_key) - len(created_runs),
            created_detections=len(detections_to_add),
            dataset_analysis_id=experiment.model.id,
            mapping=mapping,
        )

    def _resolve_target_dataset(
        self, resolved: list[tuple[BundleSample, RecordingModel, tuple[PackageDetection, ...]]]
    ) -> DatasetModel:
        dataset_ids = {recording.dataset_id for _, recording, _ in resolved}
        if len(dataset_ids) != 1:
            raise PlatformError(
                "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                "Matched samples resolve to more than one local Dataset.",
                409,
                details={"dataset_ids": sorted(str(value) for value in dataset_ids)},
            )
        dataset = self.session.get(DatasetModel, dataset_ids.pop())
        if dataset is None:
            raise PlatformError(
                "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                "The matched local Dataset no longer exists.",
                409,
            )
        return dataset

    def _find_existing_experiment(self, import_fingerprint: str) -> DatasetExperimentModel | None:
        candidates = list(
            self.session.scalars(
                select(DatasetExperimentModel).where(DatasetExperimentModel.executor == "imported")
            ).all()
        )
        found = [
            candidate
            for candidate in candidates
            if ((candidate.runtime_descriptor_json or {}).get("analysis_bundle") or {})
            .get("import_fingerprint")
            == import_fingerprint
        ]
        if not found:
            return None
        if len(found) > 1:
            raise PlatformError(
                "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                "Duplicate imported Dataset Analysis exists for this bundle fingerprint.",
                409,
            )
        return found[0]

    def _ensure_imported_experiment(
        self,
        *,
        dataset: DatasetModel,
        manifest: AnalysisBundleManifest,
        local_manifest,
        import_fingerprint: str,
        archive_sha256: str,
        mapping: list[BundleRunMapping],
        resolved: dict[str, tuple[BundleSample, RecordingModel, tuple[PackageDetection, ...]]],
    ) -> "_BuiltImportedExperiment":
        existing = self._find_existing_experiment(import_fingerprint)
        if existing is not None:
            if existing.dataset_id != dataset.id:
                raise PlatformError(
                    "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                    "Prior imported Dataset Analysis is bound to a different local Dataset.",
                    409,
                )
            return _BuiltImportedExperiment(model=existing, rows=[])

        now = datetime.now(timezone.utc)
        order_by_recording = {entry.recording_id: entry for entry in local_manifest.entries}
        run_by_recording = {row.recording_id: row.analysis_run_id for row in mapping}
        protocol = self._resolve_import_protocol(manifest)

        experiment = DatasetExperimentModel(
            id=f"exp_{uuid4().hex}",
            name=f"{manifest.analysis.name} (imported)",
            dataset_name=dataset.name,
            dataset_split=dataset.split,
            dataset_label_space=dataset.label_space or "",
            dataset_id=dataset.id,
            recording_manifest_hash=local_manifest.recording_manifest_hash,
            plugin_id=manifest.pipeline.id,
            plugin_version=manifest.pipeline.version,
            parameters_json=dict(manifest.parameters),
            executor="imported",
            runtime_descriptor_json={
                "source": "analysis_bundle",
                "analysis_bundle": {
                    "bundle_id": manifest.bundle_id,
                    "import_fingerprint": import_fingerprint,
                    "archive_sha256": archive_sha256,
                },
            },
            evaluation_protocol=protocol,
            max_concurrency=1,
            status=manifest.analysis.status,
            created_at=now,
            completed_at=now,
        )
        rows: list = []
        for entry in local_manifest.entries:
            run_id = run_by_recording.get(entry.recording_id)
            item = DatasetExperimentItemModel(
                id=f"expitem_{uuid4().hex}",
                experiment_id=experiment.id,
                manifest_order=entry.manifest_order,
                recording_id=entry.recording_id,
                status="completed" if run_id is not None else "failed",
            )
            if run_id is None:
                item.last_error_type = _RESULT_UNAVAILABLE_ERROR
                item.last_error_message = (
                    "No result for this sample was included in the imported Analysis Bundle."
                )
            rows.append(item)
            if run_id is not None:
                rows.append(DatasetExperimentAttemptModel(
                    id=f"expattempt_{uuid4().hex}",
                    experiment_item_id=item.id,
                    attempt_number=1,
                    analysis_run_id=run_id,
                ))

        evaluation = self._build_restored_evaluation(
            dataset=dataset,
            manifest=manifest,
            local_manifest=local_manifest,
            protocol=protocol,
            mapping=mapping,
            resolved=resolved,
        )
        if evaluation is not None:
            rows.extend(evaluation.items)
            experiment.dataset_evaluation_id = evaluation.model.id

        return _BuiltImportedExperiment(
            model=experiment, rows=rows, evaluation=evaluation.model if evaluation else None
        )

    def _resolve_import_protocol(self, manifest: AnalysisBundleManifest) -> str:
        summary = manifest.provenance.evaluation_summary
        protocol = summary.get("protocol") if isinstance(summary, dict) else None
        if isinstance(protocol, str) and protocol:
            try:
                resolve_protocol_config(protocol)
                return protocol
            except PlatformError:
                pass
        return DEFAULT_PHYSICAL_TF_PROTOCOL

    def _build_restored_evaluation(
        self,
        *,
        dataset: DatasetModel,
        manifest: AnalysisBundleManifest,
        local_manifest,
        protocol: str,
        mapping: list[BundleRunMapping],
        resolved: dict[str, tuple[BundleSample, RecordingModel, tuple[PackageDetection, ...]]],
    ) -> "_BuiltEvaluation | None":
        """Restore a completed evaluation snapshot from the bundle, when safe.

        Any reconstruction problem leaves the evaluation unlinked: importing the
        analysis results itself must never fail because of the optional snapshot.
        """
        try:
            summary = manifest.provenance.evaluation_summary
            if not isinstance(summary, dict) or summary.get("status") != "completed":
                return None
            entries = local_manifest.entries
            if len(mapping) != len(entries):
                return None  # partial result set: no honest complete evaluation
            if any(entry.gt_count == 0 for entry in entries):
                return None  # target dataset lacks complete ground truth
            aggregate = summary.get("aggregate_metrics")
            if not isinstance(aggregate, dict):
                return None
            resolve_protocol_config(protocol)
            now = datetime.now(timezone.utc)
            run_by_recording = {row.recording_id: row.analysis_run_id for row in mapping}
            evaluation = DatasetEvaluationModel(
                id=f"eval_{uuid4().hex}",
                name=f"{manifest.analysis.name} (imported)",
                dataset_name=dataset.name,
                dataset_split=dataset.split,
                label_space=dataset.label_space or "",
                dataset_id=dataset.id,
                dataset_projection_id=None,
                pipeline_id=manifest.pipeline.id,
                pipeline_version=manifest.pipeline.version,
                status="completed",
                expected_recordings=len(entries),
                evaluated_recordings=len(mapping),
                missing_recordings=0,
                coverage=1.0,
                comparable=True,
                recording_manifest_hash=local_manifest.recording_manifest_hash,
                evaluation_protocol=protocol,
                protocol_config_json=resolve_protocol_config(protocol),
                aggregate_metrics_json=aggregate,
                per_class_metrics_json=(
                    list(summary["per_class_metrics"])
                    if isinstance(summary.get("per_class_metrics"), list)
                    else None
                ),
                confusion_json=(
                    list(summary["confusion"])
                    if isinstance(summary.get("confusion"), list)
                    else None
                ),
                created_at=now,
                completed_at=now,
            )
            items = []
            for entry in entries:
                run_id = run_by_recording.get(entry.recording_id)
                detections = resolved[entry.recording_id][2] if run_id else ()
                items.append(DatasetEvaluationItemModel(
                    id=f"evalitem_{uuid4().hex}",
                    evaluation_id=evaluation.id,
                    manifest_order=entry.manifest_order,
                    recording_id=entry.recording_id,
                    analysis_run_id=run_id,
                    status="completed" if run_id is not None else "missing",
                    gt_count=entry.gt_count,
                    prediction_count=len(detections),
                ))
            return _BuiltEvaluation(model=evaluation, items=items)
        except Exception:
            self.session.rollback()
            return None

    def _find_existing(self, manifest, import_fingerprint, resolved) -> dict[str, dict]:
        recording_ids = [recording.id for _, recording, _ in resolved]
        if not recording_ids:
            return {}
        runs = list(
            self.session.scalars(
                select(AnalysisRunModel).where(
                    AnalysisRunModel.recording_id.in_(recording_ids),
                    AnalysisRunModel.pipeline_id == manifest.pipeline.id,
                    AnalysisRunModel.pipeline_version == manifest.pipeline.version,
                    AnalysisRunModel.executor == "imported",
                    AnalysisRunModel.status == "completed",
                )
            ).all()
        )
        by_key: dict[str, dict] = {}
        for run in runs:
            meta = (run.parameters_json or {}).get("analysis_bundle") or {}
            if meta.get("import_fingerprint") != import_fingerprint:
                continue
            item_key = meta.get("item_key")
            if item_key is None:
                continue
            if item_key in by_key:
                raise PlatformError(
                    "ANALYSIS_BUNDLE_STATE_INCONSISTENT",
                    "Duplicate sample-key mapping in prior Analysis Bundle import state.",
                    409,
                )
            by_key[item_key] = {"run_id": run.id, "recording_id": run.recording_id}
        return by_key

    def _summary(
        self,
        manifest: AnalysisBundleManifest,
        import_fingerprint: str,
        archive_sha256: str,
        *,
        dataset_id: str | None,
        already_imported: bool,
        created_runs: int,
        existing_runs: int,
        created_detections: int,
        mapping: list[BundleRunMapping],
        dataset_analysis_id: str | None = None,
    ) -> AnalysisBundleImportSummary:
        return AnalysisBundleImportSummary(
            schema_version=ANALYSIS_BUNDLE_SCHEMA_VERSION,
            bundle_id=manifest.bundle_id,
            import_fingerprint=import_fingerprint,
            archive_sha256=archive_sha256,
            dataset_id=dataset_id,
            dataset_name=manifest.dataset.name,
            dataset_split=manifest.dataset.split,
            pipeline_id=manifest.pipeline.id,
            pipeline_version=manifest.pipeline.version,
            label_space=manifest.dataset.label_space,
            sample_count=len(mapping),
            detection_count=sum(sample.detection_count for sample in manifest.samples),
            already_imported=already_imported,
            created_runs=created_runs,
            existing_runs=existing_runs,
            created_detections=created_detections,
            dataset_analysis_id=dataset_analysis_id,
            sample_run_mapping=mapping,
        )


def _sha256_fileobj(source: BinaryIO) -> str:
    source.seek(0)
    digest = sha256()
    while True:
        block = source.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    source.seek(0)
    return digest.hexdigest()
