"""Bulk loading of frozen benchmark inputs without N+1 queries."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel
from app.detections.model import DetectionResultModel
from app.evaluation.ap import EvaluationGroundTruth, EvaluationPrediction
from app.evaluation.dataset_metrics import EvaluationSample
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


@dataclass(frozen=True)
class LoadedBenchmark:
    samples: tuple[EvaluationSample, ...]
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]
    runs_by_recording: dict[str, AnalysisRunModel]
    recordings_by_id: dict[str, RecordingModel]


class BenchmarkInputLoader:
    def __init__(self, session: Session):
        self.session = session

    def load(self, evaluation_id: str) -> LoadedBenchmark:
        items = list(
            self.session.scalars(
                select(DatasetEvaluationItemModel)
                .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
                .order_by(DatasetEvaluationItemModel.manifest_order)
            ).all()
        )
        recording_ids = [item.recording_id for item in items]
        run_ids = [item.analysis_run_id for item in items if item.analysis_run_id is not None]

        recordings = {
            recording.id: recording
            for recording in self.session.scalars(
                select(RecordingModel).where(RecordingModel.id.in_(recording_ids))
            ).all()
        }
        runs = {
            run.id: run
            for run in self.session.scalars(
                select(AnalysisRunModel).where(AnalysisRunModel.id.in_(run_ids))
            ).all()
        }

        gt_by_recording: dict[str, list[GroundTruthModel]] = {}
        if recording_ids:
            for gt in self.session.scalars(
                select(GroundTruthModel).where(GroundTruthModel.recording_id.in_(recording_ids))
            ).all():
                gt_by_recording.setdefault(gt.recording_id, []).append(gt)

        pred_by_run: dict[str, list[DetectionResultModel]] = {}
        if run_ids:
            for det in self.session.scalars(
                select(DetectionResultModel).where(DetectionResultModel.run_id.in_(run_ids))
            ).all():
                pred_by_run.setdefault(det.run_id, []).append(det)

        samples = []
        ground_truths = []
        predictions = []
        for item in items:
            recording = recordings[item.recording_id]
            gts = tuple(
                EvaluationGroundTruth(
                    recording_id=item.recording_id,
                    manifest_order=item.manifest_order,
                    t_start_s=gt.t_start_s, t_end_s=gt.t_end_s,
                    f_low_hz=gt.f_low_hz, f_high_hz=gt.f_high_hz,
                    class_id=gt.class_id, class_name=gt.class_name,
                )
                for gt in gt_by_recording.get(item.recording_id, [])
            )
            preds = tuple(
                EvaluationPrediction(
                    recording_id=item.recording_id,
                    manifest_order=item.manifest_order,
                    t_start_s=det.t_start_s, t_end_s=det.t_end_s,
                    f_low_hz=det.f_low_hz, f_high_hz=det.f_high_hz,
                    class_id=det.class_id, class_name=det.class_name,
                    confidence=det.confidence,
                )
                for det in pred_by_run.get(item.analysis_run_id, [])
            )
            samples.append(EvaluationSample(
                recording_id=item.recording_id, manifest_order=item.manifest_order,
                ground_truths=gts, predictions=preds,
            ))
            ground_truths.extend(gts)
            predictions.extend(preds)

        return LoadedBenchmark(
            samples=tuple(samples),
            ground_truths=tuple(ground_truths),
            predictions=tuple(predictions),
            runs_by_recording={item.recording_id: runs[item.analysis_run_id] for item in items
                               if item.analysis_run_id is not None},
            recordings_by_id=recordings,
        )