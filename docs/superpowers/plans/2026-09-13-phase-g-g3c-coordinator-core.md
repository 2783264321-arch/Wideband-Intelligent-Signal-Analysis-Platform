# Phase G G3-C Coordinator Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G3-C sub-gate):** Complete the approved Phase G **G3 — Orchestrator
Core**: durable, generation-fenced coordinator ownership
(`pending -> running` + `coordinator_token`), a fenced single-iteration
coordinator, Item reconciliation from `AnalysisRun` authority, bounded
`max_concurrency` scheduling in deterministic `manifest_order`, best-effort
continuation, and the terminal inference transitions. G3-C is normal
orchestration only; it is not G4 recovery/retry and not G5 evaluation/API.

**Architecture:** A control-plane-only coordinator package
(`backend/app/dataset_experiments/{coordinator,job_manager,worker,wiring}.py`)
plus additive methods on the sealed `DatasetExperimentService`. The supplied
coordinator generation participates in the **final durable claims** (Transaction
A Item claim, Transaction B launch-intent claim, reconciliation projections,
Item-failure projection) through additive optional `coordinator_token`
parameters. Existing G3-A/G3-B callers that omit the token stay byte-identical.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, no PRAGMAs, `expire_on_commit=False`),
subprocess job managers, pytest 9. No GPU, no SSH, no torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`.

**Base:** `feature/m9-2-implementation @ 290471cb9bcfd7f6096ef39ad735ab07b7d12fb1`.

---

## Global Constraints

1. New Item execution composes G3-A `start_item_attempt` then G3-B
   `launch_item_attempt`; no `prepare_run`/`provider.launch` in the coordinator.
2. Generation fencing is part of the atomic claims when a token is supplied.
3. `coordinator_token=None` preserves sealed G3-A/G3-B behavior exactly.
4. Reconciliation is all-or-nothing and validates every Attempt.
5. One normal coordinator per pending Experiment via a durable CAS.
6. No `DatasetEvaluation`, no `evaluating`, no `completed`.
7. No schema/migration change; no `remote_execution/*` change.
8. Control-plane only (no torch/ultralytics/model code).
9. Small transactions; nothing spans I/O, probes, launch, spawn, or sleep.

---

## Exact Production Code

### `backend/app/dataset_experiments/service.py` — module level

Add to the existing imports:

```python
from dataclasses import dataclass

from sqlalchemy import exists, func, select, update
```

Add the result type:

```python
@dataclass(frozen=True)
class ReconcileSummary:
    expected: int
    queued: int
    running: int
    completed: int
    failed: int
```

### Generation-fenced claims (add / modify)

```python
    def _require_experiment_generation(self, experiment_id, coordinator_token):
        row = self.session.execute(
            select(DatasetExperimentModel.status, DatasetExperimentModel.coordinator_token)
            .where(DatasetExperimentModel.id == experiment_id)
        ).one_or_none()
        if row is None or row[0] != "running" or row[1] != coordinator_token:
            raise PlatformError(
                "DATASET_EXPERIMENT_FENCE_LOST",
                "Coordinator generation no longer owns the experiment.",
                409,
            )

    def _claim_queued_item(self, *, item_id, experiment_id, coordinator_token=None):
        statement = (
            update(DatasetExperimentItemModel)
            .where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "queued",
            )
            .values(status="running")
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        result = self.session.execute(statement)
        return int(result.rowcount or 0)

    def _claim_launch_intent(self, *, attempt_id, item_id, requested_at,
                             experiment_id=None, coordinator_token=None):
        statement = (
            update(DatasetExperimentAttemptModel)
            .where(
                DatasetExperimentAttemptModel.id == attempt_id,
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                DatasetExperimentAttemptModel.launch_requested_at.is_(None),
            )
            .values(launch_requested_at=requested_at)
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        result = self.session.execute(statement)
        return int(result.rowcount or 0)
```

In `start_item_attempt` add `coordinator_token: str | None = None` and change the
claim block to:

```python
            with self.session.no_autoflush:
                claimed = self._claim_queued_item(
                    item_id=item_id, experiment_id=experiment.id,
                    coordinator_token=coordinator_token,
                )
                if claimed != 1:
                    if coordinator_token is not None:
                        self._require_experiment_generation(experiment.id, coordinator_token)
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Item is no longer queued; another start won or it is not eligible.",
                        409,
                    )
                attempt_number = self._next_attempt_number(item_id)
                attempt = DatasetExperimentAttemptModel(
                    id=f"expattempt_{uuid4().hex}",
                    experiment_item_id=item_id,
                    attempt_number=attempt_number,
                    analysis_run_id=run.id,
                )
                self.session.add(attempt)
                self.session.expire(item)
```

In `launch_item_attempt` add `coordinator_token: str | None = None` and change
the claim block to:

```python
            with self.session.no_autoflush:
                claimed = self._claim_launch_intent(
                    attempt_id=attempt_id, item_id=item_id, requested_at=now,
                    experiment_id=experiment.id, coordinator_token=coordinator_token,
                )
                if claimed != 1:
                    if coordinator_token is not None:
                        self._require_experiment_generation(experiment.id, coordinator_token)
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Launch intent is already recorded or the attempt is not eligible.",
                        409,
                    )
            self.session.commit()  # Transaction B (all executors)
```

### Reconciliation + selection (add)

```python
    def _load_attempts_by_item(self, item_ids):
        attempts = list(
            self.session.scalars(
                select(DatasetExperimentAttemptModel)
                .where(DatasetExperimentAttemptModel.experiment_item_id.in_(item_ids))
                .order_by(
                    DatasetExperimentAttemptModel.experiment_item_id,
                    DatasetExperimentAttemptModel.attempt_number,
                )
            ).all()
        )
        by_item: dict[str, list] = {}
        for attempt in attempts:
            by_item.setdefault(attempt.experiment_item_id, []).append(attempt)
        return by_item

    def _load_runs_by_id(self, run_ids):
        if not run_ids:
            return {}
        return {
            run.id: run
            for run in self.session.scalars(
                select(AnalysisRunModel).where(AnalysisRunModel.id.in_(run_ids))
            ).all()
        }

    def reconcile_items(self, experiment_id, coordinator_token=None):
        try:
            if coordinator_token is not None:
                fence = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "running",
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                    )
                    .values(status="running")
                    .execution_options(synchronize_session=False)
                )
                if int(fence.rowcount or 0) != 1:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_FENCE_LOST",
                        "Coordinator generation no longer owns the experiment.",
                        409,
                    )
            experiment = self.session.get(DatasetExperimentModel, experiment_id)
            if experiment is None:
                raise PlatformError(
                    "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
                )
            items = list(
                self.session.scalars(
                    select(DatasetExperimentItemModel)
                    .where(DatasetExperimentItemModel.experiment_id == experiment_id)
                    .order_by(DatasetExperimentItemModel.manifest_order)
                ).all()
            )
            if not items:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION", "Experiment has no items.", 409
                )
            attempts_by_item = self._load_attempts_by_item([item.id for item in items])
            run_ids = {
                attempt.analysis_run_id
                for attempts in attempts_by_item.values()
                for attempt in attempts
            }
            runs_by_id = self._load_runs_by_id(run_ids)
            now = datetime.now(timezone.utc)
            for item in items:
                attempts = attempts_by_item.get(item.id, [])
                for attempt in attempts:
                    run = runs_by_id.get(attempt.analysis_run_id)
                    if run is None:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt references a missing analysis run.", 409,
                        )
                    if run.recording_id != item.recording_id:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt run does not match the item recording.", 409,
                        )
                    if (
                        run.pipeline_id != experiment.plugin_id
                        or run.pipeline_version != experiment.plugin_version
                        or run.executor != experiment.executor
                    ):
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt run identity does not match the frozen experiment.", 409,
                        )
                latest = attempts[-1] if attempts else None
                latest_run = runs_by_id.get(latest.analysis_run_id) if latest else None
                active = [
                    attempt for attempt in attempts
                    if runs_by_id[attempt.analysis_run_id].status in {"pending", "running"}
                ]
                if item.status == "queued":
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Queued item has an active attempt.", 409,
                        )
                    if latest is not None and latest_run.status not in {"failed", "interrupted"}:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Queued item has a non-retryable historical attempt.", 409,
                        )
                    continue
                if item.status == "running":
                    if latest is None or latest_run is None:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Running item has no authoritative attempt/run.", 409,
                        )
                    if latest_run.status in {"pending", "running"}:
                        if active != [latest]:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "The active attempt is not the latest attempt.", 409,
                            )
                        continue
                    if latest_run.status == "completed":
                        if active:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Terminal latest attempt has an older active attempt.", 409,
                            )
                        item.status = "completed"
                        item.updated_at = now
                    elif latest_run.status in {"failed", "interrupted"}:
                        if active:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Terminal latest attempt has an older active attempt.", 409,
                            )
                        item.status = "failed"
                        item.last_error_type = latest_run.error_type
                        item.last_error_message = latest_run.error_message
                        item.updated_at = now
                    else:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Running item references an unknown run status.", 409,
                        )
                elif item.status == "completed":
                    if latest is None or latest_run is None or latest_run.status != "completed":
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Completed item disagrees with its authoritative run.", 409,
                        )
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Completed item retains an active attempt.", 409,
                        )
                elif item.status == "failed":
                    if latest is not None and (
                        latest_run is None or latest_run.status not in {"failed", "interrupted"}
                    ):
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Failed item disagrees with its authoritative run.", 409,
                        )
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Failed item retains an active attempt.", 409,
                        )
                else:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Item has an unknown status.", 409,
                    )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return ReconcileSummary(
            expected=len(items), queued=counts["queued"], running=counts["running"],
            completed=counts["completed"], failed=counts["failed"],
        )

    def select_queued_items(self, experiment_id, limit):
        if limit <= 0:
            return []
        return list(
            self.session.scalars(
                select(DatasetExperimentItemModel.id)
                .where(
                    DatasetExperimentItemModel.experiment_id == experiment_id,
                    DatasetExperimentItemModel.status == "queued",
                )
                .order_by(DatasetExperimentItemModel.manifest_order.asc())
                .limit(limit)
            ).all()
        )
```

### Start ownership + coordinator-owned writes (add)

```python
    def start_experiment(self, experiment_id, job_manager):
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "pending",
                    )
                    .values(
                        status="running", coordinator_token=token, started_at=now,
                        heartbeat_at=now, worker_pid=None,
                        error_type=None, error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                claimed_count = int(claimed.rowcount or 0)
            if claimed_count != 1:
                self.session.rollback()
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVALID_TRANSITION",
                    "Only a pending experiment can be started.",
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
            with self.session.no_autoflush:
                self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                    )
                    .values(
                        status="failed", worker_pid=None,
                        error_type="DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                        error_message=str(exc)[:1000],
                        completed_at=datetime.now(timezone.utc),
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unable to start DatasetExperiment coordinator.",
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

    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def mark_experiment_completed_with_failures(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="completed_with_failures",
                        completed_at=datetime.now(timezone.utc),
                        error_type=None, error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def _fail_experiment(self, experiment_id, coordinator_token, error_type, error_message):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="failed", error_type=error_type,
                        error_message=(error_message or "")[:1000],
                        completed_at=datetime.now(timezone.utc),
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def _mark_item_failed(self, item_id, error_type, error_message, *,
                          experiment_id, coordinator_token=None):
        statement = (
            update(DatasetExperimentItemModel)
            .where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status.in_(("queued", "running")),
            )
            .values(
                status="failed", last_error_type=error_type,
                last_error_message=(error_message or "")[:1000],
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        try:
            result = self.session.execute(statement)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        if int(result.rowcount or 0) == 1:
            return True
        if coordinator_token is not None:
            self._require_experiment_generation(experiment_id, coordinator_token)
        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404
            )
        if item.experiment_id != experiment_id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Item does not belong to the expected experiment.",
                409,
            )
        if item.status in {"completed", "failed"}:
            return False
        raise PlatformError(
            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
            "Item cannot be marked failed from its current state.",
            409,
        )
```

### `backend/app/dataset_experiments/coordinator.py` (new, exact)

```python
from __future__ import annotations

import time
from enum import Enum

from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentModel

EXPERIMENT_LEVEL_CODES = frozenset({
    "ANALYSIS_RUN_NOT_LAUNCHABLE",
    "EXECUTION_CAPABILITY_UNAVAILABLE",
    "EXECUTION_NOT_CERTIFIED",
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
})


class CoordinatorOutcome(str, Enum):
    SCHEDULED = "scheduled"
    WAITING = "waiting"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    INFERENCE_COMPLETE = "inference_complete"
    EXPERIMENT_TERMINAL = "experiment_terminal"
    FENCE_LOST = "fence_lost"
    INVARIANT_FAILED = "invariant_failed"


EXIT_OUTCOMES = frozenset({
    CoordinatorOutcome.COMPLETED_WITH_FAILURES,
    CoordinatorOutcome.INFERENCE_COMPLETE,
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
                 sleep_fn=time.sleep, poll_interval=1.0, max_iterations=None):
        self._session_factory = session_factory
        self._services_factory = services_factory
        self._sleep_fn = sleep_fn
        self._poll_interval = poll_interval
        self._max_iterations = max_iterations

    def step(self, experiment_id, coordinator_token):
        with self._session_factory() as session:
            ds, analysis = self._services_factory(session)
            return self._step(session, ds, analysis, experiment_id, coordinator_token)

    def _step(self, session, ds, analysis, experiment_id, coordinator_token):
        experiment = session.get(DatasetExperimentModel, experiment_id)
        if experiment is None or experiment.coordinator_token != coordinator_token:
            return CoordinatorOutcome.FENCE_LOST
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
                    return CoordinatorOutcome.INFERENCE_COMPLETE
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
```

### `backend/app/dataset_experiments/job_manager.py` (new, exact)

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.core.config import Settings


class DatasetExperimentJobManager:
    DEFAULT_COORDINATOR_MODULE = "app.dataset_experiments.worker"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend_root = Path(__file__).resolve().parents[2]

    def start(self, experiment_id: str, coordinator_token: str) -> int:
        env = os.environ.copy()
        env.update(
            {
                "WSP_PROJECT_ROOT": str(self.settings.project_root),
                "WSP_DATA_ROOT": str(self.settings.data_root),
                "WSP_LABEL_SPACE_ROOT": str(self.settings.label_space_root),
                "WSP_DATABASE_URL": str(self.settings.database_url),
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-m", self.DEFAULT_COORDINATOR_MODULE,
             experiment_id, "--coordinator-token", coordinator_token],
            cwd=self.backend_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        return process.pid
```

### `backend/app/dataset_experiments/wiring.py` (new, exact)

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


@dataclass(frozen=True)
class ControlPlaneDependencies:
    registry: object
    model_release_store: object
    executor_registry: object
    identity_resolver: object
    orchestrator_commit_resolver: object
    runtime_commit_config: str | None
    project_root: Path
    data_root: Path


def _plugins_root() -> Path:
    return Path(__file__).resolve().parents[1] / "pipelines"


def build_control_plane_dependencies(settings: Settings) -> ControlPlaneDependencies:
    from app.analysis.local_executor import build_local_providers
    from app.pipelines.registry import create_pipeline_registry
    from app.remote_execution.identity import (
        resolve_local_orchestrator_commit,
        resolve_remote_recording_identity,
    )
    from app.remote_execution.model_release import (
        ModelReleaseStore,
        load_model_release_defaults,
    )
    from app.remote_execution.runtime import (
        ExecutionCertificateStore,
        ExecutorRegistry,
        load_execution_certificates,
    )

    plugins_root = _plugins_root()
    registry = create_pipeline_registry()
    model_release_store = ModelReleaseStore(
        plugins_root,
        load_model_release_defaults(plugins_root / "model_release_defaults.json"),
    )

    remote_provider = None
    runtime_commit_config = None
    identity_resolver = None
    try:
        from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
        from app.remote_execution.executor import SshRemoteExecutorProbe
        from app.remote_execution.profile import RemoteProfile
        from app.remote_execution.runtime import RemoteGpuExecutorProvider
        from app.remote_execution.transport import SshRunner

        profile = RemoteProfile.from_env(settings)
        transport = SshRunner(profile)
        probe = SshRemoteExecutorProbe(
            profile, transport,
            expected_runtime_commit=profile.required_remote_runtime_commit,
        )
        remote_provider = RemoteGpuExecutorProvider(
            profile=profile, probe=probe,
            launcher=CoordinatorJobManager(settings),
            required_runtime_commit=profile.required_remote_runtime_commit,
        )
        runtime_commit_config = profile.required_remote_runtime_commit
        identity_resolver = resolve_remote_recording_identity
    except Exception:
        remote_provider = None
        runtime_commit_config = None
        identity_resolver = None

    providers = dict(build_local_providers(settings))
    if remote_provider is not None:
        providers[remote_provider.name] = remote_provider
    certificates = ExecutionCertificateStore(
        load_execution_certificates(plugins_root / "execution_certificates.json")
    )
    executor_registry = ExecutorRegistry(providers, certificates)
    return ControlPlaneDependencies(
        registry=registry,
        model_release_store=model_release_store,
        executor_registry=executor_registry,
        identity_resolver=identity_resolver,
        orchestrator_commit_resolver=resolve_local_orchestrator_commit,
        runtime_commit_config=runtime_commit_config,
        project_root=settings.project_root,
        data_root=settings.data_root,
    )
```

### `backend/app/dataset_experiments/worker.py` (new, exact)

```python
import argparse
import time

DEFAULT_POLL_INTERVAL = 1.0


def run_coordinator(experiment_id, coordinator_token, *, settings=None,
                    poll_interval=DEFAULT_POLL_INTERVAL, max_iterations=None):
    from app.analysis.service import AnalysisService
    from app.core.config import Settings
    from app.dataset_experiments.coordinator import (
        DatasetExperimentCoordinator,
        EXIT_OUTCOMES,
    )
    from app.dataset_experiments.service import DatasetExperimentService
    from app.dataset_experiments.wiring import build_control_plane_dependencies
    from app.db.base import Base, load_domain_models
    from app.db.migrations import run_additive_migrations
    from app.db.session import Database

    settings = settings or Settings()
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    deps = build_control_plane_dependencies(settings)

    def services_factory(session):
        ds = DatasetExperimentService(
            session, deps.registry, deps.model_release_store, deps.executor_registry
        )
        analysis = AnalysisService(
            session, deps.registry, None,
            model_release_store=deps.model_release_store,
            executor_registry=deps.executor_registry,
            identity_resolver=deps.identity_resolver,
            orchestrator_commit_resolver=deps.orchestrator_commit_resolver,
            runtime_commit_config=deps.runtime_commit_config,
            project_root=deps.project_root,
            data_root=deps.data_root,
        )
        return ds, analysis

    coordinator = DatasetExperimentCoordinator(
        session_factory=database.session_factory,
        services_factory=services_factory,
        poll_interval=poll_interval,
        max_iterations=max_iterations,
    )
    iterations = 0
    while True:
        outcome = coordinator.step(experiment_id, coordinator_token)
        if outcome in EXIT_OUTCOMES:
            return 0
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return 0
        time.sleep(poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id")
    parser.add_argument("--coordinator-token", required=True)
    args = parser.parse_args(argv)
    return run_coordinator(args.experiment_id, args.coordinator_token)


if __name__ == "__main__":
    raise SystemExit(main())
```

---

# TASK 1 — Generation-fenced primitives (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (apply the exact
  generation-fenced code above).
- Create: `backend/tests/test_dataset_experiment_generation_fence.py`

Test scaffolding (re-declares G3-A helpers and establishes persisted generations
explicitly):

```python
def _running_experiment_with_token(client, token):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)  # sets status running; commits
    experiment.coordinator_token = token
    session.commit()
    return session, ds, analysis, provider, experiment


def _rotate_token(client, experiment_id, new_token):
    with client.app.state.database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        experiment.coordinator_token = new_token
        session.commit()


def _owned_attempt_with_token(client, token):
    session, ds, analysis, provider, experiment = _running_experiment_with_token(client, token)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        analysis_service=analysis, coordinator_token=token,
    )
    return session, ds, analysis, provider, experiment, item, attempt
```

Tests:

- `test_matching_token_persisted_and_claim_succeeds` — uses
  `_owned_attempt_with_token`; asserts a committed Attempt exists and
  `experiment.coordinator_token == token`.
- `test_start_item_attempt_none_token_matches_sealed_behavior` — no token
  supplied: `_experiment(ds)` (no token) then `start_item_attempt(...)` succeeds.
- `test_start_item_attempt_fence_lost_before_transaction_a` — persisted token
  `T2`; call with stale `T` → `DATASET_EXPERIMENT_FENCE_LOST`; fresh DB: no Run,
  no Attempt, Item `queued`, zero launches.
- `test_launch_item_attempt_fence_lost_before_transaction_b` — claim under `T`,
  `_rotate_token(T2)`, call `launch_item_attempt(..., coordinator_token=T)` →
  `DATASET_EXPERIMENT_FENCE_LOST`; fresh DB: Run `pending`, `worker_pid NULL`,
  `launch_requested_at NULL`, zero launches; Run+Attempt+Item running remain.
- `test_mark_item_failed_fence_lost` — rotate then
  `_mark_item_failed(..., coordinator_token=T)` → `FENCE_LOST`; Item unchanged.
- `test_reconcile_fence_lost_no_projection` — Item would project `completed`;
  rotate first; `reconcile_items(token=T)` → `FENCE_LOST`; Item stays `running`.

### Steps

- [ ] Write the test file; RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_generation_fence.py -v
```
Expected RED: `TypeError`/`AttributeError` for the new `coordinator_token`
parameter and fence behavior.
- [ ] Implement the exact generation-fenced code from "Exact Production Code".
- [ ] GREEN: same invocation.
- [ ] Focused regressions (sealed callers unchanged):
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_attempt_concurrency.py \
  backend/tests/test_dataset_experiment_launch.py \
  backend/tests/test_dataset_experiment_launch_concurrency.py -q
```
- [ ] Commit: `feat: add coordinator generation fencing to experiment primitives`

---

# TASK 2 — Item reconciliation + queued selection (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (apply the exact
  reconciliation/selection code).
- Create: `backend/tests/test_dataset_experiment_coordinator_reconciliation.py`

Seeding helper:

```python
def _add_attempt_run(session, *, item, attempt_id, attempt_number, run_id, status,
                     error_type=None, error_message=None):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status=status,
        parameters_json={}, error_type=error_type, error_message=error_message,
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item.id,
        attempt_number=attempt_number, analysis_run_id=run_id,
    ))
    session.commit()
```

Tests (real Sessions):

- `test_queued_no_attempts_valid`
- `test_queued_latest_failed_valid`
- `test_queued_latest_interrupted_valid`
- `test_queued_latest_completed_invariant`
- `test_queued_active_pending_or_running_invariant`
- `test_active_attempt_must_be_latest`
- `test_terminal_item_with_hidden_older_active_attempt_invariant`
- `test_historical_attempt_missing_run_invariant`
- `test_historical_attempt_identity_mismatch_invariant`
- `test_running_pending_run_stays_running`
- `test_running_completed_run_becomes_completed`
- `test_running_failed_run_becomes_failed_and_projects_error`
- `test_running_interrupted_run_becomes_failed`
- `test_completed_requires_completed_latest_run`
- `test_failed_without_attempt_allowed`
- `test_reconciliation_all_or_nothing` — Item1 would become `completed`; Item2
  corrupt → invariant; fresh DB: Item1 still `running`; Experiment unchanged.
- `test_select_queued_items_manifest_order_and_limit`

### Steps

- [ ] Write tests; RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_coordinator_reconciliation.py -v
```
- [ ] Implement the exact reconciliation/selection code.
- [ ] GREEN; focused regressions
  (`test_dataset_experiment_generation_fence.py`,
  `test_dataset_experiment_attempt.py`).
- [ ] Commit: `feat: add dataset experiment item reconciliation`

---

# TASK 3 — Coordinator core: start, fence, heartbeat, terminal, scheduling (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (apply the exact
  `start_experiment`, heartbeat, and terminal/failure helpers).
- Create: `backend/app/dataset_experiments/job_manager.py`
- Create: `backend/app/dataset_experiments/coordinator.py`
- Create: `backend/tests/test_dataset_experiment_start.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_fencing.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_terminal.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_scheduling.py`

Start tests (`test_dataset_experiment_start.py`):

- `test_start_experiment_pending_to_running_token_before_spawn` (spawn probe
  reads a fresh Session and asserts `status=running`, `coordinator_token=token`,
  `worker_pid=NULL` before spawn)
- `test_start_experiment_real_two_session_stale_pending_cas` (Session A loads
  `pending`; Session B wins the real CAS and commits; Session A calls the real
  `start_experiment` → `DATASET_EXPERIMENT_INVALID_TRANSITION`, zero spawns,
  winner token/status intact)
- `test_start_experiment_spawn_failure_marks_failed`
- `test_start_experiment_commit_failure_zero_spawn` (monkeypatched
  `session.commit` raises on the CAS → rollback, zero spawn)
- `test_start_experiment_does_not_overwrite_terminal_worker_pid` (job manager
  marks the Experiment `completed_with_failures` during `start`; parent's
  `worker_pid` update must not change the terminal status)
- `test_start_experiment_rejects_missing_and_non_pending`

Fencing tests (`test_dataset_experiment_coordinator_fencing.py`):

- `test_matching_token_updates_heartbeat`
- `test_stale_token_returns_fence_lost_without_writes`
- `test_non_running_experiment_returns_terminal`
- `test_rotation_before_transaction_a_creates_no_run_or_attempt`
- `test_rotation_before_transaction_b_blocks_launch_intent`
- `test_rotation_before_reconciliation_commit_blocks_projection`
- `test_rotation_before_mark_item_failed_blocks_mutation`

Terminal tests (`test_dataset_experiment_coordinator_terminal.py`):

- `test_completed_with_failures_sets_status_and_exits`
- `test_all_success_returns_inference_complete_and_leaves_running` (Experiment
  still `running`; no `DatasetEvaluationModel` row; status neither `evaluating`
  nor `completed`)
- `test_empty_experiment_fails_closed`

Scheduling + classification tests
(`test_dataset_experiment_coordinator_scheduling.py`):

- `test_max_concurrency_one_starts_one`
- `test_max_concurrency_two_starts_two`
- `test_existing_running_items_consume_slots`
- `test_never_oversubscribes`
- `test_queued_items_started_in_manifest_order`
- `test_item_level_failure_marks_item_failed_and_continues` — injects
  `INPUT_INCOMPATIBLE` for the first queued Item (per-item `AnalysisService`)
  and asserts it becomes `failed` while the next queued Item starts.
- `test_experiment_level_execution_capability_failure_stops` — injects
  `EXECUTION_CAPABILITY_UNAVAILABLE` → Experiment `failed`, no further Items
  started.
- `test_experiment_level_release_failure_stops` — injects
  `MODEL_RELEASE_MISMATCH` or `EXECUTION_NOT_CERTIFIED` → Experiment `failed`.
- `test_remote_runtime_drift_stops_experiment` — injects
  `REMOTE_IMPLEMENTATION_MISMATCH` on the pre-run prepare/probe path for the first
  queued Item → Experiment `failed`; no later queued Item starts; the failing
  Item is not merely projected Item-level; pre-existing Runs/results preserved.
- `test_remote_asset_drift_stops_experiment` — injects `PIPELINE_ASSET_MISMATCH`
  on the pre-run scheduling path → Experiment `failed`; no later queued Item
  starts.
- `test_impossible_launch_state_stops` — injects `ANALYSIS_RUN_NOT_LAUNCHABLE`
  → Experiment `failed`.
- `test_unknown_platform_error_fails_closed` — injected code not in either set
  → Experiment `failed`.
- `test_scheduling_uses_g3a_then_g3b`
- `test_no_direct_prepare_run_or_provider_launch` (source scan)

`_mark_item_failed` coverage (`test_dataset_experiment_start.py` or a dedicated
`test_dataset_experiment_mark_item_failed.py`), real Sessions:

- `test_mark_item_failed_wrong_experiment_fails_closed` — Item belongs to
  Experiment B; call `_mark_item_failed(item_b, ..., experiment_id=A,
  coordinator_token=A_valid)` → `DATASET_EXPERIMENT_INVARIANT_VIOLATION`; Item B
  unchanged.
- `test_mark_item_failed_already_failed_is_idempotent` — same Experiment, valid
  generation, Item already `failed` → returns `False`; Item remains `failed`; no
  ownership/invariant error.
- `test_mark_item_failed_queued_projects_failed_once` — valid queued Item →
  returns `True`; `status="failed"` with bounded `last_error_type`/
  `last_error_message`; a second call returns `False` (exactly one durable
  mutation).
- `test_mark_item_failed_running_projects_failed_once` — valid running Item →
  same assertions.
- `test_mark_item_failed_stale_generation_fence_lost` — stale token →
  `DATASET_EXPERIMENT_FENCE_LOST`; zero Item mutation (retained).

Shared one-step helper:

```python
def _step_once(client, experiment_id, token, *, pipeline=None, provider=None,
               prepare_override=None):
    session_factory = client.app.state.database.session_factory

    def services_factory(session):
        registry = PipelineRegistry([pipeline or G3LocalPipeline()])
        executor_registry = FakeRegistry({"local_cpu": provider or FakeProvider("local_cpu")})
        ds = DatasetExperimentService(session, registry, None, executor_registry)
        analysis = AnalysisService(session, registry, client.app.state.job_manager,
                                   executor_registry=executor_registry)
        if prepare_override is not None:
            analysis.prepare_run = prepare_override
        return ds, analysis

    coordinator = DatasetExperimentCoordinator(
        session_factory=session_factory, services_factory=services_factory,
    )
    return coordinator.step(experiment_id, token)
```

Classification injections use `prepare_override` callables that raise
`PlatformError(<code>, "injected")`.

### Steps

- [ ] Write the four test files; RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_start.py \
  backend/tests/test_dataset_experiment_coordinator_fencing.py \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_coordinator_scheduling.py -v
```
Expected RED: `ModuleNotFoundError: app.dataset_experiments.coordinator`.
- [ ] Implement the exact `service.py` helpers, `coordinator.py`,
  `job_manager.py`.
- [ ] GREEN: same invocation.
- [ ] Focused regressions:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_generation_fence.py \
  backend/tests/test_dataset_experiment_coordinator_reconciliation.py \
  backend/tests/test_dataset_experiment_launch.py -q
```
- [ ] Commit: `feat: add dataset experiment coordinator core`

---

# TASK 4 — Worker loop + control-plane wiring (behavior)

**Files:**
- Create: `backend/app/dataset_experiments/wiring.py`
- Create: `backend/app/dataset_experiments/worker.py`
- Create: `backend/tests/test_dataset_experiment_worker.py`

Tests:

- `test_worker_loop_exits_on_terminal_outcome` (monkeypatch
  `DatasetExperimentCoordinator.step` to return `EXPERIMENT_TERMINAL`; assert one
  call and return 0)
- `test_worker_loop_honors_max_iterations` (`step` returns `WAITING`;
  `max_iterations=2`; assert two calls and return 0)
- `test_build_control_plane_dependencies_executes_torch_free` (fresh subprocess
  builds `Settings` under a temp dir with no remote env, calls
  `build_control_plane_dependencies`, asserts `torch`/`ultralytics` not in
  `sys.modules`, prints `OK`)
- `test_wiring_dependencies_construct_coordinator_services` (build deps and
  construct `DatasetExperimentService`/`AnalysisService` on a Session)
- `test_coordinator_packages_import_is_torch_free` (import-only subprocess)

### Steps

- [ ] Write tests; RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_worker.py -v
```
- [ ] Implement `wiring.py` and `worker.py`.
- [ ] GREEN; focused regression `backend/tests/test_dataset_experiment_start.py`.
- [ ] Commit: `feat: add dataset experiment coordinator worker and wiring`

---

# TASK 5 — Regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_dataset_experiment_coordinator_regression.py`

Guards:

- `DatasetExperiment` and `AnalysisRun` schemas unchanged (exact column sets).
- `coordinator.py` source contains no `prepare_run(`, no `provider.launch(`, no
  `DatasetEvaluation`, no `evaluating`, no `completed` assignment, no token
  rotation.
- `worker.py`/`wiring.py`/`job_manager.py` import no `torch`/`ultralytics`
  (fresh subprocess).
- `remote_execution/*` unchanged.

Regression commands:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_generation_fence.py \
  backend/tests/test_dataset_experiment_start.py \
  backend/tests/test_dataset_experiment_coordinator_fencing.py \
  backend/tests/test_dataset_experiment_coordinator_reconciliation.py \
  backend/tests/test_dataset_experiment_coordinator_scheduling.py \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_worker.py \
  backend/tests/test_dataset_experiment_coordinator_regression.py -v

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
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
  backend/tests/test_remote_coordinator_fencing.py -q

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Full-suite baseline: `1480 passed, 28 skipped`; require 0 failed / 0 errors with
a fresh final pytest summary. Commit: `test: add phase G G3C regression matrix`.

---

## Error Classification (authoritative table)

| Code | Class | Effect |
|---|---|---|
| `DATASET_EXPERIMENT_*` (incl. `..._FENCE_LOST`, `..._INVARIANT_VIOLATION`, `..._EXECUTION_IDENTITY_CHANGED`, `..._ORCHESTRATION_FAILED`, `..._NOT_FOUND`, `..._ITEM_NOT_FOUND`, `..._ATTEMPT_NOT_FOUND`, `..._INVALID_TRANSITION`) | Experiment-level | `Experiment -> failed`, stop scheduling |
| `ANALYSIS_RUN_NOT_LAUNCHABLE` | Experiment-level | impossible launch authority/state |
| `EXECUTION_CAPABILITY_UNAVAILABLE` | Experiment-level | executor registry/provider global corruption |
| `EXECUTION_NOT_CERTIFIED` | Experiment-level | frozen certificate drift |
| `MODEL_RELEASE_MISMATCH` | Experiment-level | frozen release identity drift |
| `PIPELINE_INCOMPATIBLE` | Experiment-level | control-plane catalog corruption |
| `PLUGIN_API_INCOMPATIBLE` | Experiment-level | plugin API drift |
| `PLUGIN_NOT_FOUND` | Experiment-level | frozen plugin identity drift |
| `PLUGIN_PARAMETERS_INVALID` | Experiment-level | frozen parameters drift |
| `RECORDING_NOT_FOUND` | Experiment-level | frozen membership corruption |
| `REMOTE_IMPLEMENTATION_MISMATCH` | Experiment-level | live remote runtime commit drifted from the frozen required runtime commit (§14.2 execution-identity drift) |
| `PIPELINE_ASSET_MISMATCH` | Experiment-level | pre-run remote probe returned an asset manifest SHA different from the frozen release manifest (§14.2 ModelRelease/AssetManifest drift) |
| `INPUT_INCOMPATIBLE` | Item-level | recording-specific input failure |
| `EXECUTOR_UNAVAILABLE` | Item-level | recording-specific probe failure |
| `ANALYSIS_FAILED` | Item-level | provider execution failure |
| `SOURCE_DATA_NOT_FOUND` / `SOURCE_DATA_NOT_FILE` | Item-level | recording source failure |
| `REMOTE_EXECUTOR_UNAVAILABLE` / `REMOTE_TRANSPORT_UNAVAILABLE` / `REMOTE_PROBE_UNAVAILABLE` | Item-level | recording-specific remote availability failure |
| any other code | Experiment-level (fail closed) | unknown → stop |

`DATASET_EXPERIMENT_FENCE_LOST` is handled before the table and maps to
`FENCE_LOST` (no Experiment failure).

### Pre-run vs run-level `PIPELINE_ASSET_MISMATCH`

`is_experiment_level()` classifies only errors observed on the **pre-run
scheduling path** (revalidation, `prepare_run` availability/probe, G3-A/G3-B
claims). On that path, `PIPELINE_ASSET_MISMATCH` and
`REMOTE_IMPLEMENTATION_MISMATCH` mean the live remote deployment drifted from the
frozen required runtime commit / release asset manifest — a frozen
execution/scientific identity failure (§14.2) that must fail the Experiment and
stop scheduling.

A run-level asset/deployment failure that occurs **after** an `AnalysisRun` has
physically launched (worker/remote-runtime execution failure) is an
`AnalysisRun` terminal outcome and is projected by reconciliation into
`Item.failed` (§14.1). It never passes through the coordinator's pre-run
`is_experiment_level()` classifier, so moving these two codes to
Experiment-level does not change §14.1 run-level failure semantics.

---

## Queued Historical-Attempt Rule

For a `queued` Item:

- no Attempts → valid (initial queued);
- Attempts exist, `active_attempts == 0`, and the latest Run is `failed` or
  `interrupted` → valid (G4 Retry Failed requeue);
- latest Run is `completed` → `DATASET_EXPERIMENT_INVARIANT_VIOLATION`;
- any active (`pending`/`running`) Attempt → `DATASET_EXPERIMENT_INVARIANT_VIOLATION`;
- every historical Attempt's Run must exist and match the frozen identity.

---

## Accepted-Preserved Behaviors

- all-success → `CoordinatorOutcome.INFERENCE_COMPLETE`; Experiment stays
  `running`; no `DatasetEvaluation`; no `evaluating`; no `completed`; worker
  exits; G5 replaces the branch.
- `completed_with_failures` remains G3-C.
- deterministic `manifest_order` scheduling; `max_concurrency`.
- G3-A → G3-B composition; no direct `prepare_run`/`provider.launch`.
- control-plane only; short-lived Session per step; no sleep in `step`.
- no G4/G5; no schema/migration; no `remote_execution/*`.
- step order remains `token → status → heartbeat`.

---

## Self-Review

1. No placeholder implementation code: every new/modified method and module has
   complete exact code above. PASS
2. Error classification table, prose, and tests agree:
   `test_item_level_failure...` uses `INPUT_INCOMPATIBLE`;
   `EXECUTION_CAPABILITY_UNAVAILABLE` is Experiment-level;
   `REMOTE_IMPLEMENTATION_MISMATCH` and `PIPELINE_ASSET_MISMATCH` are
   Experiment-level on the pre-run path, with run-level projection unchanged. PASS
3. Queued historical Attempts allow only failed/interrupted; completed → invariant. PASS
4. Matching-token tests persist `Experiment.coordinator_token = token` before the
   fenced call. PASS
5. `_mark_item_failed` includes `experiment_id` in the UPDATE and precisely
   classifies zero-row outcomes; wrong-experiment, idempotent-terminal, and
   exactly-once queued/running projection tests are enumerated. PASS
6. Reconciliation keeps `try/commit/except rollback`; coordinator rolls back
   before `_fail_experiment`; all-or-nothing test present. PASS
7. Generation claims remain in the final CAS (EXISTS); no SELECT-then-UPDATE,
   no in-memory flag, no heartbeat-only fence. PASS
8. Real stale-session start CAS + zero spawn + BUSY fail-closed. PASS
9. G5 handoff preserved. PASS
10. No G4/G5 scope creep; no schema/remote changes. PASS
11. No TODO/TBD/XXX/ellipsis-as-missing-code; no cross-test imports. PASS

---

## Out-of-Scope Guardrails During Implementation

- If generation fencing cannot be added without breaking sealed G3-A/G3-B or any
  schema change, STOP and report.
- If reconciliation cannot be all-or-nothing, STOP.
- If G3-C appears to need evaluation, `evaluating`/`completed`, Retry Failed,
  token rotation, restart recovery, or a new state, STOP — G4/G5.
- If the coordinator cannot remain torch-free, STOP.
