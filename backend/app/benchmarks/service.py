from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import uuid4

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.benchmarks.manifest import (
    FrozenRecordingManifest,
    ManifestGroundTruth,
    ManifestRecording,
    build_recording_manifest,
)
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.protocol import count_ground_truths_for_protocol
from app.benchmarks.schema import (
    DEFAULT_PHYSICAL_TF_PROTOCOL,
    PHYSICAL_TF_PROTOCOL_V1,
    PHYSICAL_TF_PROTOCOL_V2,
    PROTOCOL_CONFIG_V1,
    PROTOCOL_CONFIG_V2,
)
from app.core.errors import PlatformError
from app.datasets.projection import DatasetProjectionResolver
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


@dataclass(frozen=True)
class ManifestPreview:
    recording_manifest_hash: str
    expected_recordings: int
    entries: tuple[dict, ...]


def mark_stale_running_evaluations_interrupted(session: Session) -> int:
    statement = (
        update(DatasetEvaluationModel)
        .where(DatasetEvaluationModel.status == "running")
        .values(
            status="interrupted",
            error_type="BENCHMARK_INTERRUPTED",
            error_message="Previous local benchmark process ended before platform restart.",
            completed_at=datetime.now(timezone.utc),
        )
    )
    result = session.execute(statement)
    session.commit()
    return int(result.rowcount or 0)


def _protocol_config_for(protocol: str) -> dict:
    if protocol == PHYSICAL_TF_PROTOCOL_V1:
        return deepcopy(PROTOCOL_CONFIG_V1)
    if protocol == PHYSICAL_TF_PROTOCOL_V2:
        return deepcopy(PROTOCOL_CONFIG_V2)
    raise PlatformError("UNSUPPORTED_EVALUATION_PROTOCOL", f"Unsupported evaluation protocol: {protocol}", 422)


def resolve_protocol_config(evaluation_protocol: str) -> dict:
    """Public read seam over the frozen supported evaluation-protocol set.

    Reuses ``_protocol_config_for`` so the supported-protocol authority stays
    singular; raises ``UNSUPPORTED_EVALUATION_PROTOCOL`` for unknown values.
    """
    return _protocol_config_for(evaluation_protocol)


@dataclass(frozen=True)
class RunResolutionPreview:
    recording_manifest_hash: str
    entries: tuple[dict, ...]


@dataclass(frozen=True)
class ManifestEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    gt_count: int


@dataclass(frozen=True)
class DatasetEvaluationItemView:
    id: str
    evaluation_id: str
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str | None
    status: str
    gt_count: int
    prediction_count: int
    error_reason: str | None


@dataclass(frozen=True)
class RunResolutionEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    resolution: str
    candidate_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImportedBatchCatalogEntry:
    import_fingerprint: str
    pipeline_id: str | None
    pipeline_version: str | None
    dataset_name: str | None
    dataset_split: str | None
    label_space: str | None
    run_count: int
    detection_count: int
    archive_sha256: str | None
    result_provenance: dict
    transport_provenance: dict
    ready: bool
    inconsistency_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImportedBatchResolutionEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str
    item_key: str


@dataclass(frozen=True)
class ImportedBatchResolutionPreview:
    import_fingerprint: str
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline_id: str
    pipeline_version: str
    recording_manifest_hash: str
    expected_recordings: int
    resolved_recordings: int
    missing_recordings: int
    conflict_count: int
    entries: tuple[ImportedBatchResolutionEntry, ...]
    dataset_projection_id: str | None = None


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _batch_import_payload(run: AnalysisRunModel) -> dict:
    payload = (run.parameters_json or {}).get("batch_import")
    return payload if isinstance(payload, dict) else {}


def _only_or_none(values):
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


