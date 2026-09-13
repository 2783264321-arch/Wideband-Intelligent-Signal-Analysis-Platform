from __future__ import annotations

import time
from enum import Enum

from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentModel

EXPERIMENT_LEVEL_CODES = frozenset({
    "ANALYSIS_RUN_NOT_LAUNCHABLE",
    "MODEL_RELEASE_MISMATCH",
    "PIPELINE_INCOMPATIBLE",
    "PLUGIN_API_INCOMPATIBLE",
    "PLUGIN_NOT_FOUND",
    "PLUGIN_PARAMETERS_INVALID",
    "RECORDING_NOT_FOUND",
    "REMOTE_IMPLEMENTATION_MISMATCH",
    "PIPELINE_ASSET_MISMATCH",
})

ITEM_LEVEL_CODES = frozenset({
    "INPUT_INCOMPATIBLE",
    "EXECUTOR_UNAVAILABLE",
    "ANALYSIS_FAILED",
    "SOURCE_DATA_NOT_FOUND",
    "SOURCE_DATA_NOT_FILE",
    "REMOTE_EXECUTOR_UNAVAILABLE",
    "REMOTE_TRANSPORT_UNAVAILABLE",
    "REMOTE_PROBE_UNAVAILABLE",
    # Plan A1: a recoverable deployment authority loss fails the item, not the
    # experiment. The persisted pending run is terminalized before this is raised.
    "EXECUTION_CAPABILITY_UNAVAILABLE",
    "EXECUTION_NOT_CERTIFIED",
})


class CoordinatorOutcome(str, Enum):
    SCHEDULED = "scheduled"
    WAITING = "waiting"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    INFERENCE_COMPLETE = "inference_complete"
    EXPERIMENT_COMPLETED = "experiment_completed"
    EXPERIMENT_TERMINAL = "experiment_terminal"
    FENCE_LOST = "fence_lost"
    INVARIANT_FAILED = "invariant_failed"


EXIT_OUTCOMES = frozenset({
    CoordinatorOutcome.COMPLETED_WITH_FAILURES,
    CoordinatorOutcome.INFERENCE_COMPLETE,
    CoordinatorOutcome.EXPERIMENT_COMPLETED,
    CoordinatorOutcome.EXPERIMENT_TERMINAL,
    CoordinatorOutcome.FENCE_LOST,
    CoordinatorOutcome.INVARIANT_FAILED,
})


def is_experiment_level(exc: PlatformError) -> bool:
    if exc.code.startswith("DATASET_EXPERIMENT_"):
        return True
    if exc.code in EXPERIMENT_LEVEL_CODES:
        return True
    if exc.code in ITEM_LEVEL_CODES:
        return False
    return True


