# Phase G G4 — Retry & Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G4 sub-gate):** Complete the approved Phase G **G4 — Retry and
Recovery** on top of the sealed G3-C coordinator core: explicit Retry Failed,
restart recovery with durable coordinator-generation takeover, local
launch-ambiguity fail-closed semantics, and duplicate-active-Attempt protection.
G4 repairs only legitimate lifecycle gaps; normal execution always returns to
the sealed G3-C engine. G4 is not G5 evaluation/API.

**Architecture:** Additive methods on the sealed `DatasetExperimentService`
(`retry_failed`, `_restore_retry_failed`) plus one new control-plane module
`backend/app/dataset_experiments/recovery.py`. G4 reuses the sealed G3-B
`launch_item_attempt` first-launch seam and the G3-C `reconcile_items` /
`_fail_experiment` authority instead of reimplementing them. Startup integrates
DatasetExperiment recovery last, after the existing local stale-Run,
DatasetEvaluation stale, and remote AnalysisRun recovery.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, no PRAGMAs, `expire_on_commit=False`),
subprocess job managers, pytest 9. No GPU, no SSH, no torch.

**Spec:**
`docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`,
especially §3.4, §5, §7, §8, §9, §11, §12, §14, §15, §20.

**Base:** `feature/m9-2-implementation @ 86260299e7591c26cd671fb5f5f9e53598f840cf`.

---

## Global Constraints

1. Database state is authoritative. Never infer launch state from memory or
   process-local state.
2. Retry never revives a terminal `AnalysisRun`. Retry creates a new Attempt and
   a new Run only later through normal G3-C scheduling.
3. Retry Failed itself creates zero Attempts and zero Runs.
4. Completed Items never return to `queued`.
5. Restart never automatically retries failed Items.
6. Local `launch_requested_at IS NULL` + `pending` → safe FIRST launch of the
   SAME Attempt/Run through the sealed G3-B seam.
7. Local `launch_requested_at IS NOT NULL` + `pending` → ZERO relaunch; fail
   closed.
8. remote_gpu restart recovery remains delegated to the existing fenced remote
   recovery. G4 must not touch `remote_execution/*`.
9. A fresh coordinator token is durably committed before any new coordinator
   subprocess may act; older generations are fenced by the token.
10. At most one non-terminal `AnalysisRun` per Item. Duplicate active Attempts
    fail closed; G4 never guesses a winner.
11. No schema/model/migration change. If a schema change appears required, STOP.
12. No `DatasetEvaluation`, no `evaluating`, no Retry Evaluation, no REST, no
    frontend, no new queue infrastructure, no torch.
13. Every durable ownership transition has one explicit commit boundary; no
    transaction spans probes, SSH, provider launch, subprocess spawn, or sleep.

---

## Existing Recovery Audit

This audit is the authoritative statement of what already exists and therefore
is **not** rebuilt.

### Startup ordering (`backend/app/main.py:114-144`)

`create_app()` opens one recovery Session and, in order:

1. `mark_stale_local_cpu_runs_interrupted(recovery_session)` — imported locally
   from `app.remote_execution.recovery` (`main.py:131-136`).
2. `mark_stale_running_evaluations_interrupted(recovery_session)` — imported at
   module top from `app.benchmarks.service` (`main.py:24`, called `main.py:137`).
3. `coordinate_orphaned_remote_runs(...)` — only when
   `app.state.remote_config_available` and a launcher exist (`main.py:138-144`).

There is currently **no** DatasetExperiment recovery call. G4 adds it as step 4.

### Existing local stale AnalysisRun handler

`backend/app/remote_execution/recovery.py:22-37`
`mark_stale_local_cpu_runs_interrupted(session)` sets every `local_cpu`
`running` Run to `status="interrupted"`, `error_type="ANALYSIS_INTERRUPTED"`,
`error_message="Previous local analysis process ended before platform restart."`,
`finished_at=now`. Legacy alias
`app.analysis.service.mark_stale_running_runs_interrupted` (`analysis/service.py:19-27`)
delegates to it. `remote_gpu` runs are never touched.

G4 expectation: by the time DatasetExperiment recovery runs, a local Run that
was physically `running` at crash is already `interrupted`; G4 does not
re-interrupt it and does not relaunch it.

### Existing DatasetEvaluation stale handler

`backend/app/benchmarks/service.py:41-54`
`mark_stale_running_evaluations_interrupted(session)` sets every `running`
`DatasetEvaluation` to `status="interrupted"`, `error_type="BENCHMARK_INTERRUPTED"`.
G4 does not duplicate or alter this.

### Existing remote AnalysisRun recovery

`backend/app/remote_execution/recovery.py:40-108`:

- `find_orphaned_remote_runs(session)` returns `remote_gpu`
  `pending`/`running` run ids.
- `rotate_coordinator_token(metadata)` (`recovery.py:49-58`) replaces
  `coordinator_token` on final local execution metadata without changing
  `build_batch()` / `request_sha256`.
- `coordinate_orphaned_remote_runs(session, *, launcher, remote_config_available,
  seen_run_ids)` (`recovery.py:77-108`) persists a fresh token per orphan and
  launches one coordinator per run. When remote config is unavailable it leaves
  runs untouched and launches nothing.

`backend/app/remote_execution/startup.py` holds the token helpers:
`build_coordinator_metadata` (`13-19`), `find_or_new_coordinator_token`
(`22-26`), `rotate_coordinator_token` (`29-34`).

G4 does not add, change, or call a new remote recovery path. G4 only consumes
the resulting `AnalysisRun` state.

### Existing G3-C coordinator core (sealed at base)

`backend/app/dataset_experiments/service.py`:

- `ReconcileSummary` (`33-39`).
- `_require_experiment_generation` (`521-531`), `_claim_queued_item` (`533-553`),
  `_next_attempt_number` (`555-561`).
- `start_item_attempt` (`429-519`) — Transaction A.
- `launch_item_attempt` (`565-688`) — Transaction B + sealed G3-B first launch;
  rejects `run.status != "pending"` or `run.worker_pid is not None` (`653-664`);
  CAS `_claim_launch_intent` on `launch_requested_at IS NULL` (`690-711`).
- `reconcile_items` (`741-900`) — all-or-nothing invariant validation +
  `Item` projection; detects missing Run, identity mismatch, active-attempt
  ordering, duplicate active attempts, terminal disagreements.
- `select_queued_items` (`902-915`).
- `start_experiment` (`919-998`) — pending→running CAS, token before spawn, spawn
  outside transaction, token/status-guarded `worker_pid` persist, spawn failure
  marks Experiment `failed`.
- `refresh_coordinator_heartbeat` (`1000-1017`),
  `mark_experiment_completed_with_failures` (`1019-1040`),
  `_fail_experiment` (`1042-1063`), `_mark_item_failed` (`1065-1119`).

`backend/app/dataset_experiments/coordinator.py`:

- `EXIT_OUTCOMES`, `is_experiment_level`, `DatasetExperimentCoordinator.step`
  (`73-140`). Step order is token → status → heartbeat → reconcile → validate →
  terminal/schedule. `status != "running"` returns `EXPERIMENT_TERMINAL` and
  schedules nothing.
- All-success returns `CoordinatorOutcome.INFERENCE_COMPLETE` and leaves the
  Experiment `running` (`89-95`).

`backend/app/dataset_experiments/job_manager.py`:
`DatasetExperimentJobManager.start(experiment_id, coordinator_token) -> int`
(`18-39`).

`backend/app/dataset_experiments/worker.py`:
`run_coordinator(experiment_id, coordinator_token, *, settings, poll_interval,
max_iterations)` (`7-58`).

### What G4 must add (and nothing else)

- `DatasetExperimentService.retry_failed` + `_restore_retry_failed`.
- `backend/app/dataset_experiments/recovery.py` with
  `claim_experiment_generation`, `fail_closed_pending_local_run`,
  `repair_local_pending_runs`, `start_recovered_coordinator`,
  `recover_dataset_experiments`, `RecoveryReport`.
- One new bounded AnalysisRun error type `ANALYSIS_LAUNCH_AMBIGUOUS`.
- `main.py` step 4 integration plus `DatasetExperimentJobManager` wiring.
- G4 test files.

### Confirmed absences

- No `retry_failed` / `retry-failed` anywhere in `backend/app`.
- No `dataset_experiments/router.py`; REST is G5.
- No DatasetExperiment recovery module.
- No `remote_execution/*` recovery change is required.

---

## Exact Production Code

### A. `backend/app/dataset_experiments/service.py` — Retry Failed (Task 1)

Add near the other coordinator-owned writes (after `mark_experiment_completed_with_failures`,
before `_fail_experiment`):