class DatasetBenchmarkService:
    def __init__(self, session: Session):
        self.session = session

    # ---------- manifest preparation ----------

    def _load_recording_manifests(self, dataset_name: str, dataset_split: str, label_space: str) -> list[ManifestRecording]:
        recordings = list(
            self.session.scalars(
                select(RecordingModel)
                .where(
                    RecordingModel.dataset_name == dataset_name,
                    RecordingModel.dataset_split == dataset_split,
                    RecordingModel.label_space == label_space,
                    RecordingModel.has_ground_truth.is_(True),
                )
                .order_by(RecordingModel.name)
            ).all()
        )
        if not recordings:
            raise PlatformError("DATASET_SNAPSHOT_EMPTY", "No Ground-Truth-bearing Recordings match the dataset selection.", 422)
        return self._to_manifest_recordings(recordings)

    def _to_manifest_recordings(self, recordings: list[RecordingModel]) -> list[ManifestRecording]:
        if not recordings:
            raise PlatformError("DATASET_SNAPSHOT_EMPTY", "No Ground-Truth-bearing Recordings match the dataset selection.", 422)
        recording_ids = [recording.id for recording in recordings]
        gt_rows = list(
            self.session.scalars(
                select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(recording_ids))
            ).all()
        )
        gt_by_recording: dict[str, list[GroundTruthModel]] = {}
        for gt in gt_rows:
            gt_by_recording.setdefault(gt.recording_id, []).append(gt)
        manifests = []
        for recording in recordings:
            gts = tuple(
                ManifestGroundTruth(
                    t_start_s=gt.t_start_s, t_end_s=gt.t_end_s, f_low_hz=gt.f_low_hz, f_high_hz=gt.f_high_hz,
                    class_id=gt.class_id, class_name=gt.class_name,
                )
                for gt in sorted(gt_by_recording.get(recording.id, []), key=lambda g: g.id)
            )
            manifests.append(ManifestRecording(
                recording_id=recording.id,
                name=recording.name,
                data_format=recording.data_format,
                sample_rate_hz=recording.sample_rate_hz,
                center_frequency_hz=recording.center_frequency_hz,
                frequency_low_hz=recording.frequency_low_hz,
                frequency_high_hz=recording.frequency_high_hz,
                num_samples=recording.num_samples,
                duration_s=recording.duration_s,
                ground_truth=gts,
            ))
        return manifests

    def _build_frozen_manifest(self, dataset_name: str, dataset_split: str, label_space: str) -> FrozenRecordingManifest:
        manifests = self._load_recording_manifests(dataset_name, dataset_split, label_space)
        return build_recording_manifest(dataset_name, dataset_split, label_space, manifests)

    # ---------- imported-batch derived read model ----------

    def _batch_runs(self, import_fingerprint: str | None = None) -> list[AnalysisRunModel]:
        candidates = list(self.session.scalars(
            select(AnalysisRunModel)
            .where(
                AnalysisRunModel.executor == "imported",
                AnalysisRunModel.status == "completed",
            )
            .order_by(AnalysisRunModel.id)
        ).all())
        selected = []
        for run in candidates:
            fingerprint = _batch_import_payload(run).get("import_fingerprint")
            if not isinstance(fingerprint, str) or _SHA256_RE.fullmatch(fingerprint) is None:
                continue
            if import_fingerprint is None or fingerprint == import_fingerprint:
                selected.append(run)
        return selected

    def _detection_counts(self, run_ids: list[str]) -> dict[str, int]:
        if not run_ids:
            return {}
        return {
            run_id: int(count)
            for run_id, count in self.session.execute(
                select(DetectionResultModel.run_id, func.count(DetectionResultModel.id))
                .where(DetectionResultModel.run_id.in_(run_ids))
                .group_by(DetectionResultModel.run_id)
            ).all()
        }

    def resolve_imported_batch(self, import_fingerprint: str) -> ImportedBatchResolutionPreview:
        runs = self._batch_runs(import_fingerprint)
        if not runs:
            raise PlatformError("IMPORTED_BATCH_NOT_FOUND", "Imported batch was not found.", 404)

        metas = [_batch_import_payload(run) for run in runs]
        item_keys = [meta.get("item_key") for meta in metas]
        if any(not isinstance(key, str) or not key for key in item_keys) or len(set(item_keys)) != len(item_keys):
            raise PlatformError(
                "IMPORTED_BATCH_STATE_INCONSISTENT",
                "Imported batch has missing or duplicate item keys.",
                409,
            )

        recording_ids = [run.recording_id for run in runs]
        if len(set(recording_ids)) != len(recording_ids):
            raise PlatformError(
                "IMPORTED_BATCH_STATE_INCONSISTENT",
                "Imported batch maps more than one completed run to a Recording.",
                409,
            )

        pipeline_ids = {run.pipeline_id for run in runs}
        pipeline_versions = {run.pipeline_version for run in runs}
        if len(pipeline_ids) != 1 or len(pipeline_versions) != 1:
            raise PlatformError(
                "IMPORTED_BATCH_STATE_INCONSISTENT",
                "Imported batch contains mixed pipeline id/version values.",
                409,
            )

        recordings = {
            recording.id: recording
            for recording in self.session.scalars(
                select(RecordingModel).where(RecordingModel.id.in_(recording_ids))
            ).all()
        }
        if set(recordings) != set(recording_ids):
            raise PlatformError(
                "IMPORTED_BATCH_STATE_INCONSISTENT",
                "Imported batch references a missing Recording.",
                409,
            )

        resolver = DatasetProjectionResolver(self.session)
        projection_ids = set()
        for recording in recordings.values():
            projection = resolver.find_for_recording(recording)
            if projection is None:
                raise PlatformError(
                    "IMPORTED_BATCH_STATE_INCONSISTENT",
                    "Imported batch references a Recording without a dataset identity.",
                    409,
                )
            projection_ids.add(projection.dataset_projection_id)
        if len(projection_ids) != 1:
            raise PlatformError(
                "IMPORTED_BATCH_STATE_INCONSISTENT",
                "Imported batch Recordings do not share one dataset projection identity.",
                409,
            )
        dataset_projection_id = next(iter(projection_ids))
        projection = resolver.get(dataset_projection_id)
        dataset_name = projection.dataset_name
        dataset_split = projection.dataset_split
        label_space = projection.label_space or ""
        frozen = self._build_projection_frozen_manifest(dataset_projection_id)
        frozen_ids = {entry.recording_id for entry in frozen.entries}
        if set(recording_ids) != frozen_ids:
            raise PlatformError(
                "IMPORTED_BATCH_DATASET_INCOMPLETE",
                "Imported batch does not cover the current frozen Recording manifest exactly.",
                422,
            )

        # M8.6B did not persist the outer manifest hash per run. Future provenance may.
        # If present, it is a consistency assertion, not the source of truth.
        persisted_manifest_hashes = {
            meta.get("recording_manifest_hash")
            for meta in metas
            if meta.get("recording_manifest_hash") is not None
        }
        if persisted_manifest_hashes and persisted_manifest_hashes != {frozen.sha256}:
            raise PlatformError(
                "IMPORTED_BATCH_DATASET_INCOMPLETE",
                "Imported batch manifest provenance does not match the current frozen Recording manifest.",
                422,
            )

        run_by_recording = {run.recording_id: run for run in runs}
        item_key_by_recording = {run.recording_id: _batch_import_payload(run)["item_key"] for run in runs}
        entries = tuple(
            ImportedBatchResolutionEntry(
                manifest_order=index,
                recording_id=entry.recording_id,
                recording_name=entry.name,
                analysis_run_id=run_by_recording[entry.recording_id].id,
                item_key=item_key_by_recording[entry.recording_id],
            )
            for index, entry in enumerate(frozen.entries)
        )
        return ImportedBatchResolutionPreview(
            import_fingerprint=import_fingerprint,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            label_space=label_space,
            dataset_projection_id=dataset_projection_id,
            pipeline_id=next(iter(pipeline_ids)),
            pipeline_version=next(iter(pipeline_versions)),
            recording_manifest_hash=frozen.sha256,
            expected_recordings=len(frozen.entries),
            resolved_recordings=len(entries),
            missing_recordings=0,
            conflict_count=0,
            entries=entries,
        )

    def list_imported_batches(self) -> list[ImportedBatchCatalogEntry]:
        runs = self._batch_runs()
        groups: dict[str, list[AnalysisRunModel]] = {}
        for run in runs:
            fingerprint = _batch_import_payload(run)["import_fingerprint"]
            groups.setdefault(fingerprint, []).append(run)

        all_run_ids = [run.id for run in runs]
        detection_counts = self._detection_counts(all_run_ids)
        recording_ids = {run.recording_id for run in runs}
        recordings = {
            recording.id: recording
            for recording in self.session.scalars(
                select(RecordingModel).where(RecordingModel.id.in_(recording_ids))
            ).all()
        } if recording_ids else {}

        entries = []
        for fingerprint in sorted(groups):
            group = sorted(groups[fingerprint], key=lambda run: run.id)
            metas = [_batch_import_payload(run) for run in group]
            group_recordings = [recordings.get(run.recording_id) for run in group]
            valid_recordings = [recording for recording in group_recordings if recording is not None]

            pipeline_id = _only_or_none(run.pipeline_id for run in group)
            pipeline_version = _only_or_none(run.pipeline_version for run in group)
            dataset_name = _only_or_none(recording.dataset_name for recording in valid_recordings)
            dataset_split = _only_or_none(recording.dataset_split for recording in valid_recordings)
            label_space = _only_or_none(recording.label_space for recording in valid_recordings)
            archive_sha256 = _only_or_none(meta.get("archive_sha256") for meta in metas)

            result_payloads = [meta.get("result_provenance") or {} for meta in metas]
            transport_payloads = [meta.get("transport_provenance") or {} for meta in metas]
            result_provenance = result_payloads[0] if all(x == result_payloads[0] for x in result_payloads) else {}
            transport_provenance = transport_payloads[0] if all(x == transport_payloads[0] for x in transport_payloads) else {}

            try:
                resolved = self.resolve_imported_batch(fingerprint)
                ready = True
                reasons: tuple[str, ...] = ()
                pipeline_id = resolved.pipeline_id
                pipeline_version = resolved.pipeline_version
                dataset_name = resolved.dataset_name
                dataset_split = resolved.dataset_split
                label_space = resolved.label_space
            except PlatformError as exc:
                ready = False
                reasons = (exc.code,)

            entries.append(ImportedBatchCatalogEntry(
                import_fingerprint=fingerprint,
                pipeline_id=pipeline_id,
                pipeline_version=pipeline_version,
                dataset_name=dataset_name,
                dataset_split=dataset_split,
                label_space=label_space,
                run_count=len(group),
                detection_count=sum(detection_counts.get(run.id, 0) for run in group),
                archive_sha256=archive_sha256,
                result_provenance=result_provenance,
                transport_provenance=transport_provenance,
                ready=ready,
                inconsistency_reasons=reasons,
            ))

        return sorted(
            entries,
            key=lambda item: (
                item.dataset_name or "", item.dataset_split or "",
                item.pipeline_id or "", item.pipeline_version or "", item.import_fingerprint,
            ),
        )

    def _preview_from_frozen(self, frozen: FrozenRecordingManifest) -> ManifestPreview:
        gt_counts = {entry.recording_id: len(entry.ground_truth) for entry in frozen.entries}
        entries = tuple(
            ManifestEntry(
                manifest_order=index,
                recording_id=entry.recording_id,
                recording_name=entry.name,
                gt_count=gt_counts[entry.recording_id],
            )
            for index, entry in enumerate(frozen.entries)
        )
        return ManifestPreview(
            recording_manifest_hash=frozen.sha256,
            expected_recordings=len(frozen.entries),
            entries=entries,
        )

    def prepare_manifest(self, dataset_name: str, dataset_split: str, label_space: str) -> ManifestPreview:
        return self._preview_from_frozen(
            self._build_frozen_manifest(dataset_name, dataset_split, label_space)
        )

    def _build_projection_frozen_manifest(self, dataset_projection_id: str) -> FrozenRecordingManifest:
        resolver = DatasetProjectionResolver(self.session)
        projection = resolver.get(dataset_projection_id)
        members = resolver.members(dataset_projection_id, require_ground_truth=True)
        manifests = self._to_manifest_recordings(members)
        return build_recording_manifest(
            projection.dataset_name,
            projection.dataset_split,
            projection.label_space or "",
            manifests,
        )

    def prepare_projection_manifest(self, dataset_projection_id: str) -> ManifestPreview:
        return self._preview_from_frozen(
            self._build_projection_frozen_manifest(dataset_projection_id)
        )

    # ---------- pipeline snapshot resolution ----------

    def resolve_pipeline_snapshot(
        self, dataset_name: str, dataset_split: str, label_space: str, pipeline_id: str, pipeline_version: str
    ) -> RunResolutionPreview:
        frozen = self._build_frozen_manifest(dataset_name, dataset_split, label_space)
        recording_ids = [entry.recording_id for entry in frozen.entries]
        runs = list(
            self.session.scalars(
                select(AnalysisRunModel)
                .where(
                    AnalysisRunModel.recording_id.in_(recording_ids),
                    AnalysisRunModel.pipeline_id == pipeline_id,
                    AnalysisRunModel.pipeline_version == pipeline_version,
                    AnalysisRunModel.status == "completed",
                )
                .order_by(AnalysisRunModel.created_at, AnalysisRunModel.id)
            ).all()
        )
        runs_by_recording: dict[str, list[str]] = {}
        for run in runs:
            runs_by_recording.setdefault(run.recording_id, []).append(run.id)
        entries = []
        for index, entry in enumerate(frozen.entries):
            candidates = runs_by_recording.get(entry.recording_id, [])
            if len(candidates) == 1:
                resolution = "resolved"
            elif len(candidates) == 0:
                resolution = "missing"
            else:
                resolution = "ambiguous"
            entries.append(RunResolutionEntry(
                manifest_order=index,
                recording_id=entry.recording_id,
                recording_name=entry.name,
                resolution=resolution,
                candidate_run_ids=tuple(candidates),
            ))
        return RunResolutionPreview(
            recording_manifest_hash=frozen.sha256,
            entries=tuple(entries),
        )

    # ---------- explicit frozen evaluation creation ----------

    def prepare_evaluation(
        self,
        *,
        name: str,
        dataset_name: str,
        dataset_split: str,
        label_space: str,
        recording_manifest_hash: str,
        items: list[dict],
        allow_incomplete: bool = False,
        evaluation_protocol: str = DEFAULT_PHYSICAL_TF_PROTOCOL,
        dataset_projection_id: str | None = None,
        dataset_id: str | None = None,
    ) -> DatasetEvaluationModel:
        if dataset_id is not None:
            from app.datasets.analysis_manifest import build_dataset_analysis_manifest

            info = build_dataset_analysis_manifest(self.session, dataset_id)
            if dataset_name != info.dataset_name:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_name does not match the dataset authority.",
                )
            if dataset_split != info.dataset_split:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_split does not match the dataset authority.",
                )
            if label_space != info.label_space:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied label_space does not match the dataset authority.",
                )
            dataset_name = info.dataset_name
            dataset_split = info.dataset_split
            label_space = info.label_space
            frozen = info.frozen
        elif dataset_projection_id is not None:
            projection = DatasetProjectionResolver(self.session).get(dataset_projection_id)
            if dataset_name != projection.dataset_name:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_name does not match the dataset projection.",
                )
            if dataset_split != projection.dataset_split:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_split does not match the dataset projection.",
                )
            if label_space != (projection.label_space or ""):
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied label_space does not match the dataset projection.",
                )
            dataset_name = projection.dataset_name
            dataset_split = projection.dataset_split
            label_space = projection.label_space or ""
            frozen = self._build_projection_frozen_manifest(dataset_projection_id)
        else:
            frozen = self._build_frozen_manifest(dataset_name, dataset_split, label_space)
        if frozen.sha256 != recording_manifest_hash:
            raise PlatformError("DATASET_MANIFEST_CHANGED", "Recording manifest changed since preview.", 409)

        manifest_by_id = {entry.recording_id: entry for entry in frozen.entries}
        if len(items) != len(frozen.entries):
            raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "One item per manifest Recording is required.", 422)
        supplied_ids = {item["recording_id"] for item in items}
        if supplied_ids != set(manifest_by_id):
            raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "Item set does not match the manifest.", 422)

        included_runs: list[AnalysisRunModel] = []
        run_ids = [item["analysis_run_id"] for item in items if item["analysis_run_id"] is not None]
        if not run_ids:
            raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "At least one included run is required.", 422)
        if any(item["analysis_run_id"] is None for item in items) and not allow_incomplete:
            raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "Incomplete benchmark requires allow_incomplete.", 422)

        runs_by_id = {
            run.id: run
            for run in self.session.scalars(select(AnalysisRunModel).where(AnalysisRunModel.id.in_(run_ids))).all()
        }
        for item in items:
            run_id = item["analysis_run_id"]
            if run_id is None:
                continue
            run = runs_by_id.get(run_id)
            if run is None:
                raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", f"Run {run_id} does not exist.", 422)
            if run.status != "completed":
                raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "All included runs must be completed.", 422)
            if run.recording_id != item["recording_id"]:
                raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", f"Run {run_id} belongs to a different Recording.", 422)
            if item["recording_id"] not in manifest_by_id:
                raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", f"Run {run_id} belongs to a different Recording.", 422)
            included_runs.append(run)

        pipeline_ids = {run.pipeline_id for run in included_runs}
        pipeline_versions = {run.pipeline_version for run in included_runs}
        if len(pipeline_ids) != 1 or len(pipeline_versions) != 1:
            raise PlatformError("INVALID_BENCHMARK_MEMBERSHIP", "All included runs must share the same pipeline id/version.", 422)
        pipeline_id = pipeline_ids.pop()
        pipeline_version = pipeline_versions.pop()

        prediction_counts = {
            run_id: count
            for run_id, count in self.session.execute(
                select(DetectionResultModel.run_id, func.count(DetectionResultModel.id))
                .where(DetectionResultModel.run_id.in_(run_ids))
                .group_by(DetectionResultModel.run_id)
            ).all()
        }

        evaluation_id = f"eval_{uuid4().hex}"
        evaluation = DatasetEvaluationModel(
            id=evaluation_id,
            name=name,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            label_space=label_space,
            pipeline_id=pipeline_id,
            pipeline_version=pipeline_version,
            status="pending",
            expected_recordings=len(frozen.entries),
            evaluated_recordings=0,
            missing_recordings=0,
            coverage=0.0,
            comparable=False,
            recording_manifest_hash=frozen.sha256,
            dataset_projection_id=dataset_projection_id,
            dataset_id=dataset_id,
            evaluation_protocol=evaluation_protocol,
            protocol_config_json=_protocol_config_for(evaluation_protocol),
        )
        item_rows = []
        evaluated = 0
        missing = 0
        for index, item in enumerate(sorted(items, key=lambda i: manifest_by_id[i["recording_id"]].name)):
            run_id = item["analysis_run_id"]
            if run_id is None:
                status = "missing_run"
                missing += 1
            else:
                status = "included"
                evaluated += 1
            recording_id = item["recording_id"]
            gt_count = count_ground_truths_for_protocol(
                evaluation_protocol,
                manifest_by_id[recording_id].ground_truth,
            )
            item_rows.append(DatasetEvaluationItemModel(
                id=f"evalitem_{uuid4().hex}",
                evaluation_id=evaluation_id,
                manifest_order=index,
                recording_id=recording_id,
                analysis_run_id=run_id,
                status=status,
                gt_count=gt_count,
                prediction_count=prediction_counts.get(run_id, 0) if run_id else 0,
            ))
        evaluation.evaluated_recordings = evaluated
        evaluation.missing_recordings = missing
        evaluation.coverage = evaluated / len(frozen.entries) if frozen.entries else 0.0
        evaluation.comparable = evaluation.coverage == 1.0
        self.session.add(evaluation)
        self.session.add_all(item_rows)
        return evaluation

    def create_evaluation(self, **kwargs):
        evaluation = self.prepare_evaluation(**kwargs)
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation

    def start_evaluation(self, evaluation_id: str, job_manager) -> DatasetEvaluationModel:
        # Transaction A: durable single-winner start claim (pending -> running).
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        DatasetEvaluationModel.status == "pending",
                        DatasetEvaluationModel.worker_pid.is_(None),
                    )
                    .values(status="running", started_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            if int(claimed.rowcount or 0) != 1:
                self.session.rollback()
                row = self.session.get(DatasetEvaluationModel, evaluation_id)
                if row is None:
                    raise PlatformError("BENCHMARK_NOT_FOUND",
                                        "DatasetEvaluation was not found.", 404)
                raise PlatformError("INVALID_BENCHMARK_TRANSITION",
                                    "Evaluation was already started.", 409)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        # No open transaction before spawn.
        self.session.rollback()

        try:
            worker_pid = job_manager.start(evaluation_id)
        except Exception as exc:
            # Claim is now running: definitive spawn failure -> running -> failed.
            now = datetime.now(timezone.utc)
            try:
                with self.session.no_autoflush:
                    self.session.execute(
                        update(DatasetEvaluationModel)
                        .where(
                            DatasetEvaluationModel.id == evaluation_id,
                            DatasetEvaluationModel.status == "running",
                            DatasetEvaluationModel.worker_pid.is_(None),
                        )
                        .values(status="failed", error_type="BENCHMARK_FAILED",
                                error_message=str(exc)[:1000], completed_at=now)
                        .execution_options(synchronize_session=False)
                    )
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
            raise PlatformError("BENCHMARK_FAILED",
                                "Unable to start local benchmark worker.") from exc

        # Parent persists ONLY worker_pid; never overwrite a fast worker's
        # running/completed/failed status.
        try:
            with self.session.no_autoflush:
                self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(DatasetEvaluationModel.id == evaluation_id)
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return self.session.get(DatasetEvaluationModel, evaluation_id)

    def snapshot_retry_evaluation(self, evaluation_id):
        evaluation = self.get_evaluation(evaluation_id)
        return {
            "status": evaluation.status,
            "error_type": evaluation.error_type,
            "error_message": evaluation.error_message,
            "completed_at": evaluation.completed_at,
            "started_at": evaluation.started_at,
            "worker_pid": evaluation.worker_pid,
            "progress_stage": evaluation.progress_stage,
            "progress_current": evaluation.progress_current,
            "progress_total": evaluation.progress_total,
            "aggregate_metrics_json": evaluation.aggregate_metrics_json,
            "per_class_metrics_json": evaluation.per_class_metrics_json,
            "confusion_json": evaluation.confusion_json,
        }

    def prepare_retry_evaluation(self, evaluation_id):
        evaluation = self.get_evaluation(evaluation_id)
        if evaluation.status not in {"failed", "interrupted"}:
            raise PlatformError("INVALID_BENCHMARK_TRANSITION",
                                "Only failed/interrupted evaluations can be retried.", 409)
        evaluation.status = "pending"
        evaluation.error_type = None
        evaluation.error_message = None
        evaluation.aggregate_metrics_json = None
        evaluation.per_class_metrics_json = None
        evaluation.confusion_json = None
        evaluation.progress_stage = None
        evaluation.worker_pid = None
        return evaluation

    def restore_retry_evaluation(self, evaluation_id, snapshot):
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        for field, value in snapshot.items():
            setattr(evaluation, field, value)
        return evaluation

    def _evaluation_is_dataset_experiment_managed(self, evaluation_id):
        from app.dataset_experiments.model import DatasetExperimentModel

        return bool(self.session.scalar(
            select(exists().where(
                DatasetExperimentModel.dataset_evaluation_id == evaluation_id
            ))
        ))

    def retry_evaluation(self, evaluation_id):
        # OWNERSHIP: a DatasetExperiment-linked Evaluation is orchestration-managed
        # and MUST NOT be retried through the generic benchmark API; that would
        # leave Experiment=failed while the Evaluation advances out-of-band.
        self.get_evaluation(evaluation_id)  # 404 if missing
        if self._evaluation_is_dataset_experiment_managed(evaluation_id):
            raise PlatformError(
                "BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT",
                "This DatasetEvaluation is managed by DatasetExperiment; use the "
                "DatasetExperiment Retry Evaluation endpoint.",
                409,
            )
        evaluation = self.prepare_retry_evaluation(evaluation_id)
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
    
    
    def get_evaluation(self, evaluation_id: str) -> DatasetEvaluationModel:
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        return evaluation
    
    
    def list_evaluations(self, dataset_id: str | None = None) -> list[DatasetEvaluationModel]:
        statement = select(DatasetEvaluationModel).order_by(DatasetEvaluationModel.created_at.desc())
        if dataset_id is not None:
            statement = statement.where(DatasetEvaluationModel.dataset_id == dataset_id)
        return list(self.session.scalars(statement).all())
    
    
    def list_items(self, evaluation_id: str) -> list[DatasetEvaluationItemView]:
        self.get_evaluation(evaluation_id)
        rows = self.session.execute(
            select(DatasetEvaluationItemModel, RecordingModel.name)
            .join(RecordingModel, DatasetEvaluationItemModel.recording_id == RecordingModel.id)
            .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
            .order_by(DatasetEvaluationItemModel.manifest_order)
        ).all()
        return [
            DatasetEvaluationItemView(
                id=item.id,
                evaluation_id=item.evaluation_id,
                manifest_order=item.manifest_order,
                recording_id=item.recording_id,
                recording_name=recording_name,
                analysis_run_id=item.analysis_run_id,
                status=item.status,
                gt_count=item.gt_count,
                prediction_count=item.prediction_count,
                error_reason=item.error_reason,
            )
            for item, recording_name in rows
        ]
    
    
    def compare_evaluations(self, evaluation_a_id: str, evaluation_b_id: str) -> dict:
        a = self.get_evaluation(evaluation_a_id)
        b = self.get_evaluation(evaluation_b_id)
        reasons: list[str] = []
        if a.status != "completed":
            reasons.append("evaluation_a_not_completed")
        if b.status != "completed":
            reasons.append("evaluation_b_not_completed")
        if a.coverage != 1.0:
            reasons.append("evaluation_a_incomplete")
        if b.coverage != 1.0:
            reasons.append("evaluation_b_incomplete")
        if a.dataset_name != b.dataset_name:
            reasons.append("dataset_name_mismatch")
        if a.dataset_split != b.dataset_split:
            reasons.append("dataset_split_mismatch")
        if a.label_space != b.label_space:
            reasons.append("label_space_mismatch")
        if (
            a.dataset_id is not None
            and b.dataset_id is not None
            and a.dataset_id != b.dataset_id
        ):
            reasons.append("dataset_id_mismatch")
        if a.recording_manifest_hash != b.recording_manifest_hash:
            reasons.append("recording_manifest_hash_mismatch")
        if a.evaluation_protocol != b.evaluation_protocol:
            reasons.append("evaluation_protocol_mismatch")
        if a.protocol_config_json != b.protocol_config_json:
            reasons.append("protocol_config_mismatch")
    
        comparable = not reasons
        deltas: dict[str, float | None] = {}
        if comparable:
            agg_a = a.aggregate_metrics_json or {}
            agg_b = b.aggregate_metrics_json or {}
            pairs = {
                "localization_ap50": ("localization", "ap50"),
                "localization_ap50_95": ("localization", "ap50_95"),
                "class_aware_map50": ("class_aware", "map50"),
                "class_aware_map50_95": ("class_aware", "map50_95"),
                "matched_accuracy": ("classification_on_matched", "matched_accuracy"),
            }
            for key, (section, field) in pairs.items():
                val_a = (agg_a.get(section) or {}).get(field)
                val_b = (agg_b.get(section) or {}).get(field)
                if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                    deltas[key] = val_b - val_a
                else:
                    deltas[key] = None
        return {
            "comparable": comparable,
            "reasons": reasons,
            "evaluation_a_id": a.id,
            "evaluation_b_id": b.id,
            "aggregate_a": a.aggregate_metrics_json,
            "aggregate_b": b.aggregate_metrics_json,
            "deltas": deltas,
            "recordings": self._compare_recordings(a, b),
        }

    @staticmethod
    def _match_box(obj) -> dict:
        return {
            "t_start_s": obj.t_start_s,
            "t_end_s": obj.t_end_s,
            "f_low_hz": obj.f_low_hz,
            "f_high_hz": obj.f_high_hz,
        }

    def _detected_recordings(self, evaluation: DatasetEvaluationModel) -> dict[str, bool]:
        """Reuse the frozen protocol view + M8.5 matching to decide, per Recording,
        whether the evaluation's run matched at least one Ground Truth at IoU 0.5.
        """
        from app.benchmarks.loader import BenchmarkInputLoader
        from app.benchmarks.protocol import build_protocol_view
        from app.evaluation.matching import match_predictions

        loaded = BenchmarkInputLoader(self.session).load(evaluation.id)
        view = build_protocol_view(evaluation.evaluation_protocol, loaded)
        detected: dict[str, bool] = {}
        for sample in view.samples:
            match = match_predictions(
                [self._match_box(gt) for gt in sample.ground_truths],
                [self._match_box(pred) for pred in sample.predictions],
                iou_threshold=0.5,
            )
            detected[sample.recording_id] = bool(match.pairs)
        return detected

    def _compare_recordings(
        self, a: DatasetEvaluationModel, b: DatasetEvaluationModel
    ) -> list[dict]:
        items_a = {item.recording_id: item for item in self.list_items(a.id)}
        items_b = {item.recording_id: item for item in self.list_items(b.id)}
        detected_a = self._detected_recordings(a)
        detected_b = self._detected_recordings(b)

        order: dict[str, int] = {}
        for recording_id in {**items_a, **items_b}:
            candidates = [
                item.manifest_order
                for item in (items_a.get(recording_id), items_b.get(recording_id))
                if item is not None
            ]
            order[recording_id] = min(candidates)

        rows: list[dict] = []
        for recording_id in sorted(order, key=lambda rid: order[rid]):
            item_a = items_a.get(recording_id)
            item_b = items_b.get(recording_id)
            a_detected = detected_a.get(recording_id, False)
            b_detected = detected_b.get(recording_id, False)
            if a_detected and b_detected:
                comparison = "both_detected"
            elif a_detected:
                comparison = "a_only"
            elif b_detected:
                comparison = "b_only"
            else:
                comparison = "both_missed"
            rows.append({
                "recording_id": recording_id,
                "recording_name": (item_a or item_b).recording_name,
                "evaluation_a_run_id": item_a.analysis_run_id if item_a else None,
                "evaluation_b_run_id": item_b.analysis_run_id if item_b else None,
                "comparison": comparison,
            })
        return rows