class DatasetExperimentCoordinator:
    def __init__(self, *, session_factory, services_factory,
                 sleep_fn=time.sleep, poll_interval=1.0, max_iterations=None,
                 benchmark_services_factory=None, benchmark_job_manager=None):
        self._session_factory = session_factory
        self._services_factory = services_factory
        self._sleep_fn = sleep_fn
        self._poll_interval = poll_interval
        self._max_iterations = max_iterations
        self._benchmark_services_factory = benchmark_services_factory
        self._benchmark_job_manager = benchmark_job_manager

    def step(self, experiment_id, coordinator_token):
        with self._session_factory() as session:
            ds, analysis = self._services_factory(session)
            benchmarks = (
                self._benchmark_services_factory(session)
                if self._benchmark_services_factory is not None else None
            )
            return self._step(session, ds, analysis, benchmarks, experiment_id, coordinator_token)

    def _step(self, session, ds, analysis, benchmarks, experiment_id, coordinator_token):
        experiment = session.get(DatasetExperimentModel, experiment_id)
        if experiment is None or experiment.coordinator_token != coordinator_token:
            return CoordinatorOutcome.FENCE_LOST
        if experiment.status == "evaluating":
            return self._step_evaluating(session, ds, experiment_id, coordinator_token)
        if experiment.status != "running":
            return CoordinatorOutcome.EXPERIMENT_TERMINAL
        if not ds.refresh_coordinator_heartbeat(experiment_id, coordinator_token):
            return CoordinatorOutcome.FENCE_LOST
        try:
            summary = ds.reconcile_items(experiment_id, coordinator_token=coordinator_token)
            ds.revalidate_frozen_identity(experiment_id)
            if summary.queued == 0 and summary.running == 0:
                if summary.failed > 0:
                    if not ds.mark_experiment_completed_with_failures(experiment_id, coordinator_token):
                        return CoordinatorOutcome.FENCE_LOST
                    return CoordinatorOutcome.COMPLETED_WITH_FAILURES
                if summary.completed > 0:
                    if self._benchmark_services_factory is None or self._benchmark_job_manager is None:
                        return CoordinatorOutcome.INFERENCE_COMPLETE
                    return self._finish_inference(
                        session, ds, benchmarks, experiment, coordinator_token
                    )
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION", "Experiment has no items.", 409
                )
            free_slots = max(0, experiment.max_concurrency - summary.running)
            if free_slots == 0:
                return CoordinatorOutcome.WAITING
            started = 0
            for item_id in ds.select_queued_items(experiment_id, limit=free_slots):
                try:
                    attempt = ds.start_item_attempt(
                        experiment_id=experiment_id, item_id=item_id,
                        analysis_service=analysis, coordinator_token=coordinator_token,
                    )
                    ds.launch_item_attempt(
                        experiment_id=experiment_id, item_id=item_id,
                        attempt_id=attempt.id, analysis_service=analysis,
                        coordinator_token=coordinator_token,
                    )
                    started += 1
                except PlatformError as exc:
                    if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                        raise
                    if is_experiment_level(exc):
                        raise
                    ds._mark_item_failed(
                        item_id, exc.code, exc.message,
                        experiment_id=experiment_id, coordinator_token=coordinator_token,
                    )
            return CoordinatorOutcome.SCHEDULED if started else CoordinatorOutcome.WAITING
        except PlatformError as exc:
            if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                session.rollback()
                return CoordinatorOutcome.FENCE_LOST
            if is_experiment_level(exc):
                session.rollback()
                ds._fail_experiment(experiment_id, coordinator_token, exc.code, exc.message)
                return CoordinatorOutcome.INVARIANT_FAILED
            raise
        except Exception:
            session.rollback()
            ds._fail_experiment(
                experiment_id, coordinator_token,
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED", "Unexpected coordinator error.",
            )
            return CoordinatorOutcome.INVARIANT_FAILED

    def _finish_inference(self, session, ds, benchmarks, experiment, coordinator_token):
        evaluation = self._ensure_evaluation(session, ds, benchmarks, experiment, coordinator_token)
        try:
            ds.start_linked_evaluation(
                experiment.id, evaluation.id, coordinator_token, self._benchmark_job_manager
            )
            return CoordinatorOutcome.WAITING
        except PlatformError as exc:
            session.rollback()
            if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                return CoordinatorOutcome.FENCE_LOST
            error_type = (
                "DATASET_EXPERIMENT_EVALUATION_FAILED"
                if exc.code == "DATASET_EXPERIMENT_EVALUATION_FAILED"
                else exc.code
            )
            if not ds._fail_experiment(
                experiment.id, coordinator_token, error_type, exc.message,
                expected_status="evaluating",
            ):
                return CoordinatorOutcome.FENCE_LOST
            return CoordinatorOutcome.INVARIANT_FAILED
        except Exception:
            session.rollback()
            return CoordinatorOutcome.WAITING

    def _ensure_evaluation(self, session, ds, benchmarks, experiment, coordinator_token):
        if experiment.dataset_evaluation_id is not None:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Running experiment already has a linked DatasetEvaluation.", 409,
            )
        membership = ds.build_evaluation_membership(experiment.id)
        evaluation = benchmarks.prepare_evaluation(
            name=f"{experiment.name} evaluation",
            dataset_name=experiment.dataset_name,
            dataset_split=experiment.dataset_split,
            label_space=experiment.dataset_label_space,
            recording_manifest_hash=experiment.recording_manifest_hash,
            items=membership, allow_incomplete=False,
            evaluation_protocol=experiment.evaluation_protocol,
        )
        session.flush()
        if not ds.link_evaluation(experiment.id, evaluation.id, coordinator_token):
            session.rollback()
            raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST", "link lost", 409)
        return evaluation

    def _step_evaluating(self, session, ds, experiment_id, coordinator_token):
        if not ds.refresh_coordinator_heartbeat(
            experiment_id, coordinator_token, expected_status="evaluating"
        ):
            return CoordinatorOutcome.FENCE_LOST
        try:
            experiment = session.get(DatasetExperimentModel, experiment_id)
            if experiment is None or experiment.dataset_evaluation_id is None:
                raise PlatformError(
                    "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                    "Evaluating experiment has no linked DatasetEvaluation.", 409,
                )
            experiment, evaluation = ds.validate_evaluation_linkage(experiment_id)
            if evaluation.status == "completed":
                if not ds.mark_experiment_completed(experiment_id, coordinator_token):
                    return CoordinatorOutcome.FENCE_LOST
                return CoordinatorOutcome.EXPERIMENT_COMPLETED
            if evaluation.status == "failed":
                session.rollback()
                ds._fail_experiment(
                    experiment_id, coordinator_token,
                    "DATASET_EXPERIMENT_EVALUATION_FAILED", evaluation.error_message,
                    expected_status="evaluating",
                )
                return CoordinatorOutcome.INVARIANT_FAILED
            if evaluation.status == "interrupted":
                ds.reset_interrupted_evaluation(experiment_id, evaluation.id, coordinator_token)
                return CoordinatorOutcome.WAITING
            if evaluation.status == "pending" and evaluation.worker_pid is None:
                ds.start_linked_evaluation(
                    experiment_id, evaluation.id, coordinator_token, self._benchmark_job_manager
                )
                return CoordinatorOutcome.WAITING
            if evaluation.status in {"pending", "running"}:
                return CoordinatorOutcome.WAITING
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Linked DatasetEvaluation has an unknown status.", 409,
            )
        except PlatformError as exc:
            session.rollback()
            if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                return CoordinatorOutcome.FENCE_LOST
            ds._fail_experiment(
                experiment_id, coordinator_token, exc.code, exc.message,
                expected_status="evaluating",
            )
            return CoordinatorOutcome.INVARIANT_FAILED
        except Exception:
            session.rollback()
            ds._fail_experiment(
                experiment_id, coordinator_token,
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unexpected evaluating coordinator error.",
                expected_status="evaluating",
            )
            return CoordinatorOutcome.INVARIANT_FAILED
