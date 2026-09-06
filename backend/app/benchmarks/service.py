from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select, update
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
class RunResolutionEntry:
    manifest_order: int
    recording_id: str
    recording_name: str
    resolution: str
    candidate_run_ids: tuple[str, ...]


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

    def prepare_manifest(self, dataset_name: str, dataset_split: str, label_space: str) -> ManifestPreview:
        frozen = self._build_frozen_manifest(dataset_name, dataset_split, label_space)
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

    def create_evaluation(
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
    ) -> DatasetEvaluationModel:
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
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
    def start_evaluation(self, evaluation_id: str, job_manager) -> DatasetEvaluationModel:
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        if evaluation.status != "pending":
            raise PlatformError("INVALID_BENCHMARK_TRANSITION", "Only pending evaluations can be started.", 409)
        try:
            worker_pid = job_manager.start(evaluation.id)
        except Exception as exc:
            evaluation.status = "failed"
            evaluation.error_type = "BENCHMARK_FAILED"
            evaluation.error_message = str(exc)[:1000]
            evaluation.completed_at = datetime.now(timezone.utc)
            self.session.commit()
            raise PlatformError("BENCHMARK_FAILED", "Unable to start local benchmark worker.") from exc
        # Parent only records the worker PID. The running transition is owned by
        # the worker subprocess so a fast worker that completes before this commit
        # is never overwritten back to running.
        evaluation.worker_pid = worker_pid
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
        
        
    def retry_evaluation(self, evaluation_id: str) -> DatasetEvaluationModel:
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        if evaluation.status not in {"failed", "interrupted"}:
            raise PlatformError("INVALID_BENCHMARK_TRANSITION", "Only failed/interrupted evaluations can be retried.", 409)
        evaluation.status = "pending"
        evaluation.error_type = None
        evaluation.error_message = None
        evaluation.aggregate_metrics_json = None
        evaluation.per_class_metrics_json = None
        evaluation.confusion_json = None
        evaluation.progress_stage = None
        evaluation.worker_pid = None
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
    
    
    def get_evaluation(self, evaluation_id: str) -> DatasetEvaluationModel:
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        return evaluation
    
    
    def list_evaluations(self) -> list[DatasetEvaluationModel]:
        return list(
            self.session.scalars(select(DatasetEvaluationModel).order_by(DatasetEvaluationModel.created_at.desc())).all()
        )
    
    
    def list_items(self, evaluation_id: str) -> list[DatasetEvaluationItemModel]:
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        return list(
            self.session.scalars(
                select(DatasetEvaluationItemModel)
                .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
                .order_by(DatasetEvaluationItemModel.manifest_order)
            ).all()
        )
    
    
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
        }