```python
    def retry_failed(self, experiment_id, job_manager):
        """G4 explicit Retry Failed. Valid only from ``completed_with_failures``.

        One durable transaction: revalidate status, requeue failed Items only,
        clear retryable Item error projection, transition the Experiment back to
        ``running`` under a fresh coordinator generation, and clear the terminal
        orchestration projection. Creates NO Attempt and NO AnalysisRun.

        Then, outside the transaction: spawn the new coordinator, then persist
        ``worker_pid`` under the fresh-generation guard. A spawn failure
        generation-fenced-restores the pre-retry terminal Experiment projection
        and the requeued Items, but ONLY if this generation still owns the
        Experiment (see ``_restore_retry_failed``).
        """
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        if experiment.status != "completed_with_failures":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVALID_TRANSITION",
                "Only a completed_with_failures experiment can retry failed items.",
                409,
            )

        terminal_snapshot = {
            "completed_at": experiment.completed_at,
            "heartbeat_at": experiment.heartbeat_at,
            "coordinator_token": experiment.coordinator_token,
            "worker_pid": experiment.worker_pid,
            "error_type": experiment.error_type,
            "error_message": experiment.error_message,
        }

        failed_rows = self.session.execute(
            select(
                DatasetExperimentItemModel.id,
                DatasetExperimentItemModel.last_error_type,
                DatasetExperimentItemModel.last_error_message,
            ).where(
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "failed",
            )
        ).all()
        if not failed_rows:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVALID_TRANSITION",
                "Experiment has no failed items to retry.",
                409,
            )
        snapshot = [(row[0], row[1], row[2]) for row in failed_rows]

        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            claimed_count = 0
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "completed_with_failures",
                    )
                    .values(
                        status="running",
                        coordinator_token=token,
                        worker_pid=None,
                        heartbeat_at=now,
                        completed_at=None,
                        error_type=None,
                        error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                claimed_count = int(claimed.rowcount or 0)
                if claimed_count == 1:
                    self.session.execute(
                        update(DatasetExperimentItemModel)
                        .where(
                            DatasetExperimentItemModel.experiment_id == experiment_id,
                            DatasetExperimentItemModel.status == "failed",
                        )
                        .values(
                            status="queued",
                            last_error_type=None,
                            last_error_message=None,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
            if claimed_count != 1:
                self.session.rollback()
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVALID_TRANSITION",
                    "Only a completed_with_failures experiment can retry failed items.",
                    409,
                )
            self.session.commit()
        except PlatformError:
            raise
        except Exception:
            self.session.rollback()
            raise

        try:
            worker_pid = job_manager.start(experiment_id, token)
        except Exception as exc:
            restored = self._restore_retry_failed(
                experiment_id, token, snapshot, terminal_snapshot
            )
            if not restored:
                raise PlatformError(
                    "DATASET_EXPERIMENT_FENCE_LOST",
                    "Retry Failed lost its coordinator generation before the spawn "
                    "failure could be compensated; a newer generation owns the "
                    "experiment and no Item was mutated.",
                    409,
                ) from exc
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unable to start DatasetExperiment coordinator for retry.",
            ) from exc

        try:
            with self.session.no_autoflush:
                self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.expire_all()
        return self.session.get(DatasetExperimentModel, experiment_id)

    def _restore_retry_failed(self, experiment_id, coordinator_token,
                              item_snapshot, terminal_snapshot):
        """Generation-fenced compensation for a Retry Failed spawn failure.

        The compensation transaction FIRST must durably re-acquire ownership of
        the retry generation:

            CAS UPDATE dataset_experiments
                SET status='completed_with_failures',
                    coordinator_token = terminal_snapshot token,
                    worker_pid = terminal_snapshot pid,
                    heartbeat_at = terminal_snapshot heartbeat,
                    completed_at = terminal_snapshot completed_at,
                    error_type = terminal_snapshot error_type,
                    error_message = terminal_snapshot error_message
                WHERE id=:id AND coordinator_token=:retry_token AND status='running'

        Only when that UPDATE affects exactly one row (rowcount == 1) does this
        caller still own the generation, and only THEN are the requeued Items
        restored inside the SAME transaction. Each Item UPDATE also requires
        ``id`` AND ``experiment_id`` AND the expected retry-created state
        ``status='queued'``.

        If the ownership CAS affects zero rows (a newer generation already took
        over), the transaction is rolled back immediately, ZERO Items are
        mutated, and this returns ``False`` so the caller raises
        ``DATASET_EXPERIMENT_FENCE_LOST`` instead of clobbering the newer owner.

        Restoring ``completed_at``/``heartbeat_at``/``coordinator_token``/
        ``worker_pid``/``error_type``/``error_message`` from the pre-retry
        terminal snapshot makes this a genuine terminal-projection restore, not a
        normalized re-write.

        Used only when ``job_manager.start`` raises, which proves no coordinator
        process was created.
        """
        now = datetime.now(timezone.utc)
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="completed_with_failures",
                        coordinator_token=terminal_snapshot["coordinator_token"],
                        worker_pid=terminal_snapshot["worker_pid"],
                        heartbeat_at=terminal_snapshot["heartbeat_at"],
                        completed_at=terminal_snapshot["completed_at"],
                        error_type=terminal_snapshot["error_type"],
                        error_message=terminal_snapshot["error_message"],
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(claimed.rowcount or 0) != 1:
                    self.session.rollback()
                    return False
                for item_id, error_type, error_message in item_snapshot:
                    self.session.execute(
                        update(DatasetExperimentItemModel)
                        .where(
                            DatasetExperimentItemModel.id == item_id,
                            DatasetExperimentItemModel.experiment_id == experiment_id,
                            DatasetExperimentItemModel.status == "queued",
                        )
                        .values(
                            status="failed",
                            last_error_type=error_type,
                            last_error_message=error_message,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True
```

### B. `backend/app/dataset_experiments/recovery.py` (new, final after Task 3)

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import exists, or_, select, update

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService


@dataclass
class RecoveryReport:
    claimed: int = 0
    skipped: int = 0
    repaired_first_launch: int = 0
    ambiguous_failed: int = 0
    invariants_failed: int = 0
    coordinators_started: int = 0
    spawn_failures: int = 0


_ACTIVE_RECOVERY_STATUSES = ("running",)
_AMBIGUOUS_ERROR_TYPE = "ANALYSIS_LAUNCH_AMBIGUOUS"
_AMBIGUOUS_ERROR_MESSAGE = (
    "Local launch intent is durable but the run is still pending after platform "
    "restart; the launch outcome is ambiguous. Fail closed and require explicit "
    "Retry Failed."
)


def _now():
    return datetime.now(timezone.utc)


