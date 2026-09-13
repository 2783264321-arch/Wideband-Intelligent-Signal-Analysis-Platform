from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import exists, or_, select, update

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.benchmarks.model import DatasetEvaluationModel
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService, cas_fail_closed_pending_run
from app.remote_execution.runtime import FROZEN_AUTHORITY_ITEM_CODES


@dataclass
class RecoveryReport:
    claimed: int = 0
    skipped: int = 0
    repaired_first_launch: int = 0
    ambiguous_failed: int = 0
    invariants_failed: int = 0
    coordinators_started: int = 0
    spawn_failures: int = 0


_ACTIVE_RECOVERY_STATUSES = ("running", "evaluating")
_LOCAL_EXECUTORS = ("local_cpu", "local_gpu")
_AMBIGUOUS_ERROR_TYPE = "ANALYSIS_LAUNCH_AMBIGUOUS"
_AMBIGUOUS_ERROR_MESSAGE = (
    "Local launch intent is durable but the run is still pending after platform "
    "restart; the launch outcome is ambiguous. Fail closed and require explicit "
    "Retry Failed."
)


def _now():
    return datetime.now(timezone.utc)


def claim_experiment_generation(session, experiment_id, expected_token,
                                startup_recovery_cutoff,
                                statuses=("running", "evaluating")):
    """Durably take ownership of an active Experiment under a fresh token."""
    fresh_token = f"coord_{uuid4().hex}"
    cutoff_predicate = or_(
        DatasetExperimentModel.heartbeat_at.is_(None),
        DatasetExperimentModel.heartbeat_at < startup_recovery_cutoff,
    )
    statement = (
        update(DatasetExperimentModel)
        .where(
            DatasetExperimentModel.id == experiment_id,
            DatasetExperimentModel.status.in_(statuses),
            cutoff_predicate,
        )
        .values(
            coordinator_token=fresh_token,
            worker_pid=None,
            heartbeat_at=_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if expected_token is None:
        statement = statement.where(DatasetExperimentModel.coordinator_token.is_(None))
    else:
        statement = statement.where(
            DatasetExperimentModel.coordinator_token == expected_token
        )
    try:
        result = session.execute(statement)
        session.commit()
    except Exception:
        session.rollback()
        raise
    if int(result.rowcount or 0) != 1:
        return None
    return fresh_token


def fail_closed_pending_local_run(session, *, experiment_id, coordinator_token,
                                  item_id, attempt_id, run_id):
    """Generation-fenced ``pending -> interrupted`` for an ambiguous local Run."""
    return cas_fail_closed_pending_run(
        session,
        experiment_id=experiment_id,
        coordinator_token=coordinator_token,
        item_id=item_id,
        attempt_id=attempt_id,
        run_id=run_id,
        error_type=_AMBIGUOUS_ERROR_TYPE,
        error_message=_AMBIGUOUS_ERROR_MESSAGE,
    )


def repair_local_pending_runs(session, ds, analysis, *, experiment_id,
                              coordinator_token, report):
    """Repair local (``local_cpu`` + ``local_gpu``) ``pending`` runs for a
    just-claimed running Experiment."""
    items = list(
        session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all()
    )
    attempts_by_item = ds._load_attempts_by_item([item.id for item in items])
    for item in items:
        if item.status != "running":
            continue
        attempts = attempts_by_item.get(item.id, [])
        if not attempts:
            continue
        latest = attempts[-1]
        run = session.get(AnalysisRunModel, latest.analysis_run_id)
        if run is None:
            continue
        if run.executor not in _LOCAL_EXECUTORS or run.status != "pending":
            continue
        if latest.launch_requested_at is None:
            if run.worker_pid is not None:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                    "Pending local run has a worker but no durable launch intent.",
                    409,
                )
            try:
                ds.launch_item_attempt(
                    experiment_id=experiment_id,
                    item_id=item.id,
                    attempt_id=latest.id,
                    analysis_service=analysis,
                    coordinator_token=coordinator_token,
                )
            except PlatformError as exc:
                if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                    raise
                if exc.code in FROZEN_AUTHORITY_ITEM_CODES:
                    # launch_item_attempt terminalized the pending run under the
                    # generation fence; the coordinator will project the item to
                    # failed. Do not fail the whole experiment.
                    report.ambiguous_failed += 1
                    continue
                session.expire_all()
                refreshed = session.get(DatasetExperimentAttemptModel, latest.id)
                if refreshed is not None and refreshed.launch_requested_at is not None:
                    # A concurrent recovery actor won the first-launch CAS.
                    continue
                raise
            report.repaired_first_launch += 1
        else:
            outcome = fail_closed_pending_local_run(
                session,
                experiment_id=experiment_id,
                coordinator_token=coordinator_token,
                item_id=item.id,
                attempt_id=latest.id,
                run_id=run.id,
            )
            if outcome == "interrupted":
                report.ambiguous_failed += 1
    return report


def start_recovered_coordinator(session, experiment_id, coordinator_token,
                                job_manager,
                                statuses=("running", "evaluating")):
    """Spawn the recovered coordinator outside any transaction, then persist the
    PID guarded by the fresh token and an active status.

    Precondition: the CALLER has no open SQLAlchemy transaction when
    ``job_manager.start()`` is invoked. ``recover_dataset_experiments``
    explicitly closes any autobegun read transaction before calling this. This
    function itself opens NO SELECT/read transaction before spawning; it spawns
    outside a transaction and only then opens the PID-persistence transaction.

    On spawn failure the Experiment is left with the fresh token and
    ``worker_pid NULL`` so the next startup recovery can retry. Returns the PID,
    or ``None`` on spawn failure.
    """
    try:
        worker_pid = job_manager.start(experiment_id, coordinator_token)
    except Exception:
        return None
    try:
        with session.no_autoflush:
            session.execute(
                update(DatasetExperimentModel)
                .where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                    DatasetExperimentModel.status.in_(statuses),
                )
                .values(worker_pid=worker_pid)
                .execution_options(synchronize_session=False)
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.expire_all()
    return worker_pid


def normalize_orphaned_evaluation(session, experiment_id, evaluation_id,
                                  coordinator_token):
    """Generation-fenced restart normalization of ``pending + PID`` evaluations.

    Atomic DB-side UPDATE proving the current Experiment generation/link. On a
    zero-row miss: fresh generation check -> ``FENCE_LOST`` when stale, otherwise
    a legitimate no-op (the Evaluation is no longer ``pending`` + PID).
    """
    statement = (
        update(DatasetEvaluationModel)
        .where(
            DatasetEvaluationModel.id == evaluation_id,
            DatasetEvaluationModel.status == "pending",
            DatasetEvaluationModel.worker_pid.is_not(None),
            exists().where(
                DatasetExperimentModel.id == experiment_id,
                DatasetExperimentModel.status == "evaluating",
                DatasetExperimentModel.coordinator_token == coordinator_token,
                DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
            ),
        )
        .values(
            status="interrupted",
            error_type="BENCHMARK_INTERRUPTED",
            error_message="Benchmark worker did not survive platform restart.",
            completed_at=_now(),
        )
        .execution_options(synchronize_session=False)
    )
    try:
        result = session.execute(statement)
        rowcount = int(result.rowcount or 0)
        session.commit()
    except Exception:
        session.rollback()
        raise
    if rowcount == 1:
        return True

    generation = session.execute(
        select(DatasetExperimentModel.status,
               DatasetExperimentModel.coordinator_token,
               DatasetExperimentModel.dataset_evaluation_id)
        .where(DatasetExperimentModel.id == experiment_id)
    ).one_or_none()
    if (generation is None or generation[0] != "evaluating"
            or generation[1] != coordinator_token
            or generation[2] != evaluation_id):
        raise PlatformError(
            "DATASET_EXPERIMENT_FENCE_LOST",
            "Coordinator generation no longer owns the experiment.",
            409,
        )
    return False


def recover_dataset_experiments(session, *, job_manager, registry,
                                model_release_store, executor_registry,
                                startup_recovery_cutoff):
    """Restart recovery for active DatasetExperiments.

    Selects ``running`` and ``evaluating`` Experiments. For each: claim a fresh
    generation under the fixed ``startup_recovery_cutoff``, then branch:
    ``running`` -> sealed G4 inference reconciliation + local repair;
    ``evaluating`` -> generation-fenced evaluation normalization only (ZERO
    inference reconciliation, ZERO AnalysisRuns). Finally spawn one coordinator.
    Never reruns completed Items and never auto-retries failed Items.
    """
    report = RecoveryReport()
    ds = DatasetExperimentService(
        session, registry, model_release_store, executor_registry
    )
    analysis = AnalysisService(
        session, registry, None, executor_registry=executor_registry
    )
    experiment_ids = list(
        session.scalars(
            select(DatasetExperimentModel.id)
            .where(DatasetExperimentModel.status.in_(_ACTIVE_RECOVERY_STATUSES))
            .order_by(DatasetExperimentModel.created_at, DatasetExperimentModel.id)
        ).all()
    )
    for experiment_id in experiment_ids:
        expected_token = session.execute(
            select(DatasetExperimentModel.coordinator_token)
            .where(DatasetExperimentModel.id == experiment_id)
        ).scalar_one_or_none()
        claimed_token = claim_experiment_generation(
            session, experiment_id, expected_token, startup_recovery_cutoff
        )
        if claimed_token is None:
            report.skipped += 1
            session.expire_all()
            continue
        report.claimed += 1
        session.expire_all()
        current = session.execute(
            select(DatasetExperimentModel.status,
                   DatasetExperimentModel.dataset_evaluation_id)
            .where(DatasetExperimentModel.id == experiment_id)
        ).one_or_none()
        current_status = current[0] if current is not None else None
        evaluation_id = current[1] if current is not None else None
        try:
            if current_status == "evaluating":
                # Evaluating takeover: NO inference reconciliation/repair.
                if evaluation_id is not None:
                    normalize_orphaned_evaluation(
                        session, experiment_id, evaluation_id, claimed_token
                    )
            else:
                ds.reconcile_items(experiment_id, coordinator_token=claimed_token)
                report = repair_local_pending_runs(
                    session, ds, analysis,
                    experiment_id=experiment_id,
                    coordinator_token=claimed_token,
                    report=report,
                )
        except PlatformError as exc:
            session.rollback()
            if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                report.skipped += 1
                continue
            ds._fail_experiment(
                experiment_id, claimed_token, exc.code, exc.message,
                expected_status=current_status or "running",
            )
            report.invariants_failed += 1
            continue
        except Exception:
            session.rollback()
            raise

        # `repair_local_pending_runs` may have issued read-only ORM SELECTs after
        # its last durable commit. SQLAlchemy autobegin therefore may have opened
        # a read transaction. Close it before subprocess spawn.
        session.rollback()

        started_pid = start_recovered_coordinator(
            session, experiment_id, claimed_token, job_manager
        )
        if started_pid is None:
            report.spawn_failures += 1
        else:
            report.coordinators_started += 1
    return report
