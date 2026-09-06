from __future__ import annotations

from datetime import datetime, timezone
import logging
import sys
import traceback

from app.analysis.model import AnalysisRunModel
from app.benchmarks.job_manager import LocalBenchmarkJobManager
from app.benchmarks.loader import BenchmarkInputLoader, LoadedBenchmark
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.protocol import build_protocol_view
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL_V2
from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.evaluation.ap import class_aware_ap_summary, localization_ap_summary
from app.evaluation.capability import classification_applicability
from app.evaluation.dataset_metrics import compute_dataset_diagnostics
from app.pipelines.registry import create_pipeline_registry

logger = logging.getLogger(__name__)


def _build_result_jsons(
    diagnostics,
    localization_ap,
    class_aware_ap,
    applicable: bool,
    reason: str | None,
):
    aggregate = {
        "classification_applicable": applicable,
        "classification_reason": reason,
        "localization": {
            "ap50": localization_ap.ap50,
            "ap50_95": localization_ap.ap50_95,
            "operating": {
                "tp": diagnostics.localization.tp,
                "fp": diagnostics.localization.fp,
                "fn": diagnostics.localization.fn,
                "precision": diagnostics.localization.precision,
                "recall": diagnostics.localization.recall,
                "f1": diagnostics.localization.f1,
            },
        },
    }
    if not applicable:
        aggregate["classification_on_matched"] = None
        aggregate["class_aware"] = None
        per_class: list[dict] = []
        confusion = None
        return aggregate, per_class, confusion

    classification = diagnostics.classification
    aggregate["classification_on_matched"] = {
        "matched_count": classification.matched_count,
        "class_correct": classification.class_correct,
        "class_wrong": classification.class_wrong,
        "matched_accuracy": classification.matched_accuracy,
    }
    aggregate["class_aware"] = {
        "map50": class_aware_ap.map50,
        "map50_95": class_aware_ap.map50_95,
        "operating": {
            "tp": diagnostics.class_aware.tp,
            "fp": diagnostics.class_aware.fp,
            "fn": diagnostics.class_aware.fn,
            "precision": diagnostics.class_aware.precision,
            "recall": diagnostics.class_aware.recall,
            "f1": diagnostics.class_aware.f1,
        },
    }
    per_class = []
    per_class_by_id = {item.class_id: item for item in diagnostics.per_class}
    for item in class_aware_ap.per_class:
        operating = per_class_by_id.get(item.class_id)
        per_class.append({
            "class_id": item.class_id,
            "class_name": item.class_name,
            "gt_count": item.gt_count,
            "prediction_count": item.prediction_count,
            "ap50": item.ap50,
            "ap50_95": item.ap50_95,
            "operating": {
                "tp": operating.tp if operating else 0,
                "fp": operating.fp if operating else 0,
                "fn": operating.fn if operating else 0,
                "precision": operating.precision if operating else 0.0,
                "recall": operating.recall if operating else 0.0,
                "f1": operating.f1 if operating else 0.0,
            },
        })
    confusion = [
        {
            "gt_class_id": c.gt_class_id,
            "gt_class_name": c.gt_class_name,
            "pred_class_id": c.pred_class_id,
            "pred_class_name": c.pred_class_name,
            "count": c.count,
        }
        for c in classification.confusions
    ]
    return aggregate, per_class, confusion


def execute_benchmark(evaluation_id: str, settings: Settings | None = None) -> None:
    settings = settings or Settings()
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    registry = create_pipeline_registry()

    try:
        with database.session_factory() as session:
            evaluation = session.get(DatasetEvaluationModel, evaluation_id)
            if evaluation is None:
                raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
            evaluation.status = "running"
            evaluation.started_at = datetime.now(timezone.utc)
            evaluation.error_type = None
            evaluation.error_message = None
            evaluation.progress_stage = "loading"
            session.commit()

            loader = BenchmarkInputLoader(session)
            loaded = loader.load(evaluation_id)
            view = build_protocol_view(evaluation.evaluation_protocol, loaded)
            evaluation.progress_stage = "diagnostics"
            session.commit()

            # Consistency check for classification semantics across included items.
            applicable = None
            reason = None
            for recording_id, run in loaded.runs_by_recording.items():
                recording = loaded.recordings_by_id[recording_id]
                result = classification_applicability(run, recording, registry)
                if applicable is None:
                    applicable, reason = result.applicable, result.reason
                elif (result.applicable, result.reason) != (applicable, reason):
                    raise PlatformError(
                        "INCONSISTENT_CLASSIFICATION_SEMANTICS",
                        "Included runs resolve to different classification semantics.",
                        422,
                    )
            if applicable is None:
                applicable = False
                reason = "unknown_classification_semantics"

            evaluation.progress_stage = "diagnostics"
            session.commit()
            diagnostics = compute_dataset_diagnostics(list(view.samples), classification_applicable=applicable)

            evaluation.progress_stage = "localization_ap"
            session.commit()
            localization_ap = localization_ap_summary(list(view.ground_truths), list(view.predictions))

            class_aware_ap = None
            if applicable:
                evaluation.progress_stage = "class_aware_ap"
                session.commit()
                class_aware_ap = class_aware_ap_summary(list(view.ground_truths), list(view.predictions))

            aggregate, per_class, confusion = _build_result_jsons(
                diagnostics, localization_ap, class_aware_ap, applicable, reason)
            if evaluation.evaluation_protocol == PHYSICAL_TF_PROTOCOL_V2:
                aggregate["ground_truth"] = {
                    "raw_count": view.ground_truth_accounting.raw_count,
                    "canonical_count": view.ground_truth_accounting.canonical_count,
                    "duplicates_removed": view.ground_truth_accounting.removed_count,
                    "duplicate_policy": "exact_physical_class_dedup",
                }
            evaluation.progress_stage = "finalizing"
            session.commit()

            evaluation.aggregate_metrics_json = aggregate
            evaluation.per_class_metrics_json = per_class
            evaluation.confusion_json = confusion
            sample_by_recording = {sample.recording_id: sample for sample in view.samples}
            for item in evaluation.items:
                sample = sample_by_recording.get(item.recording_id)
                if sample is not None:
                    assert item.gt_count == len(sample.ground_truths)
                    item.prediction_count = len(sample.predictions)
            evaluation.status = "completed"
            evaluation.progress_stage = "completed"
            evaluation.completed_at = datetime.now(timezone.utc)
            session.commit()
    except Exception as exc:
        logger.error("Benchmark worker failed for %s\n%s", evaluation_id, traceback.format_exc())
        with database.session_factory() as recovery_session:
            evaluation = recovery_session.get(DatasetEvaluationModel, evaluation_id)
            if evaluation is not None:
                evaluation.status = "failed"
                if isinstance(exc, PlatformError):
                    evaluation.error_type = exc.code
                    evaluation.error_message = exc.message[:1000]
                else:
                    evaluation.error_type = type(exc).__name__
                    evaluation.error_message = str(exc)[:1000]
                evaluation.completed_at = datetime.now(timezone.utc)
                evaluation.aggregate_metrics_json = None
                evaluation.per_class_metrics_json = None
                evaluation.confusion_json = None
                recovery_session.commit()
        raise


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 1:
        print("usage: python -m app.benchmarks.worker <evaluation_id>", file=sys.stderr)
        return 2
    execute_benchmark(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())