def claim_experiment_generation(session, experiment_id, expected_token,
                                startup_recovery_cutoff):
    """Durably take ownership of a running Experiment under a fresh token.

    CAS on the previously observed ``coordinator_token`` (or ``IS NULL`` when the
    observed token is ``None``) AND on the startup recovery cutoff:

        heartbeat_at IS NULL OR heartbeat_at < startup_recovery_cutoff

    The cutoff makes a generation freshly claimed in the CURRENT startup epoch
    ineligible to be stolen by any other recovery call in the SAME startup (its
    ``heartbeat_at`` is written as ``now`` and is therefore ``>=`` the cutoff).
    A later real platform restart uses a later cutoff and can recover a prior
    crash-after-claim state.

    Timestamp comparison is strictly ``<``; equality is NOT eligible. Commits
    the fresh token, clears ``worker_pid``, and writes ``heartbeat_at=now``
    BEFORE any coordinator subprocess may act. Only one racer can win; the loser
    returns ``None``.
    """
    fresh_token = f"coord_{uuid4().hex}"
    cutoff_predicate = or_(
        DatasetExperimentModel.heartbeat_at.is_(None),
        DatasetExperimentModel.heartbeat_at < startup_recovery_cutoff,
    )
    statement = (
        update(DatasetExperimentModel)
        .where(
            DatasetExperimentModel.id == experiment_id,
            DatasetExperimentModel.status == "running",
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
    """Generation-fenced ``pending -> interrupted`` for an ambiguous local Run.

    The FINAL durable UPDATE atomically proves the whole ownership chain in the
    database; there is no SELECT-then-UPDATE:

        AnalysisRun.id == run_id AND AnalysisRun.status == 'pending'
        EXISTS Experiment(id == experiment_id AND status == 'running'
                          AND coordinator_token == coordinator_token)
        EXISTS Item(id == item_id AND experiment_id == experiment_id
                    AND status == 'running')
        EXISTS Attempt(id == attempt_id AND experiment_item_id == item_id
                       AND analysis_run_id == run_id)

    Returns ``"interrupted"`` iff this caller performed the transition; returns
    ``"already_interrupted"`` when the Run is already terminal (idempotent
    concurrent CAS). On a zero-row result it performs a fresh generation check:

      * generation lost -> ``DATASET_EXPERIMENT_FENCE_LOST``;
      * missing/unknown ownership -> ``DATASET_EXPERIMENT_INVARIANT_VIOLATION``.

    Never silently succeeds on ownership corruption.
    """
    statement = (
        update(AnalysisRunModel)
        .where(
            AnalysisRunModel.id == run_id,
            AnalysisRunModel.status == "pending",
            exists().where(
                DatasetExperimentModel.id == experiment_id,
                DatasetExperimentModel.status == "running",
                DatasetExperimentModel.coordinator_token == coordinator_token,
            ),
            exists().where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "running",
            ),
            exists().where(
                DatasetExperimentAttemptModel.id == attempt_id,
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                DatasetExperimentAttemptModel.analysis_run_id == run_id,
            ),
        )
        .values(
            status="interrupted",
            error_type=_AMBIGUOUS_ERROR_TYPE,
            error_message=_AMBIGUOUS_ERROR_MESSAGE,
            finished_at=_now(),
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
        return "interrupted"

    generation = session.execute(
        select(DatasetExperimentModel.status, DatasetExperimentModel.coordinator_token)
        .where(DatasetExperimentModel.id == experiment_id)
    ).one_or_none()
    if generation is None or generation[0] != "running" or generation[1] != coordinator_token:
        raise PlatformError(
            "DATASET_EXPERIMENT_FENCE_LOST",
            "Coordinator generation no longer owns the experiment.",
            409,
        )
    run_status = session.execute(
        select(AnalysisRunModel.status).where(AnalysisRunModel.id == run_id)
    ).scalar_one_or_none()
    if run_status == "interrupted":
        return "already_interrupted"
    if run_status is None:
        raise PlatformError(
            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
            "Ambiguous local run no longer exists.",
            409,
        )
    raise PlatformError(
        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
        "Ambiguous local run could not be terminalized from its current state.",
        409,
    )


def repair_local_pending_runs(session, ds, analysis, *, experiment_id,
                              coordinator_token, report):
    """Repair local_cpu ``pending`` runs for a just-claimed running Experiment.

    For each ``running`` Item whose latest Attempt's Run is a local_cpu
    ``pending`` Run:
      * ``launch_requested_at IS NULL`` -> safe FIRST launch of the SAME
        Attempt/Run through the sealed G3-B ``launch_item_attempt`` seam;
      * ``launch_requested_at IS NOT NULL`` -> ambiguous; fail closed with ZERO
        provider launch.
    Remote runs and terminal runs are left for G3-C reconciliation / remote
    recovery. Mutates and returns ``report``.
    """
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
        if run.executor != "local_cpu" or run.status != "pending":
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
                                job_manager):
    """Spawn the recovered coordinator outside any transaction, then persist the
    PID guarded by the fresh token and ``status == running``.

    On spawn failure the Experiment is left ``running`` with the fresh token and
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
                    DatasetExperimentModel.status == "running",
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


def recover_dataset_experiments(session, *, job_manager, registry,
                                model_release_store, executor_registry,
                                startup_recovery_cutoff):
    """Restart recovery for active DatasetExperiments.

    Selects only ``running`` Experiments (G5 owns ``evaluating``). For each:
    claim a fresh generation under the fixed ``startup_recovery_cutoff``,
    reconcile real AnalysisRun state, repair local pending launch gaps, fail
    closed on invariants, then spawn one coordinator. Never reruns completed
    Items and never auto-retries failed Items.
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
        try:
            ds.reconcile_items(experiment_id, coordinator_token=claimed_token)
            report = repair_local_pending_runs(
                session, ds, analysis,
                experiment_id=experiment_id,
                coordinator_token=claimed_token,
                report=report,
            )
        except PlatformError as exc:
            if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
                report.skipped += 1
                continue
            ds._fail_experiment(
                experiment_id, claimed_token, exc.code, exc.message
            )
            report.invariants_failed += 1
            continue
        started_pid = start_recovered_coordinator(
            session, experiment_id, claimed_token, job_manager
        )
        if started_pid is None:
            report.spawn_failures += 1
        else:
            report.coordinators_started += 1
    return report
```

**Task 2 stage delta:** at the end of Task 2 the module contains everything
above EXCEPT `start_recovered_coordinator`, the two report fields
`coordinators_started`/`spawn_failures`, and the final spawn block in
`recover_dataset_experiments`. Its signature at Task 2 is
`recover_dataset_experiments(session, *, registry, model_release_store,
executor_registry, startup_recovery_cutoff)`. Task 3 adds the deferred pieces
exactly as shown. `claim_experiment_generation(..., startup_recovery_cutoff)`
and the generation-fenced `fail_closed_pending_local_run(...)` are part of the
Task 2 module.

### C. `backend/app/main.py` — startup integration (Task 4)

Add to the module-level imports:

```python
from datetime import datetime, timezone

from app.dataset_experiments import recovery as dataset_experiment_recovery
from app.dataset_experiments.job_manager import DatasetExperimentJobManager
```

Capture ONE fixed startup recovery cutoff immediately before the existing
recovery `with` block, then pass it to the DatasetExperiment recovery call:

```python
    startup_recovery_cutoff = datetime.now(timezone.utc)

    with app.state.database.session_factory() as recovery_session:
        # existing recovery_session block body (steps 1-3 above) stays unchanged
```

Inside that `with` block, immediately after the remote-recovery `if` block
(currently ending at `main.py:144`):

```python
        dataset_experiment_recovery.recover_dataset_experiments(
            recovery_session,
            job_manager=DatasetExperimentJobManager(settings),
            registry=app.state.pipeline_registry,
            model_release_store=app.state.model_release_store,
            executor_registry=app.state.executor_registry,
            startup_recovery_cutoff=startup_recovery_cutoff,
        )
```

Resulting order inside the block:

```text
1. mark_stale_local_cpu_runs_interrupted(recovery_session)
2. mark_stale_running_evaluations_interrupted(recovery_session)
3. if remote available: coordinate_orphaned_remote_runs(recovery_session, ...)
4. dataset_experiment_recovery.recover_dataset_experiments(recovery_session, ...)
```

---

## G4 FILES / INTERFACES

| File | Change | Key interfaces |
|---|---|---|
| `backend/app/dataset_experiments/service.py` | add | `retry_failed(experiment_id, job_manager)`, `_restore_retry_failed(experiment_id, coordinator_token, item_snapshot, terminal_snapshot)` |
| `backend/app/dataset_experiments/recovery.py` | new | `RecoveryReport`, `claim_experiment_generation(session, id, expected_token, startup_recovery_cutoff)`, `fail_closed_pending_local_run(session, *, experiment_id, coordinator_token, item_id, attempt_id, run_id)`, `repair_local_pending_runs`, `start_recovered_coordinator`, `recover_dataset_experiments(..., startup_recovery_cutoff)` |
| `backend/app/main.py` | modify | step-4 startup call + `DatasetExperimentJobManager` wiring |
| `backend/tests/dataset_experiment_fixtures.py` | add | `G4Recover` job-manager probe helper, `recover_once` |
| `backend/tests/test_dataset_experiment_retry_failed.py` | new | Retry Failed behavior |
| `backend/tests/test_dataset_experiment_local_launch_recovery.py` | new | local safety + ambiguity + invariants |
| `backend/tests/test_dataset_experiment_restart_recovery.py` | new | generation takeover + all-success seam + G5 boundary |
| `backend/tests/test_dataset_experiment_startup_order.py` | new | ordering |
| `backend/tests/test_dataset_experiment_g4_regression.py` | new | guards |

Explicitly NOT modified: any model/migration file, `remote_execution/*`,
`AnalysisRun` persistence, plugin/model science, frontend, benchmarks
evaluation science.

---

## RETRY FAILED TRANSACTION

Exact durable order for `retry_failed`:

```text
PRECONDITION (read-only, no writes):
    experiment exists
    experiment.status == completed_with_failures
    at least one Item.status == failed
    snapshot failed Items (id, last_error_type, last_error_message)
    snapshot terminal Experiment projection:
        completed_at, heartbeat_at, coordinator_token, worker_pid,
        error_type, error_message

TRANSACTION 1 (one commit):
    CAS UPDATE dataset_experiments
        SET status='running', coordinator_token=<fresh>, worker_pid=NULL,
            heartbeat_at=now, completed_at=NULL, error_type=NULL,
            error_message=NULL
        WHERE id=:id AND status='completed_with_failures'
    IF rowcount != 1: rollback; raise DATASET_EXPERIMENT_INVALID_TRANSITION
    UPDATE dataset_experiment_items
        SET status='queued', last_error_type=NULL, last_error_message=NULL,
            updated_at=now
        WHERE experiment_id=:id AND status='failed'
    COMMIT

    NO Attempt row and NO AnalysisRun row is created in Transaction 1.

OUTSIDE TRANSACTION:
    worker_pid = job_manager.start(experiment_id, fresh_token)
    ON raise:
        restored = _restore_retry_failed(
            experiment_id, fresh_token, item_snapshot, terminal_snapshot)
        IF restored: raise DATASET_EXPERIMENT_ORCHESTRATION_FAILED
        ELSE:        raise DATASET_EXPERIMENT_FENCE_LOST  # newer generation owns it

TRANSACTION 2 (one commit):
    UPDATE dataset_experiments SET worker_pid=:pid
        WHERE id=:id AND coordinator_token=:fresh AND status='running'
    COMMIT
```

Settled semantics:

| Question | Decision | Rationale |
|---|---|---|
| `started_at` | **Preserved** | experiment-level first-start; Retry is a new generation of the same experiment, not a new experiment. Only `heartbeat_at` refreshes. |
| heartbeat initialization | `heartbeat_at = now` in Transaction 1 | new generation is live before spawn. |
| `completed_at` | cleared to `NULL` in Transaction 1 | experiment is no longer terminal. |
| Item `updated_at` | set to `now` for requeued Items | projection changed. |
| error fields | Experiment `error_type`/`error_message` cleared; Item `last_error_type`/`last_error_message` cleared | stale terminal projection must not survive a retry. Snapshot kept for spawn-failure restore. |
| CAS condition | `WHERE status='completed_with_failures'` | exactly one concurrent Retry Failed can win. |
| concurrent Retry Failed | first commit wins; loser rowcount 0 → `DATASET_EXPERIMENT_INVALID_TRANSITION`; loser spawns nothing | no double generation. |
| stale Session loser | loser's ORM object may still read `completed_with_failures`; the real CAS still fails → zero spawn | DB CAS is authority, not the Session. |
| `worker_pid` | set `NULL` before spawn, persisted after spawn under fresh token + running guard | stale PID never confers authority. |
| No failed Items | `DATASET_EXPERIMENT_INVALID_TRANSITION` | illegal Retry Failed input. |
| Wrong state (pending/running/completed/failed) | `DATASET_EXPERIMENT_INVALID_TRANSITION` | §5.1 allows `completed_with_failures -> running` only. |

`retry_failed` never revives a terminal Run: it does not read, reset, or delete
any `AnalysisRun`; requeued Items later obtain a NEW Attempt + NEW Run through
normal G3-C scheduling (`start_item_attempt` → `launch_item_attempt`).

---

## RETRY SPAWN-FAILURE SEMANTICS

Chosen design: **generation-fenced restore of the pre-retry terminal Experiment
projection plus the queued Item projection**, because `job_manager.start` raising
proves `subprocess.Popen` did not create a coordinator process (the job manager
returns `.pid` immediately after `Popen`; nothing else can raise).

The compensation is a single transaction with a strict ownership claim:

1. `UPDATE dataset_experiments ... WHERE id=:id AND coordinator_token=:retry_token
   AND status='running'` restores `status='completed_with_failures'` and the
   snapshotted `completed_at`, `heartbeat_at`, `coordinator_token`,
   `worker_pid`, `error_type`, `error_message`. This UPDATE is the ownership
   claim for the entire compensation.
2. If rowcount != 1: rollback immediately, mutate ZERO Items, return `False`.
   `retry_failed` then raises `DATASET_EXPERIMENT_FENCE_LOST`, and the newer
   generation retains untouched ownership.
3. Only if rowcount == 1: restore each requeued Item inside the SAME transaction
   to `failed` with its snapshotted `last_error_type`/`last_error_message`.
   Each Item UPDATE requires `id` AND `experiment_id == experiment_id` AND
   `status == 'queued'`.
4. Commit once; on any exception rollback and re-raise.

This is a genuine terminal-projection restore (timestamps, token, PID, and error
fields are the pre-retry values), not a normalized re-write.

Superseded-compensation semantics: `_restore_retry_failed` returns `True` iff it
still owned the generation and restored it; `False` (with zero Item mutation)
when a newer generation already owns the Experiment. The caller maps `False` to
`DATASET_EXPERIMENT_FENCE_LOST`; it never reports
`DATASET_EXPERIMENT_ORCHESTRATION_FAILED` for a compensation that did nothing.

Rejected alternative: marking the Experiment `failed`. It would strand the
requeued Items as `queued` under a terminal status that cannot be retried
(Retry Failed is only valid from `completed_with_failures`), violating
"no hidden queued Items under a terminal status that claims otherwise" and
"retryability where appropriate". The state machine cannot express a safe
`failed`-with-retryable-items outcome, so that option is illegal.

Crash-window distinction (documented, intentional):

- **Process crash after Transaction 1 commit, before/at spawn**: the durable
  retry intent remains (`running`, fresh token, queued failed Items). The next
  startup recovery claims a new generation and spawns a coordinator, so the
  retry takes effect. This is a valid, recoverable outcome — not a lost update.
- **Observed spawn exception (no crash)**: no coordinator process was created.
  The compensation runs synchronously ONLY while the retry generation still owns
  the Experiment; if a newer generation has taken over, the compensation is
  abandoned with zero Item mutation and `DATASET_EXPERIMENT_FENCE_LOST` is
  raised instead of writing under a stale generation.

Both windows preserve: no hidden queued Items under a terminal status, no
invented state, no illegal transition, and retryability.

---

## COORDINATOR GENERATION RECOVERY

Exact durable order for taking ownership of an already-active Experiment:

```text
READ: expected_token = dataset_experiments.coordinator_token of the running Experiment
      startup_recovery_cutoff = ONE fixed datetime captured by application startup

TRANSACTION (one commit) — claim_experiment_generation:
    CAS UPDATE dataset_experiments
        SET coordinator_token=<fresh>, worker_pid=NULL, heartbeat_at=now
        WHERE id=:id AND status='running'
          AND (heartbeat_at IS NULL OR heartbeat_at < startup_recovery_cutoff)
          AND coordinator_token = :expected_token     # or IS NULL if expected is NULL
    COMMIT
    IF rowcount != 1: return None (loser; no token, no spawn)

OUTSIDE TRANSACTION:
    pid = job_manager.start(experiment_id, fresh_token)

TRANSACTION (one commit) — start_recovered_coordinator:
    UPDATE dataset_experiments SET worker_pid=:pid
        WHERE id=:id AND coordinator_token=:fresh AND status='running'
    COMMIT
```

Requirements mapping:

1. Fresh token: `coord_<uuid4>`.
2. Token persisted BEFORE the new coordinator subprocess can act: the claim
   transaction commits before `job_manager.start`.
3. Stale old coordinator loses all G3-C fenced writes: every G3-C write
   (`_claim_queued_item`, `_claim_launch_intent`, `reconcile_items`,
   `_mark_item_failed`, `refresh_coordinator_heartbeat`) already embeds the
   `coordinator_token` EXISTS/CAS guard; after rotation the old token fails and
   the coordinator returns `CoordinatorOutcome.FENCE_LOST`.
4. Stale `worker_pid` is not ownership authority: the CAS reads only
   `coordinator_token`, and claim clears `worker_pid` regardless of its old
   value.
5. New PID persisted only under the new generation: the PID update is guarded by
   `coordinator_token == fresh AND status == running`.
6. Process spawn occurs outside any DB transaction.
7. No long transaction around subprocess launch.
8. Spawn/commit crash windows:
   - crash after claim commit, before spawn: Experiment `running`, fresh token,
     `worker_pid NULL`; next startup recovers again (new generation).
   - crash after spawn, before PID commit: coordinator exists but PID is not
     recorded; the next startup rotates the token, fencing the orphaned
     coordinator, then spawns a replacement. Accepted transient older
     coordinator, fenced before side effects.
   - crash after PID commit: normal.
9. Same-startup re-entry protected: the claim additionally requires
   `heartbeat_at IS NULL OR heartbeat_at < startup_recovery_cutoff`, so a
   generation claimed in the current startup is not stealable by another
   recovery call in the same epoch.

### Stale Session and two-session race analysis

`claim_experiment_generation` is a true compare-and-swap on the previously read
token, not a blind `WHERE status='running'` update:

- Two actors both read `T`. Both issue
  `... WHERE coordinator_token = T`. The database serializes the writes; the
  first commits `T -> T1`. The second's predicate no longer matches (`T1 != T`),
  so rowcount is 0 and it returns `None`. Exactly one CURRENT generation exists.
- The loser never spawns and never writes. It may have a stale ORM object, but
  ownership is decided by the DB predicate, never by Session state.
- `expected_token IS NULL` is handled with
  `coordinator_token IS NULL`; the same serialization applies.

### Startup recovery cutoff (epoch)

Application startup captures exactly ONE cutoff:

```python
startup_recovery_cutoff = datetime.now(timezone.utc)
```

and passes it to `recover_dataset_experiments`, which passes the SAME value to
every `claim_experiment_generation` call in that startup pass. It is never
regenerated per Experiment.

Eligibility predicate:

```text
heartbeat_at IS NULL OR heartbeat_at < startup_recovery_cutoff
```

Strict `<`; equality is not eligible. A successful claim writes
`heartbeat_at = now`, which is `>=` the cutoff, so:

- a stale pre-restart running Experiment (old heartbeat or NULL) is eligible;
- a generation freshly claimed in the CURRENT startup cannot be stolen by any
  other recovery call in the SAME startup;
- the NEXT real platform restart uses a later cutoff, so a prior
  crash-after-claim state (fresh token + fresh heartbeat) becomes eligible again
  and is recoverable.

### Deployment concurrency statement

WISA V1 startup recovery is a **hard single-actor invariant**: exactly one
FastAPI startup process performs recovery against a given database. The token CAS
alone does NOT make arbitrary sequential recovery actors safe — a later actor can
read a just-written fresh token and attempt to take over — which is exactly why
the startup cutoff exists. Under one startup process, cutoff plus token CAS
guarantees a single current generation.

If multi-process concurrent application startup must be supported, a stronger
cross-process startup leader/lease is required; token CAS plus cutoff is not
claimed to elect a cross-process recovery owner. That is out of G4 scope and
would be reported as an architectural STOP rather than solved with a weak
in-process lease.

---

## B→C CROSS-GENERATION RACE RESOLUTION

The dangerous window is inside the sealed G3-B seam:

```text
Actor A owns generation T1
A: Transaction B commit  ->  launch_requested_at != NULL
A: physical launch       ->  not yet called
```

Sealed G3-B intentionally does NOT cancel an already-authorized post-B physical
launch merely because the DatasetExperiment coordinator token later rotates.
Therefore no recovery pass may rotate A's fresh current generation and then
classify A's durable B-commit as crash ambiguity.

Failure mode if a second same-epoch recovery pass were allowed to take over:

```text
B reads T1
B rotates T1 -> T2
B sees local Run pending + marker != NULL
B classifies it as restart ambiguity and interrupts the Run
A still proceeds from its already-durable B commit to physical launch
=> one actor says "ZERO launch" while another physically launches
```

Resolution (no schema change):

1. The startup cutoff makes a generation claimed in the current startup
   ineligible for re-claim in the SAME startup epoch (its `heartbeat_at` is
   `>=` the cutoff).
2. A second recovery pass in the SAME startup therefore cannot rotate A's fresh
   generation, cannot observe A's Run as "ambiguous", and performs zero launch
   and zero Run mutation. A's single physical first launch remains authoritative.
3. A genuine NEXT platform restart captures a later cutoff, so it can recover a
   truly crashed crash-after-claim state (fresh token + old-enough heartbeat).
4. With a single startup process (hard deployment invariant), no concurrent
   same-epoch recovery pass exists at all; the cutoff additionally protects
   against accidental same-process re-entry.

`expire_on_commit=False` matters here: Actor A's Session may hold an identity-map
`AnalysisRun` that is still `pending` even if another Session wrote to it, so G4
never relies on `launch_prepared_run()` re-reading fresh state. The cutoff keeps
B from writing at all, which is what makes A's post-B launch deterministic.

---

## TRANSACTION HYGIENE

Every G4 recovery write follows the sealed G3-C standard — explicit
`try / commit / except rollback / raise`, never reusing a failed Session:

| Write | Hygiene |
|---|---|
| `claim_experiment_generation` | `try: session.execute(CAS); session.commit()` / `except: session.rollback(); raise` |
| `fail_closed_pending_local_run` | `try: session.execute(generation-fenced UPDATE); session.commit()` / `except: session.rollback(); raise` |
| `start_recovered_coordinator` PID persist | `try: session.execute(guarded UPDATE); session.commit()` / `except: session.rollback(); raise` |
| `retry_failed` Transaction 1 | guarded CAS + Item requeue, `commit`; `except PlatformError: raise`; `except Exception: rollback; raise` |
| `retry_failed` Transaction 2 (PID) | `try: ...; commit` / `except: rollback; raise` |
| `_restore_retry_failed` | ownership CAS first; `rowcount != 1 -> rollback; return False`; else Item restore + `commit`; `except: rollback; raise` |

Spawn and provider physical launch stay outside every DB transaction.

---

## LOCAL RECOVERY STATE MATRIX

For the latest Attempt of a `running` Item, after the existing local stale-run
handler and remote recovery have already run:

| Latest Run state | Executor | `launch_requested_at` | G4 action | `Item` after coordinator reconcile |
|---|---|---|---|---|
| `completed` | any | any | none; never relaunch | `completed` |
| `failed` | any | any | none; never relaunch | `failed` |
| `interrupted` | any | any | none; never relaunch | `failed` |
| `pending` | `local_cpu` | `NULL` | safe FIRST launch of SAME Attempt/Run (G3-B: marker commit → physical launch) | keeps `running` until the Run turns terminal |
| `pending` | `local_cpu` | non-`NULL` | fail closed: Run → `interrupted` + `ANALYSIS_LAUNCH_AMBIGUOUS`; ZERO launch | `failed` |
| `running` | `local_cpu` | any | not present at G4: the pre-G4 stale handler already made it `interrupted` | `failed` |
| `pending` / `running` | `remote_gpu` | any | delegate to existing fenced remote recovery; G4 does not apply local marker logic and never launches | reflects remote recovery outcome |
| missing Attempt for a `running` Item | any | n/a | invariant via `reconcile_items` → Experiment `failed` | fail closed |
| Attempt → missing Run | any | n/a | invariant via `reconcile_items` → Experiment `failed` | fail closed |
| Run recording / pipeline / executor mismatch | any | n/a | invariant via `reconcile_items` → Experiment `failed` | fail closed |
| `pending` local Run with `worker_pid != NULL` and marker `NULL` | `local_cpu` | `NULL` | invariant (launch without durable intent) → Experiment `failed` | fail closed |
| ≥2 active Attempts for one Item | any | any | invariant via `reconcile_items` → Experiment `failed` | fail closed |
| Experiment with zero Items | n/a | n/a | invariant via `reconcile_items` → Experiment `failed` | fail closed |

"Impossible metadata" cases are never auto-repaired: ownership corruption fails
closed using the existing G3-C invariant authority.

---

## SAFE A→B FIRST-LAUNCH RECOVERY

Persisted state on restart:

```text
Item.status == running
Attempt exists (latest)
AnalysisRun.status == pending
Attempt.launch_requested_at IS NULL
```

Meaning: Transaction A committed, Transaction B did not, so **no physical launch
has been requested**. Recovery performs the FIRST launch of the SAME
Attempt/SAME Run through the sealed G3-B `launch_item_attempt`:

1. `launch_item_attempt` revalidates frozen identity and the full ownership chain
   (Experiment → Item → Attempt → Run): pipeline/executor/recording match,
   `run.status == "pending"`, `run.worker_pid is None`.
2. Transaction B CAS `_claim_launch_intent` sets `launch_requested_at=now` on the
   SAME Attempt and commits, generation-fenced by the fresh token.
3. Only the CAS winner calls the sealed `AnalysisService.launch_prepared_run`.

Properties enforced:

- same Item, same Attempt, same Run — zero replacement Attempt/Run;
- `launch_requested_at COMMIT` strictly before `provider.launch`;
- exactly one physical launch, even with a concurrent recovery actor, because the
  CAS is on `launch_requested_at IS NULL`; the loser re-reads the marker and
  treats the claim as contended rather than failing the Experiment;
- if the CAS lose is followed by `launch_requested_at IS NOT NULL`, recovery
  skips; otherwise the original error propagates (fail closed).

---

## AMBIGUOUS B→LAUNCH FAIL-CLOSED SEMANTICS

Persisted state on restart:

```text
Item.status == running
Attempt exists (latest)
AnalysisRun.status == pending
Attempt.launch_requested_at IS NOT NULL
```

For `local_cpu` the physical launch outcome is unknowable. Recovery:

1. performs ZERO provider launch and never calls same-Run relaunch;
2. generation-fenced `pending -> interrupted` with
   `error_type="ANALYSIS_LAUNCH_AMBIGUOUS"` (bounded, new, non-scientific) and a
   fixed message. The FINAL UPDATE atomically proves the ownership chain
   `Experiment(token/status) -> Item -> Attempt -> Run` with `EXISTS` predicates;
   a stale generation gets `DATASET_EXPERIMENT_FENCE_LOST` with zero mutation;
3. preserves all Attempt/Run history (no deletes, no resets, no new Attempt);

The terminal state is `interrupted` (not `failed`) because the outcome is
aborted/unknown, matching the existing local stale-run convention
(`ANALYSIS_INTERRUPTED`); reconciliation already projects `interrupted` to
`Item.failed` and copies `error_type`/`error_message`.

Durable chain to retryability:

```text
AnalysisRun terminal authority (interrupted + ANALYSIS_LAUNCH_AMBIGUOUS)
  -> Item.failed projection via reconcile_items
  -> Experiment.completed_with_failures via coordinator
  -> explicit Retry Failed (completed_with_failures -> running)
  -> normal G3-C scheduling creates a NEW Attempt + NEW AnalysisRun
```

No second source of truth is created on the Item; `Item.last_error_*` remains a
projection of the authoritative Run.

---

## REMOTE GPU BOUNDARY

- Startup order preserves: local stale Run → DatasetEvaluation stale → remote
  AnalysisRun recovery → DatasetExperiment recovery.
- `repair_local_pending_runs` acts only when `run.executor == "local_cpu" AND
  run.status == "pending"`. A `remote_gpu` pending/running Run is never touched
  and never uses `launch_requested_at` semantics.
- G4 does not rebuild remote requests, does not change `build_batch()` /
  `request_sha256`, does not rotate remote tokens through a new mechanism, does
  not SSH, and does not import or bypass `remote_execution.recovery`.
- There is **no** `remote_execution/*` production diff in G4.
- Regression proof: existing `test_remote_startup_recovery.py`,
  `test_remote_stale_helper_regression.py`, and `test_remote_coordinator_fencing.py`
  remain green, plus a G4 test asserting a `remote_gpu` pending Run receives
  zero launches from DatasetExperiment recovery.

---

## DUPLICATE ACTIVE ATTEMPT RULE

At most one non-terminal Run per Item. G4 never creates a new Attempt while an
existing Attempt has a non-terminal Run. On recovery:

- G4 reuses `reconcile_items` as the single authority. For a `running` Item it
  requires `active == [latest]`; for a `queued` Item it requires no active
  Attempts. Any violation raises `DATASET_EXPERIMENT_INVARIANT_VIOLATION`.
- Recovery catches that invariant, calls `_fail_experiment` with the fresh
  token, and stops; it does not guess a winner, does not kill a Run, and does
  not launch.
- All Runs/results are preserved untouched.

G4 does not duplicate this logic; it delegates to the sealed reconciliation.

---

## STARTUP ORDER

`main.py` recovery block becomes exactly:

```text
1. existing local stale AnalysisRun handling
   mark_stale_local_cpu_runs_interrupted(recovery_session)
2. existing DatasetEvaluation stale handling
   mark_stale_running_evaluations_interrupted(recovery_session)
3. existing remote AnalysisRun recovery (only if remote config available)
   coordinate_orphaned_remote_runs(recovery_session, launcher=..., ...)
4. DatasetExperiment recovery
   recover_dataset_experiments(recovery_session, job_manager=..., registry=...,
                               model_release_store=..., executor_registry=...,
                               startup_recovery_cutoff=captured_once_before_the_with_block)
```

DatasetExperiment recovery runs last so it observes the post-recovery
`AnalysisRun` truth (local running → interrupted; remote orphans re-coordinated).
No frontend/API work is added in G4.

---

## G4 / G5 EVALUATING BOUNDARY

The spec §12 names `running` and `evaluating` Experiments as recovery targets,
but G3-C deliberately has no `evaluating` branch and G5 owns DatasetEvaluation
creation, `running -> evaluating`, evaluating reconciliation, and Retry
Evaluation.

Resolution (accepted approach 1 — G5-extensible recovery that currently acts only
on `running`):

- `_ACTIVE_RECOVERY_STATUSES = ("running",)` is the single selection seam. G4
  actively recovers `running` only.
- G5 extends this tuple to `("running", "evaluating")` and relies on the
  coordinator's evaluating branch. Because the sealed coordinator returns
  `EXPERIMENT_TERMINAL` and schedules nothing for any non-`running` status,
  selecting an `evaluating` Experiment can never re-enter inference scheduling —
  the dormant seam is provably safe.
- G4 creates no `DatasetEvaluation`, never sets `evaluating`, and never moves an
  Experiment out of `evaluating`.
- A G4 test asserts an `evaluating` Experiment is not selected, is unchanged, and
  spawns no coordinator.

Deferred G5 hook (documented precisely): replace
`_ACTIVE_RECOVERY_STATUSES` with `("running", "evaluating")` and, when the
coordinator's evaluating branch exists, no other recovery change is required —
the generation claim/takeover machinery is status-agnostic.

---

## ALL-SUCCESS TEMPORARY SEAM

State on restart before G5:

```text
Experiment.status == running
all Items == completed
```

Deterministic G4 behavior:

1. recovery selects the Experiment, claims a fresh generation, reconciles
   (no Item changes; `queued == 0`, `running == 0`, `failed == 0`), performs no
   local repair, and spawns a coordinator;
2. the coordinator computes `INFERENCE_COMPLETE`, creates no `AnalysisRun`,
   reruns no completed Item, and exits cleanly;
3. the Experiment remains `running` until G5 replaces the seam.

No false `completed_with_failures`, no inference rerun, no fake evaluation.
Dedicated test: `test_all_success_seam_restart_no_new_run_and_still_running`.

---

## TRANSACTION BOUNDARIES

| Durable transition | Commit point | Must NOT span |
|---|---|---|
| Retry Failed claim + Item requeue | one commit (Transaction 1) | subprocess spawn |
| Retry Failed spawn | none (outside txn) | DB transaction |
| Retry Failed PID persist | one commit (Transaction 2) | spawn |
| Retry Failed compensating restore | one commit | spawn |
| Recovery generation claim | one commit | spawn |
| Recovery reconcile | one commit (inside `reconcile_items`) | provider launch |
| Recovery first launch (Transaction B) | one commit before `launch_prepared_run` | provider launch |
| Recovery ambiguous fail-closed | one commit | launch |
| Recovery PID persist | one commit | spawn |

No transaction spans file hashing, Recording probes, SSH, provider physical
launch, subprocess spawn, or sleep. Token rotation/ownership is always committed
before coordinator subprocess side effects. The local first-launch path preserves
`Transaction B commit BEFORE physical launch`.

---

## TASK 1 — Retry Failed (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (apply section A).
- Create: `backend/tests/test_dataset_experiment_retry_failed.py`.

Shared helper added to `backend/tests/dataset_experiment_fixtures.py` (place
after `running_experiment_with_token`; reuses the module's own helpers):

```python
def completed_with_failures_experiment(client, *, failed=1, completed=0):
    from datetime import datetime, timezone

    count = failed + completed
    seed_dataset(client, count=count)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds, max_concurrency=1)
    experiment.status = "running"
    experiment.coordinator_token = "coord_old"
    session.commit()
    targets = list(
        session.query(DatasetExperimentItemModel)
        .filter_by(experiment_id=experiment.id)
        .order_by(DatasetExperimentItemModel.manifest_order)
        .all()
    )
    for index, target in enumerate(targets):
        if index < failed:
            target.status = "failed"
            target.last_error_type = "ANALYSIS_FAILED"
            target.last_error_message = "boom"
        else:
            target.status = "completed"
    experiment = session.get(DatasetExperimentModel, experiment.id)
    experiment.status = "completed_with_failures"
    experiment.completed_at = datetime.now(timezone.utc)
    session.commit()
    return session, ds, experiment, provider
```

Tests:

- `test_retry_requires_completed_with_failures` — loop `pending`,
  `running`, `completed`, `failed` → each raises
  `DATASET_EXPERIMENT_INVALID_TRANSITION`; zero spawn.
- `test_retry_missing_experiment_is_not_found` →
  `DATASET_EXPERIMENT_NOT_FOUND`.
- `test_retry_no_failed_items_rejected` — `completed_with_failures` with only
  completed Items → `DATASET_EXPERIMENT_INVALID_TRANSITION`.
- `test_retry_requeues_only_failed_items_and_leaves_completed`
- `test_retry_clears_item_error_projection`
- `test_retry_clears_experiment_terminal_projection` — status `running`,
  `completed_at is None`, `error_type is None`, `error_message is None`,
  `started_at` preserved.
- `test_retry_preserves_old_attempts_and_runs`
- `test_retry_creates_no_new_attempt_or_run`
- `test_retry_rotates_token_and_persists_pid` — `RecordingJobManager` records the
  fresh token; DB token equals it; `worker_pid == 4242`.
- `test_retry_token_persisted_before_spawn` — job-manager `token_probe` opens a
  fresh Session and asserts `status=running`, `coordinator_token == token`,
  `worker_pid is None`.
- `test_retry_old_generation_fenced` — after retry, `DatasetExperimentCoordinator`
  step with the old token returns `CoordinatorOutcome.FENCE_LOST`; Item stays
  queued.
- `test_retry_concurrent_two_session_one_winner` — Session A wins; Session B
  raises `DATASET_EXPERIMENT_INVALID_TRANSITION`, `B_job_manager.calls == []`.
- `test_retry_stale_session_loser_zero_spawn` — B loaded
  `completed_with_failures` before A committed.
- `test_retry_spawn_failure_restores_terminal_projection` —
  `RecordingJobManager(fail=True)`; raises
  `DATASET_EXPERIMENT_ORCHESTRATION_FAILED`; fresh DB: status
  `completed_with_failures`; `coordinator_token`/`heartbeat_at`/`completed_at`/
  `worker_pid`/`error_type`/`error_message` equal the pre-retry terminal
  snapshot; failed Items are `failed` with their original
  `last_error_type`/`last_error_message`; zero new Attempt/Run.
- `test_retry_compensation_loses_generation_zero_item_mutation` — after
  Transaction 1 commits under `T_retry`, a `RecordingJobManager` `on_start`
  callback opens a fresh Session and takes over the generation via
  `claim_experiment_generation(..., startup_recovery_cutoff=now+5s)`, then
  raises; `retry_failed` raises `DATASET_EXPERIMENT_FENCE_LOST`; fresh DB:
  Experiment owned by the newer token; every retried Item is still `queued`;
  ZERO `queued -> failed` restoration.
- `test_retry_compensation_ownership_cas_required_before_item_restore` —
  `_restore_retry_failed(...)` called directly with a token that does not own
  the Experiment returns `False` and performs zero Item UPDATEs.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_retry_failed.py -v
```
  Expected RED: `AttributeError: 'DatasetExperimentService' object has no
  attribute 'retry_failed'` (collection/first test failure).
- [ ] Implement section A.
- [ ] GREEN: same invocation.
- [ ] Focused regressions (sealed G3-C terminal behavior unchanged):
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_start.py \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_coordinator_fencing.py -q
```
- [ ] Commit: `feat: add dataset experiment retry failed`

---

## TASK 2 — Recovery ownership + local launch recovery (behavior)

**Files:**
- Create: `backend/app/dataset_experiments/recovery.py` (section B, Task 2 stage:
  no `start_recovered_coordinator`, no spawn block, report without
  `coordinators_started`/`spawn_failures`; entrypoint signature without
  `job_manager`).
- Create: `backend/tests/test_dataset_experiment_local_launch_recovery.py`.

Test scaffolding (local to the file):

```python
from datetime import datetime, timedelta, timezone

from dataset_experiment_fixtures import (
    G3LocalPipeline, create_experiment, item, local_services, seed_dataset,
    running_experiment_with_token,
)
from executor_fixtures import FakeProvider, FakeRegistry
from app.dataset_experiments import recovery as rec
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel, DatasetExperimentItemModel, DatasetExperimentModel,
)
from app.analysis.model import AnalysisRunModel
from app.pipelines.registry import PipelineRegistry


def _cutoff():
    return datetime.now(timezone.utc) + timedelta(seconds=5)


def _recover(client, *, provider, cutoff=None):
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    with client.app.state.database.session_factory() as session:
        return rec.recover_dataset_experiments(
            session, registry=registry, model_release_store=None,
            executor_registry=executor_registry,
            startup_recovery_cutoff=cutoff or _cutoff(),
        )


def _seed_pending_local(client, session, experiment, order=0, marker=None, worker_pid=None):
    target = item(session, experiment.id, order=order)
    session.add(AnalysisRunModel(
        id=f"run_p_{order}", recording_id=target.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="pending",
        parameters_json={}, worker_pid=worker_pid,
    ))
    session.add(DatasetExperimentAttemptModel(
        id=f"att_p_{order}", experiment_item_id=target.id, attempt_number=1,
        analysis_run_id=f"run_p_{order}", launch_requested_at=marker,
    ))
    target.status = "running"
    session.commit()
    return target
```

Tests:

- `test_claim_generation_rotates_token_clears_pid` — running Experiment with
  token `T`, old/NULL `heartbeat_at`, and `worker_pid=9999`;
  `claim_experiment_generation(session, id, "T", cutoff)` returns a new token;
  fresh DB: new token, `worker_pid is None`, `heartbeat_at >= cutoff`.
- `test_claim_generation_stale_expected_token_loses` — two Sessions both observe
  `T`; actor A claims with `T`; actor B claims with `T` → `None`, zero spawn.
- `test_claim_generation_ineligible_when_heartbeat_after_cutoff` — Experiment
  `heartbeat_at = now`; `claim_experiment_generation(..., cutoff = now - 1s)`
  returns `None` (same-startup re-entry guarded).
- `test_claim_generation_eligible_when_heartbeat_null_or_before_cutoff` — a NULL
  heartbeat and an old heartbeat both claim successfully under a later cutoff.
- `test_claim_generation_commit_failure_rolls_back` — monkeypatch `session.commit`
  to raise; `claim_experiment_generation` rolls back and re-raises; fresh DB
  keeps the original token (no partial ownership write).
- `test_safe_first_launch_reuses_same_attempt_and_run` — marker `NULL`;
  `recover_first` path; fresh DB: Attempt count and Run count unchanged, same ids,
  `launch_requested_at is not None`, provider launch once.
- `test_safe_first_launch_sets_marker_before_provider_launch` — provider wrapper
  opens a fresh Session inside `launch()` and asserts
  `launch_requested_at is not None` at that instant.
- `test_safe_first_launch_creates_no_new_attempt_or_run`
- `test_safe_first_launch_concurrent_actor_cannot_double_launch` — two Sessions'
  services call `launch_item_attempt` for the same Attempt with the fresh token;
  second raises/contends; `provider.launches` length 1.
- `test_ambiguous_pending_local_run_fails_closed_zero_launch` — marker set;
  after recovery: Run `interrupted`, `error_type == "ANALYSIS_LAUNCH_AMBIGUOUS"`,
  `provider.launches == []`, same Attempt/Run history.
- `test_ambiguous_fail_close_stale_generation_fence_lost` — generation `T1` owns
  the Experiment; another actor claims `T1 -> T2`; then
  `fail_closed_pending_local_run(..., coordinator_token="T1", ...)` raises
  `DATASET_EXPERIMENT_FENCE_LOST`; the Run stays `pending`; zero Item/Run
  mutation.
- `test_ambiguous_fail_close_idempotent_under_current_generation` — first call
  returns `"interrupted"`; a second call under the same current token returns
  `"already_interrupted"`; the Run is `interrupted` exactly once.
- `test_ambiguous_run_projects_item_failed` — after recovery, call
  `ds.reconcile_items(experiment_id)`; Item `failed` with
  `last_error_type == "ANALYSIS_LAUNCH_AMBIGUOUS"`.
- `test_existing_local_running_run_not_relaunched` — seed local Run `running`;
  call `mark_stale_local_cpu_runs_interrupted(session)` first; then recovery;
  assert Run `interrupted`, `provider.launches == []`, Item `failed` after
  reconcile.
- `test_remote_pending_run_delegated_no_local_marker_logic` — Run
  `executor="remote_gpu"`, `status="pending"`, marker `NULL`, no worker;
  recovery performs zero launches and does not fail closed.
- `test_pending_local_run_with_worker_but_null_marker_invariant` — Run
  `pending`, `worker_pid=5`, marker `NULL`; recovery fails the Experiment with
  `DATASET_EXPERIMENT_INVARIANT_VIOLATION`; zero launch.
- `test_missing_run_invariant_fails_experiment` — Attempt points to a missing
  Run; Experiment `failed`; zero launch.
- `test_duplicate_active_attempt_invariant_fails_experiment_zero_launch` — two
  Attempts, both Runs `pending`; Experiment `failed`; both Runs/Attempts
  preserved; zero launch.
- `test_non_running_experiments_untouched` — pending/completed/failed/evaluating
  Experiments unchanged; no claim, no spawn.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_local_launch_recovery.py -v
```
  Expected RED: `ModuleNotFoundError: No module named
  'app.dataset_experiments.recovery'`.
- [ ] Implement section B up to the Task 2 stage.
- [ ] GREEN: same invocation.
- [ ] Focused regressions (reconciliation authority unchanged):
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_reconciliation.py \
  backend/tests/test_dataset_experiment_generation_fence.py \
  backend/tests/test_dataset_experiment_launch.py -q
```
- [ ] Commit: `feat: add dataset experiment local launch recovery`

---

## TASK 3 — Coordinator generation takeover + all-success seam + G5 boundary (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/recovery.py` (add the Task 3 delta:
  `start_recovered_coordinator`, report fields, spawn block, `job_manager`
  parameter).
- Create: `backend/tests/test_dataset_experiment_restart_recovery.py`.

Tests:

- `test_restart_spawns_coordinator_with_fresh_token` — running Experiment token
  `T`; `RecordingJobManager`; recovery returns `coordinators_started == 1`; DB
  token equals the spawned token and differs from `T`; `worker_pid == 4242`.
- `test_restart_token_persisted_before_spawn` — job-manager `token_probe` reads a
  fresh Session and asserts `status=running`, `coordinator_token == token`,
  `worker_pid is None`.
- `test_restart_old_generation_fenced` — recovery runs; `step_once` with old
  token `T` → `CoordinatorOutcome.FENCE_LOST`.
- `test_restart_stale_pid_not_authority` — Experiment `worker_pid=7777`;
  recovery still claims via token CAS and overwrites PID under the new token.
- `test_restart_spawn_failure_leaves_running_and_recoverable` —
  `RecordingJobManager(fail=True)`; `spawn_failures == 1`; fresh DB:
  status `running`, fresh token, `worker_pid is None`; a second recovery with a
  healthy manager then starts a coordinator.
- `test_same_startup_second_recovery_cannot_steal_fresh_generation` — capture
  one cutoff `C`; recovery pass A with `C` claims and starts a coordinator under
  `T1`; recovery pass B with the SAME `C` reads `T1`, attempts to claim, is
  ineligible because `heartbeat_at >= C`, and is `skipped`; zero second
  coordinator spawn.
- `test_next_restart_later_cutoff_recovers_crash_after_claim` — pass A with
  cutoff `C1` claims `T1` but its spawn fails, leaving `running` + fresh
  heartbeat; pass B with a LATER cutoff `C2 = now + 5s` claims `T2` and starts a
  coordinator, proving a genuine next restart can recover crash-after-claim.
- `test_b_committed_c_not_launched_same_epoch_recovery_cannot_interrupt` —
  pass A with cutoff `C` claims `T1`; persist `launch_requested_at != NULL` on
  the item's Attempt to simulate the durable Transaction B commit with the
  physical launch not yet invoked; pass B runs with the SAME `C` → cannot rotate
  `T1`, cannot classify the Run as ambiguity; the Run remains `pending` and pass
  B performs zero launch.
- `test_restart_claim_two_sessions_same_expected_token_one_winner` — two
  Sessions both read token `T`; both call
  `claim_experiment_generation(session, id, "T", cutoff)`; exactly one returns a
  fresh token and the other returns `None` (the direct CAS race).
- `test_all_success_seam_restart_no_new_run_and_still_running` — running
  Experiment, all Items completed with completed Runs; recovery; then
  `step_once` with the spawned token → `INFERENCE_COMPLETE`; fresh DB: status
  `running`, no `DatasetEvaluationModel`, Run/Attempt counts unchanged.
- `test_evaluating_experiment_not_selected_by_g4` — `status="evaluating"`;
  recovery returns all-zero counts, no spawn; status unchanged.
- `test_recover_pid_commit_failure_rolls_back` — monkeypatch `session.commit` to
  raise on the PID-persist transaction; the error propagates after rollback and
  the coordinator token/generation is not silently half-written.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_restart_recovery.py -v
```
  Expected RED: `TypeError: recover_dataset_experiments() got an unexpected
  keyword argument 'job_manager'` and missing `start_recovered_coordinator`.
- [ ] Implement the Task 3 delta.
- [ ] GREEN: same invocation.
- [ ] Focused regressions (coordinator terminal + worker unchanged):
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_coordinator_scheduling.py \
  backend/tests/test_dataset_experiment_worker.py -q
```
- [ ] Commit: `feat: add dataset experiment restart recovery`

---

## TASK 4 — Startup integration + ordering (behavior)

**Files:**
- Modify: `backend/app/main.py` (section C).
- Create: `backend/tests/test_dataset_experiment_startup_order.py`.

Tests:

- `test_startup_recovery_order` — monkeypatch
  `app.remote_execution.recovery.mark_stale_local_cpu_runs_interrupted`,
  `app.main.mark_stale_running_evaluations_interrupted`, and
  `app.dataset_experiments.recovery.recover_dataset_experiments` to append
  markers; `create_app(settings)`; assert
  `["local_stale", "evaluation_stale", "dataset_recovery"]`.
- `test_startup_recovery_order_with_remote` — additionally monkeypatch
  `app.remote_execution.recovery.coordinate_orphaned_remote_runs` and
  `app.main._wire_remote_lifecycle` to set `remote_config_available=True` and a
  fake `remote_coordinator_launcher`; assert
  `["local_stale", "evaluation_stale", "remote_recovery", "dataset_recovery"]`.
- `test_dataset_recovery_receives_built_executor_registry` — monkeypatch
  `recover_dataset_experiments` to capture kwargs; assert `job_manager` is a
  `DatasetExperimentJobManager`, `executor_registry` is not `None`, and
  `startup_recovery_cutoff` is a `datetime`.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_startup_order.py -v
```
  Expected RED: `dataset_recovery` absent from the recorded order (and
  `TypeError` if the call site does not exist).
- [ ] Implement section C.
- [ ] GREEN: same invocation.
- [ ] Focused regressions (existing startup recovery untouched):
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_remote_stale_helper_regression.py \
  backend/tests/test_remote_coordinator_fencing.py -q
```
- [ ] Commit: `feat: integrate dataset experiment recovery into startup`

---

## TASK 5 — G4 regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_dataset_experiment_g4_regression.py`.

Guards:

- `DatasetExperiment`, `DatasetExperimentItem`, `DatasetExperimentAttempt`,
  `AnalysisRun` schemas unchanged (exact column sets, as in
  `test_dataset_experiment_coordinator_regression.py`).
- `recovery.py` imports do not include `app.remote_execution` and contain no
  `prepare_run(`, no `provider.launch(`, no `DatasetEvaluation`, no SSH.
- `coordinator.py` still contains no `prepare_run(`, `provider.launch(`,
  `DatasetEvaluation`, `evaluating`, or token rotation.
- `recovery.py` is torch/ultralytics-free (fresh subprocess import).
- Static guard on `recovery.py` source: `"startup_recovery_cutoff"` present;
  the ambiguous fail-close uses database-side `exists()` predicates (no
  SELECT-then-UPDATE ownership check).
- Guard that `_ACTIVE_RECOVERY_STATUSES == ("running",)` (G5 not enabled).

Regression commands:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_retry_failed.py \
  backend/tests/test_dataset_experiment_local_launch_recovery.py \
  backend/tests/test_dataset_experiment_restart_recovery.py \
  backend/tests/test_dataset_experiment_startup_order.py \
  backend/tests/test_dataset_experiment_g4_regression.py -v

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_generation_fence.py \
  backend/tests/test_dataset_experiment_start.py \
  backend/tests/test_dataset_experiment_coordinator_fencing.py \
  backend/tests/test_dataset_experiment_coordinator_reconciliation.py \
  backend/tests/test_dataset_experiment_coordinator_scheduling.py \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_worker.py \
  backend/tests/test_dataset_experiment_coordinator_regression.py \
  backend/tests/test_dataset_experiment_models.py \
  backend/tests/test_dataset_experiment_read_model.py \
  backend/tests/test_dataset_experiment_creation.py \
  backend/tests/test_dataset_experiment_revalidation.py \
  backend/tests/test_dataset_experiment_regression.py \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_attempt_remote.py \
  backend/tests/test_dataset_experiment_attempt_concurrency.py \
  backend/tests/test_dataset_experiment_g3_regression.py \
  backend/tests/test_dataset_experiment_launch.py \
  backend/tests/test_dataset_experiment_launch_concurrency.py \
  backend/tests/test_dataset_experiment_launch_remote.py \
  backend/tests/test_dataset_experiment_launch_regression.py \
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py \
  backend/tests/test_analysis_launch_prepared_run.py \
  backend/tests/test_analysis_create_run_compat.py \
  backend/tests/test_analysis_seam_regression.py \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_remote_stale_helper_regression.py \
  backend/tests/test_remote_coordinator_fencing.py -q

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Current OpenCode-reported baseline after G3-C: `1541 passed, 28 skipped`. The
G4 implementer must report the fresh final summary and require 0 failed / 0
errors. Commit: `test: add phase G G4 regression matrix`.

---

## Error Semantics

| Code | Where | Meaning |
|---|---|---|
| `DATASET_EXPERIMENT_NOT_FOUND` | retry preflight, recovery read | missing Experiment (404). |
| `DATASET_EXPERIMENT_INVALID_TRANSITION` | retry preflight/CAS; no failed Items | Retry Failed outside `completed_with_failures`. |
| `DATASET_EXPERIMENT_ORCHESTRATION_FAILED` | retry spawn failure | coordinator process could not be spawned; state compensating-restored. |
| `DATASET_EXPERIMENT_FENCE_LOST` | recovery reconcile/repair; stale ambiguous fail-close; superseded retry compensation | a newer generation owns the Experiment; loser stops with zero mutation. |
| `DATASET_EXPERIMENT_INVARIANT_VIOLATION` | recovery reconcile/repair | ownership/membership corruption; Experiment fails closed. |
| `ANALYSIS_LAUNCH_AMBIGUOUS` | ambiguous local pending Run | AnalysisRun `interrupted`, projected to `Item.failed`. |

No scientific error code is added or changed. Completed AnalysisRuns are never
mutated. DetectionResults are never touched.

---

## Self-Review

1. No automatic retry policy was introduced: failed Items requeue only via
   explicit `retry_failed`; restart never requeues. PASS
2. Retry Failed never revives an old AnalysisRun: it reads/deletes no Run and
   creates none. PASS
3. Retry Failed creates no Run/Attempt itself: Transaction 1 writes only
   Experiment and Item rows. PASS
4. Completed Items never requeue: requeue `WHERE status='failed'`. PASS
5. Failed Items do not auto-requeue at restart: recovery never changes a
   `failed` Item to `queued`. PASS
6. local marker NULL uses same Run for safe FIRST launch: G3-B
   `launch_item_attempt` on the existing Attempt. PASS
7. local marker non-NULL pending Run is NEVER physically relaunched:
   `fail_closed_pending_local_run` performs zero launch. PASS
8. remote recovery remains delegated: no `remote_execution/*` diff; remote runs
   skipped by `repair_local_pending_runs`. PASS
9. old coordinator token fenced before recovered coordinator effects: claim
   commit precedes spawn; all G3-C writes are token-guarded. PASS
10. no duplicate active Attempt created: reconcile enforces `active == [latest]`;
    recovery fails closed. PASS
11. no inference rerun for all-success temporary seam: coordinator reaches
    `INFERENCE_COMPLETE` with zero new Run; Experiment stays `running`. PASS
12. startup recovery order correct: local → evaluation → remote → dataset. PASS
13. G5 evaluation behavior not implemented: `_ACTIVE_RECOVERY_STATUSES ==
    ("running",)`; no DatasetEvaluation/evaluating code. PASS
14. no schema/migration change: exact-column guards in Task 5. PASS
15. no `remote_execution` production change: G4 never imports or edits it. PASS
16. retry compensation is generation-claimed: the Experiment ownership CAS is
    required (rowcount == 1) before any Item UPDATE; a superseded compensation
    rolls back with ZERO Item writes and raises `DATASET_EXPERIMENT_FENCE_LOST`.
    PASS
17. retry compensation restores the pre-retry terminal projection
    (`completed_at`/`heartbeat_at`/`coordinator_token`/`worker_pid`/
    `error_type`/`error_message`), not a normalized `now`/`None` rewrite. PASS
18. ambiguous local fail-close is generation-bound in the FINAL Run UPDATE via
    `EXISTS` predicates over Experiment/Item/Attempt/Run; a stale generation gets
    `FENCE_LOST` with zero mutation. PASS
19. same-startup second recovery cannot steal a freshly claimed generation
    (cutoff predicate `heartbeat_at IS NULL OR heartbeat_at < cutoff`). PASS
20. a B-committed/C-not-yet-launched Run cannot be interrupted by a same-epoch
    recovery pass; the original first physical launch remains authoritative. PASS
21. a genuine next restart with a later cutoff can recover crash-after-claim.
    PASS
22. recovery writes use explicit `try / commit / except rollback / raise` and
    never reuse a failed Session; spawn/provider launch stay outside
    transactions. PASS

---

## Scope Audit

Must NOT appear in G4 diffs:

- `backend/app/**/model.py`, `backend/app/db/**`, migrations.
- `backend/app/remote_execution/**`.
- `backend/app/benchmarks/service.py`, evaluation science.
- `backend/app/dataset_experiments/coordinator.py` behavior change (G4 relies on
  the sealed engine; only `recovery.py` and `service.py` are modified).
- REST routers, frontend, metrics, training, AssetManifest/request hashing.

Required files only: `service.py` (retry additions), new `recovery.py`,
`main.py` (step 4), and the five G4 test files plus the fixtures helper.

If any of the following is reached, STOP and report rather than improvise:

- a schema/migration change appears required;
- existing remote recovery authority proves insufficient for a remote_gpu case;
- a safe Retry spawn-failure outcome cannot be expressed by legal transitions.

---

## Verification

- `git diff --check` clean.
- All five task RED/GREEN commands recorded with their observable RED failure.
- Complete G4 suite green.
- Complete G3-C + G1/G2/G3-A/G3-B + remote recovery regression suites green.
- Full backend suite green with a fresh summary; baseline starts at
  `1541 passed, 28 skipped`.
- Scope scan confirms only the intended files changed and no forbidden paths.
