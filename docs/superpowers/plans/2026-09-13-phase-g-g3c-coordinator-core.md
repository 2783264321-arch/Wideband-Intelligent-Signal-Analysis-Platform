# Phase G G3-C Coordinator Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G3-C sub-gate):** Complete the approved Phase G **G3 — Orchestrator
Core** by turning the sealed G3-A/G3-B per-Item primitives into normal
dataset-level execution: durable initial coordinator ownership
(`pending -> running` + `coordinator_token`), a fenced single-iteration
coordinator, Item reconciliation from `AnalysisRun` authority, bounded
`max_concurrency` scheduling in deterministic `manifest_order`, best-effort
continuation across ordinary Item failures, and the terminal inference
transitions. G3-C is normal orchestration only; it is not G4 recovery/retry and
not G5 evaluation/API.

**Architecture:** A control-plane-only coordinator package
(`backend/app/dataset_experiments/{coordinator,job_manager,worker,wiring}.py`)
plus additive service methods on the sealed `DatasetExperimentService`
(`start_experiment`, `reconcile_items`, `select_queued_items`,
`mark_experiment_completed_with_failures`, `_fail_experiment`). Each coordinator
iteration is one testable `step(...)` call that opens its own short-lived
Session; a thin worker loop calls `step` then sleeps a bounded interval. New Item
execution always composes the sealed `start_item_attempt` (Transaction A) then
`launch_item_attempt` (Transaction B + physical launch). No schema change; no
`remote_execution/*` change; no `prepare_run`/`provider.launch` calls from the
coordinator.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, default engine, no PRAGMAs,
`expire_on_commit=False`), subprocess job managers, pytest 9. No GPU, no SSH, no
torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
(§5 state machines, §9 coordinator loop, §10 bounded concurrency, §12 restart
recovery, §14.2 error families, §15 invariants, §20 G3).

**Sealed dependencies:**
- G3-A/G3-B: `docs/superpowers/plans/2026-09-13-phase-g-g3-transaction-a-ownership.md`,
  `docs/superpowers/plans/2026-09-13-phase-g-g3b-durable-first-launch.md`.
- G2: `AnalysisService.prepare_run` / `launch_prepared_run`.

**Base:** `feature/m9-2-implementation @ 0398c53080edbd30a79e2be009ea755564a4ecb1`.

---

## Phase G Gate Structure (authoritative §20)

G3-A (ownership) + G3-B (durable first launch) + **G3-C (this plan)** together
complete the approved **G3 — Orchestrator Core**. G4 owns Retry Failed, local
ambiguity recovery, coordinator-token rotation/restart, duplicate-active-Attempt
recovery, and remote restart recovery. G5 owns DatasetEvaluation creation,
`running -> evaluating`, evaluation reconciliation, and REST.

G3-C MUST NOT implement any G4/G5 behavior.

---

## Process / Job-Manager Pattern Audit (verified)

- `LocalJobManager.start` (`analysis/job_manager.py:16`) and
  `LocalBenchmarkJobManager.start` (`benchmarks/job_manager.py:16`) both spawn
  `[sys.executable, "-m", "<module>", <id>]` with `cwd=backend_root`, a copied
  env plus `WSP_PROJECT_ROOT/DATA_ROOT/LABEL_SPACE_ROOT/DATABASE_URL`,
  `stdin/out/err=DEVNULL`, `shell=False`, return `process.pid`.
- `CoordinatorJobManager.launch` (`remote_execution/coordinator_job_manager.py:26`)
  adds `--coordinator-token <token>`.
- Workers open their own engine/session: `benchmarks/worker.py:109` builds
  `Database(settings.database_url)`, `load_domain_models()`,
  `Base.metadata.create_all`, `run_additive_migrations`, then
  `database.session_factory()`.
- Parent PID persistence convention: `BenchmarkService.start_evaluation`
  (`benchmarks/service.py:606`) spawns, on exception marks the entity `failed`
  and raises; otherwise persists `worker_pid` and commits. The worker owns the
  `running` transition. G3-C mirrors this for `DatasetExperiment`.
- Coordinator fencing precedent: `remote_execution/coordinator.py:97`
  `_require_current_fence` re-reads the token from the DB in a fresh session
  before side effects; `startup.find_or_new_coordinator_token` /
  `rotate_coordinator_token` generate/rotate tokens.
- No existing DatasetExperiment job manager/worker/coordinator exists; G3-C
  creates them following the conventions above (not a new generic framework).

---

## Global Constraints

1. **New Item execution composes G3-A then G3-B only.**
   `start_item_attempt(...)` then `launch_item_attempt(...)`. The coordinator
   never calls `prepare_run` or `provider.launch`.
2. **Durable coordinator ownership is a CAS.** Concurrent starts of one pending
   Experiment produce exactly one normal coordinator.
3. **Token fence is durable.** Every iteration verifies
   `Experiment.coordinator_token == supplied token`; a stale coordinator exits
   without heartbeat or scheduling.
4. **No evaluation, no new state.** G3-C never creates a `DatasetEvaluation`,
   never sets `evaluating` or `completed`.
5. **No schema/migration change.** `coordinator_token`, `worker_pid`,
   `heartbeat_at`, `started_at`, `completed_at`, `error_type`, `error_message`
   already exist on `DatasetExperimentModel`.
6. **No `remote_execution/*` change; remote recovery/token rotation is G4.**
7. **Control-plane only.** Coordinator packages import no `torch`/`ultralytics`
   and no model-specific scientific code.
8. **Small transactions.** No single transaction spans Recording I/O, probes,
   provider launch, subprocess spawn, or sleep.

---

## Repository Mapping (verified against HEAD 0398c53)

- `backend/app/dataset_experiments/model.py`
  - `DatasetExperimentModel` (`:16`): `status` (default `pending`),
    `coordinator_token`, `worker_pid`, `heartbeat_at`, `started_at`,
    `completed_at`, `error_type`, `error_message`, `max_concurrency`, `executor`.
  - `DatasetExperimentItemModel` (`:67`): `status` (`queued`/`running`/`completed`/`failed`),
    `last_error_type`, `last_error_message`, `manifest_order`, `recording_id`.
  - `DatasetExperimentAttemptModel` (`:100`): `attempt_number`, `analysis_run_id`,
    `launch_requested_at`; `UNIQUE(experiment_item_id, attempt_number)`.
- `backend/app/dataset_experiments/service.py`
  - `__init__(session, registry, model_release_store, executor_registry)` (`:33`).
  - `revalidate_frozen_identity` (`:270`), `start_item_attempt` (`:419`),
    `launch_item_attempt` (`:528`).
- `backend/app/analysis/model.py` — `AnalysisRunModel` `status`
  (`pending`/`running`/`completed`/`failed`/`interrupted`), `error_type`,
  `error_message`.
- `backend/app/analysis/local_inference_worker.py:45` —
  `_TERMINAL_STATUSES = {"completed","failed","interrupted"}`.
- `backend/app/main.py:114` — app wiring, startup recovery order
  (`mark_stale_local_cpu_runs_interrupted`,
  `mark_stale_running_evaluations_interrupted`, `coordinate_orphaned_remote_runs`).
  G3-C does not add DatasetExperiment recovery here (G4).
- Existing test owners: all `test_dataset_experiment_*` suites, `executor_fixtures.py`,
  `benchmark_fixture.py`, `test_remote_startup_recovery.py`.

### Test command conventions

- Single: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`

---

## Proposed G3-C Files / Interfaces

New files:
- `backend/app/dataset_experiments/coordinator.py` — `CoordinatorOutcome`,
  `DatasetExperimentCoordinator`.
- `backend/app/dataset_experiments/job_manager.py` — `DatasetExperimentJobManager`.
- `backend/app/dataset_experiments/worker.py` — `run_coordinator`, `main`.
- `backend/app/dataset_experiments/wiring.py` — `build_control_plane_dependencies`.

Modified file:
- `backend/app/dataset_experiments/service.py` — add `start_experiment`,
  `reconcile_items`, `select_queued_items`,
  `mark_experiment_completed_with_failures`, `_fail_experiment`,
  `_mark_item_failed`, `_load_attempts_by_item`, `_load_runs_by_id`.

Exact signatures:

```python
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

class DatasetExperimentCoordinator:
    def __init__(self, *, session_factory, services_factory, sleep_fn=time.sleep,
                 poll_interval=1.0, max_iterations=None): ...
    def step(self, experiment_id: str, coordinator_token: str) -> CoordinatorOutcome: ...

class DatasetExperimentJobManager:
    DEFAULT_COORDINATOR_MODULE = "app.dataset_experiments.worker"
    def __init__(self, settings): ...
    def start(self, experiment_id: str, coordinator_token: str) -> int: ...

def build_control_plane_dependencies(settings) -> ControlPlaneDependencies: ...
def run_coordinator(experiment_id, coordinator_token, *, settings=None,
                    poll_interval=1.0, max_iterations=None) -> int: ...
```

`services_factory(session) -> tuple[DatasetExperimentService, AnalysisService]`.

### Additive service signatures

```python
def start_experiment(self, experiment_id: str, job_manager) -> DatasetExperimentModel: ...
def reconcile_items(self, experiment_id: str) -> ReconcileSummary: ...
def select_queued_items(self, experiment_id: str, limit: int) -> list[str]: ...
def refresh_coordinator_heartbeat(self, experiment_id: str, coordinator_token: str) -> bool: ...
def mark_experiment_completed_with_failures(self, experiment_id: str, coordinator_token: str) -> bool: ...
def _fail_experiment(self, experiment_id: str, coordinator_token: str, error_type: str, error_message: str) -> bool: ...
def _mark_item_failed(self, item_id: str, error_type: str, error_message: str | None) -> None: ...
def _load_attempts_by_item(self, item_ids) -> dict: ...
def _load_runs_by_id(self, run_ids) -> dict: ...
```

`ReconcileSummary` is a frozen dataclass with
`expected, queued, running, completed, failed: int`.

---

## Initial Coordinator Durable Ownership Ordering

`start_experiment(experiment_id, job_manager)`:

1. Load Experiment; missing → `DATASET_EXPERIMENT_NOT_FOUND` (404).
2. CAS `UPDATE dataset_experiments SET status='running',
   coordinator_token=:token, started_at=:now, heartbeat_at=:now, worker_pid=NULL,
   error_type=NULL, error_message=NULL WHERE id=:id AND status='pending'`.
   - `rowcount != 1` → commit/rollback and raise
     `DATASET_EXPERIMENT_INVALID_TRANSITION` (409) (only a pending Experiment can
     start; this is the duplicate-start guard).
   - commit.
3. Spawn the coordinator with `job_manager.start(experiment_id, token)`.
   - Spawn failure → guarded `UPDATE ... SET status='failed', worker_pid=NULL,
     error_type='DATASET_EXPERIMENT_ORCHESTRATION_FAILED',
     error_message=str(exc)[:1000], completed_at=:now WHERE id=:id AND
     coordinator_token=:token`; commit; raise `PlatformError(
     "DATASET_EXPERIMENT_ORCHESTRATION_FAILED", ...)`.
4. Persist `worker_pid` with a token-guarded `UPDATE ... WHERE id=:id AND
   coordinator_token=:token`; commit; return the refreshed Experiment.

Crash windows:

| Window | Durable state | Consequence | Owner |
|---|---|---|---|
| Before CAS commit | `pending` | Duplicate start races resolve to one CAS winner; no coordinator | G3-C |
| After CAS commit, before spawn | `running` + token, no `worker_pid`, no process | Non-terminal running Experiment with no coordinator | G4 restart recovery |
| After spawn, before `worker_pid` commit | coordinator running, `worker_pid` NULL | Coordinator operates; PID not recorded | G4 |
| Spawn raises | `failed` + `ORCHESTRATION_FAILED` | Terminal fail-closed | G3-C |

G3-C performs the normal first start only; it does not recover windows 2/3.

---

## Coordinator-Token Fence

Every `step`:

1. Load Experiment by id; missing → `FENCE_LOST`.
2. If `experiment.coordinator_token != coordinator_token` → `FENCE_LOST` (no
   heartbeat, no scheduling, no other writes).
3. Heartbeat: guarded `UPDATE dataset_experiments SET heartbeat_at=:now WHERE
   id=:id AND coordinator_token=:token AND status='running'`; `rowcount != 1` →
   `FENCE_LOST`; commit.

A stale coordinator therefore cannot heartbeat, cannot schedule, and cannot
overwrite the newer generation. G4 later rotates the token during recovery; G3-C
never rotates it.

---

## Single-Iteration Architecture

`DatasetExperimentCoordinator.step(experiment_id, coordinator_token)` opens one
short-lived Session via `session_factory`, builds `(DatasetExperimentService,
AnalysisService)` via `services_factory(session)`, runs the deterministic order
below, closes the Session, and returns a `CoordinatorOutcome`. It never sleeps.

Conceptual order:

```
1. fence: load experiment, verify token
2. heartbeat (guarded by token)
3. if experiment.status != "running": return EXPERIMENT_TERMINAL
4. reconcile Items from AnalysisRun authority
5. revalidate_frozen_identity (read-only)
6. terminal inference conditions
7. if still running: free_slots, select queued by manifest_order, start via G3-A + G3-B
8. return outcome
```

The thin worker loop (`run_coordinator`) performs
`step -> sleep bounded interval -> repeat` and exits on `EXIT_OUTCOMES`.

---

## Reconciliation Algorithm

`reconcile_items(experiment_id)`:

1. Load all Items ordered by `manifest_order`; empty → raise
   `DATASET_EXPERIMENT_INVARIANT_VIOLATION` (experiment-level).
2. Load all Attempts for those Items ordered by `(experiment_item_id,
   attempt_number)`; batch-load referenced `AnalysisRunModel` rows by id.
3. For each Item, `latest` = Attempt with the greatest `attempt_number`
   (authoritative), and `active_attempts` = Attempts whose Run status is
   `pending`/`running`.

| Item status | Rule | Result |
|---|---|---|
| `running` | `latest` exists, Run exists, `Run.recording_id == Item.recording_id` | project below |
| `running` | Run `pending`/`running` | Item stays `running` |
| `running` | Run `completed` | Item → `completed` |
| `running` | Run `failed`/`interrupted` | Item → `failed`; copy bounded `Run.error_type`/`Run.error_message` into `last_error_type`/`last_error_message` |
| `running` | no Attempt / missing Run / wrong recording / unknown Run status | raise `DATASET_EXPERIMENT_INVARIANT_VIOLATION` |
| `completed` | `latest` exists and its Run is `completed` | unchanged |
| `completed` | otherwise (no Attempt, or Run not `completed`) | raise invariant |
| `failed` | no Attempt (G3-C scheduling failure) | allowed, unchanged |
| `failed` | has `latest` and Run is `failed`/`interrupted` | allowed, unchanged |
| `failed` | has `latest` and Run is not `failed`/`interrupted` | raise invariant |
| `queued` | (G4 may have historical Attempts) | unchanged |
| other | unknown status | raise invariant |
| any | `len(active_attempts) > 1` | raise invariant (one active Attempt per Item) |

`reconcile_items` commits Item projections in one small transaction and returns
`ReconcileSummary(expected, queued, running, completed, failed)`. It never mutates
`AnalysisRun` rows.

Invariant classification: all reconciliation invariant raises are
**experiment-level** and cause the coordinator to fail the Experiment and stop
(§14.2). Missing historical Attempts/Runs caused by G4 retry/requeue are not
in G3-C's scope; the strict rules above target the G3-C deployment model and are
documented as revisitable in G4.

### Efficient loading

- One `SELECT ... ORDER BY manifest_order` for Items.
- One `SELECT ... WHERE experiment_item_id IN (...) ORDER BY experiment_item_id,
  attempt_number` for Attempts.
- One `SELECT ... WHERE id IN (run_ids)` for Runs.

No per-Item query in the loop.

---

## Error Classification (best-effort vs experiment-level)

Central rule:

```python
def _is_experiment_level(exc: PlatformError) -> bool:
    return exc.code.startswith("DATASET_EXPERIMENT_")
```

- **Experiment-level** (`DATASET_EXPERIMENT_*`): frozen-identity drift
  (`DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED`), reconciliation/ownership
  corruption (`DATASET_EXPERIMENT_INVARIANT_VIOLATION`), orchestration failure
  (`DATASET_EXPERIMENT_ORCHESTRATION_FAILED`), missing structured rows
  (`DATASET_EXPERIMENT_NOT_FOUND` / `..._ITEM_NOT_FOUND` /
  `..._ATTEMPT_NOT_FOUND`), invalid transition
  (`DATASET_EXPERIMENT_INVALID_TRANSITION`). Effect: `Experiment -> failed`
  with bounded `error_type`/`error_message`; stop scheduling; return
  `INVARIANT_FAILED`.
- **Item-level execution failure** (any other `PlatformError` from `prepare_run`
  / `launch_prepared_run` / G3-A/G3-B on a specific Item): `RECORDING_NOT_FOUND`,
  `PIPELINE_INCOMPATIBLE`/`PLUGIN_NOT_FOUND`, `PLUGIN_PARAMETERS_INVALID`,
  `MODEL_RELEASE_MISMATCH`, `EXECUTION_CAPABILITY_UNAVAILABLE`,
  `EXECUTION_NOT_CERTIFIED`, `INPUT_INCOMPATIBLE`, `REMOTE_EXECUTOR_UNAVAILABLE`,
  `ANALYSIS_FAILED`, `ANALYSIS_RUN_NOT_LAUNCHABLE`, `SOURCE_DATA_*`, etc. Effect:
  `Item -> failed` with `last_error_type`/`last_error_message`; continue with the
  remaining queued Items.
- **Unexpected non-`PlatformError` exceptions** are fail-closed
  **experiment-level**: `Experiment -> failed` with
  `DATASET_EXPERIMENT_ORCHESTRATION_FAILED`. There is no broad
  `except Exception -> Item.failed` rule.

**Per-Item scheduling failure before ownership.** `start_item_attempt` rolls
back Transaction A and leaves the Item `queued`. G3-C's scheduling catch handles
the per-Item `PlatformError` by calling `_mark_item_failed(item_id, code,
message)` — a direct guarded `UPDATE dataset_experiment_items SET
status='failed', last_error_type=..., last_error_message=...
WHERE id=:id AND status IN ('queued','running')` + commit. No fake
`AnalysisRun`/`Attempt` is created. If Transaction A did commit and Transaction B
launch failed, the Item is `running` with a failed Run; the same
`_mark_item_failed` projection applies and the Attempt/Run remain authoritative
history.

---

## Bounded-Concurrency Algorithm

After reconciliation (all `running` Items already verified to have an active
Run):

```
active = summary.running            # verified against Attempt/Run authority
free_slots = max(0, experiment.max_concurrency - active)
if free_slots == 0: return WAITING (if summary.running > 0) else terminal check
queued_ids = select_queued_items(experiment_id, limit=free_slots)  # manifest_order ASC
```

`select_queued_items(experiment_id, limit)`:

```python
list(self.session.scalars(
    select(DatasetExperimentItemModel.id)
    .where(DatasetExperimentItemModel.experiment_id == experiment_id,
           DatasetExperimentItemModel.status == "queued")
    .order_by(DatasetExperimentItemModel.manifest_order.asc())
    .limit(limit)
).all())
```

Start at most `len(queued_ids)` Items; never oversubscribe. No global
cross-Experiment scheduler is introduced (spec §10 accepted V1 behavior).

---

## Best-Effort Scheduling Composition

Per selected Item:

```python
try:
    attempt = ds.start_item_attempt(experiment_id=..., item_id=..., analysis_service=analysis)
    ds.launch_item_attempt(experiment_id=..., item_id=..., attempt_id=attempt.id,
                           analysis_service=analysis)
    started += 1
except PlatformError as exc:
    if _is_experiment_level(exc):
        raise
    ds._mark_item_failed(item_id, exc.code, exc.message)
    # continue with the remaining selected Items
```

A selected Item's ordinary failure does not stop later queued Items. If an
experiment-level error occurs, it propagates to the step handler, which fails the
Experiment and returns `INVARIANT_FAILED` (Items already started in this
iteration keep their Runs; terminal immutability preserved).

---

## Terminal Inference Conditions

Computed after reconciliation and revalidation, only when
`summary.queued == 0 and summary.running == 0`:

- `summary.failed > 0`:
  `mark_experiment_completed_with_failures(experiment_id, token)` (guarded by
  token + `status='running'`; sets `status='completed_with_failures'`,
  `completed_at=:now`, clears `error_type`/`error_message`); return
  `COMPLETED_WITH_FAILURES`.
- `summary.failed == 0 and summary.completed > 0`: return `INFERENCE_COMPLETE`
  with **no Experiment mutation**.
- otherwise (all zero, empty experiment): raise
  `DATASET_EXPERIMENT_INVARIANT_VIOLATION` (handled as experiment-level).

### All-success G5 handoff resolution

- G3-C **does not** set `evaluating` (no `DatasetEvaluation` exists) and **does
  not** set `completed`. The Experiment remains `running` (scientifically
  honest: inference finished, formal evaluation pending).
- `CoordinatorOutcome.INFERENCE_COMPLETE` is the explicit handoff seam. The
  worker **exits** on this outcome (no evaluation reconciliation exists in
  G3-C; idling would do nothing and would hold a process hostage).
- G5 replaces this branch: it creates/links the `DatasetEvaluation`, sets
  `running -> evaluating`, and continues into evaluation reconciliation instead
  of exiting. The outcome name and the step boundary are the extension points.
- G4 restart recovery restarting a `running` Experiment whose inference is
  complete recomputes `INFERENCE_COMPLETE` idempotently and exits; no duplicate
  work, no state corruption.

No new persisted state is introduced and no placeholder `DatasetEvaluation` is
created.

---

## Experiment Failure

`_fail_experiment(experiment_id, coordinator_token, error_type, error_message)`:

```python
with self.session.no_autoflush:
    result = self.session.execute(
        update(DatasetExperimentModel)
        .where(DatasetExperimentModel.id == experiment_id,
               DatasetExperimentModel.coordinator_token == coordinator_token,
               DatasetExperimentModel.status == "running")
        .values(status="failed", error_type=error_type, error_message=(error_message or "")[:1000],
                completed_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
self.session.commit()
return int(result.rowcount or 0) == 1
```

If the guarded update returns 0 (fence lost), the coordinator returns
`FENCE_LOST` rather than overwriting the newer generation.

---

## Heartbeat Semantics

- Parent sets `heartbeat_at=:now` at start (initial heartbeat).
- Each `step` updates `heartbeat_at=:now` under the token fence before any
  scheduling.
- No stale-heartbeat threshold, no process kill, no recovery in G3-C; G4 owns
  stale-coordinator detection/recovery.

---

## Transaction Boundaries

- `step` opens one short-lived Session per iteration and closes it before the
  worker sleeps.
- `start_experiment`: one commit for the CAS, then spawn (no DB transaction
  held), then one commit for `worker_pid`.
- Each Item: G3-A commits Transaction A, G3-B commits Transaction B and the
  post-launch result; the coordinator holds no transaction across those.
- `reconcile_items`, `_mark_item_failed`, `mark_...`, `_fail_experiment` each
  commit their own small transaction.
- No Session/transaction spans Recording hashing/reading, capability probes,
  provider launch, subprocess spawn, or sleep.

---

## G3-C vs G4 vs G5 Boundary

- **G3-C (this plan):** `pending -> running` start ownership + token; fenced
  step loop; reconciliation; bounded scheduling; best-effort continuation;
  `running -> completed_with_failures`; all-success handoff seam.
- **G4:** Retry Failed; failed Item requeue; local ambiguity recovery; remote
  restart recovery; coordinator-token rotation; stale-coordinator recovery;
  duplicate-active-Attempt recovery; startup DatasetExperiment recovery.
- **G5:** DatasetEvaluation creation; `running -> evaluating`; evaluation
  reconciliation; Retry Evaluation; REST/read models.
- G3-C never creates evaluation, never sets `evaluating`/`completed`, never
  rotates the token, never requeues failed Items, never recovers a crashed
  coordinator.

---

## Task 1 — Experiment start ownership + job manager (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`
- Create: `backend/app/dataset_experiments/job_manager.py`
- Create: `backend/tests/test_dataset_experiment_start.py`

### `job_manager.py`

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
        module = self.DEFAULT_COORDINATOR_MODULE
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
            [sys.executable, "-m", module, experiment_id,
             "--coordinator-token", coordinator_token],
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

### `start_experiment` (add to `DatasetExperimentService`)

```python
    def start_experiment(self, experiment_id, job_manager):
        """Durably claim normal coordinator ownership, then spawn the coordinator.

        CAS `pending -> running` with a fresh coordinator_token, commit, then
        spawn, then persist worker_pid under the token.
        """
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
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

        with self.session.no_autoflush:
            self.session.execute(
                update(DatasetExperimentModel)
                .where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.coordinator_token == token,
                )
                .values(worker_pid=worker_pid)
                .execution_options(synchronize_session=False)
            )
        self.session.commit()
        self.session.expire_all()
        return self.session.get(DatasetExperimentModel, experiment_id)
```

### Tests — `backend/tests/test_dataset_experiment_start.py`

Re-declare the G3-A helpers (`G3LocalPipeline`, `_seed_dataset`, `_services`,
`_experiment`, `_item`). Add a recording job manager and:

```python
class RecordingJobManager:
    def __init__(self, *, token_probe=None, fail=False):
        self.calls = []
        self.fail = fail
        self.token_probe = token_probe

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        if self.token_probe is not None:
            self.token_probe(experiment_id, coordinator_token)
        if self.fail:
            raise RuntimeError("spawn failed")
        return 4242


def test_start_experiment_pending_to_running_token_committed_before_spawn(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds, status="pending")

    def probe(experiment_id, token):
        with client.app.state.database.session_factory() as fresh:
            stored = fresh.get(DatasetExperimentModel, experiment_id)
            assert stored.status == "running"
            assert stored.coordinator_token == token  # durable before spawn
            assert stored.worker_pid is None

    job_manager = RecordingJobManager(token_probe=probe)
    started = ds.start_experiment(experiment.id, job_manager)
    assert started.status == "running"
    assert started.coordinator_token is not None
    assert started.worker_pid == 4242
    assert len(job_manager.calls) == 1


def test_start_experiment_duplicate_start_has_single_winner(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds, status="pending")
    job_manager = RecordingJobManager()
    ds.start_experiment(experiment.id, job_manager)

    with pytest.raises(PlatformError) as exc:
        ds.start_experiment(experiment.id, job_manager)
    assert exc.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
    assert len(job_manager.calls) == 1  # only one coordinator spawned


def test_start_experiment_spawn_failure_marks_failed(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds, status="pending")
    with pytest.raises(PlatformError) as exc:
        ds.start_experiment(experiment.id, RecordingJobManager(fail=True))
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(DatasetExperimentModel, experiment.id)
        assert stored.status == "failed"
        assert stored.error_type == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"
        assert stored.worker_pid is None


def test_start_experiment_rejects_missing_and_non_pending(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    with pytest.raises(PlatformError) as missing:
        ds.start_experiment("missing", RecordingJobManager())
    assert missing.value.code == "DATASET_EXPERIMENT_NOT_FOUND"
    experiment = _experiment(ds, status="running")
    with pytest.raises(PlatformError) as non_pending:
        ds.start_experiment(experiment.id, RecordingJobManager())
    assert non_pending.value.code == "DATASET_EXPERIMENT_INVALID_TRANSITION"
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_start.py`.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_start.py -v
```
Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'start_experiment'`.
- [ ] **Implement minimal code:** add `job_manager.py` and `start_experiment`.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_launch.py -q
```
- [ ] **Commit checkpoint:** `feat: add dataset experiment coordinator start ownership`

---

## Task 2 — Item reconciliation + queued selection (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (add `ReconcileSummary`,
  `reconcile_items`, `select_queued_items`, `_load_attempts_by_item`,
  `_load_runs_by_id`).
- Create: `backend/tests/test_dataset_experiment_coordinator_reconciliation.py`

### Exact production code (add)

Module level (add `from dataclasses import dataclass` to the existing imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ReconcileSummary:
    expected: int
    queued: int
    running: int
    completed: int
    failed: int
```

Then, inside the `DatasetExperimentService` class body, add:

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

    def reconcile_items(self, experiment_id):
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
            latest = attempts[-1] if attempts else None
            active = [
                attempt
                for attempt in attempts
                if attempt.analysis_run_id in runs_by_id
                and runs_by_id[attempt.analysis_run_id].status in {"pending", "running"}
            ]
            if len(active) > 1:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                    "Item has more than one active attempt.", 409,
                )
            if item.status == "queued":
                continue
            latest_run = runs_by_id.get(latest.analysis_run_id) if latest else None
            if item.status == "running":
                if latest is None or latest_run is None:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Running item has no authoritative attempt/run.", 409,
                    )
                if latest_run.recording_id != item.recording_id:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Attempt run does not match the item recording.", 409,
                    )
                if latest_run.status in {"pending", "running"}:
                    continue
                if latest_run.status == "completed":
                    item.status = "completed"
                    item.updated_at = now
                elif latest_run.status in {"failed", "interrupted"}:
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
            elif item.status == "failed":
                if latest is not None and (
                    latest_run is None or latest_run.status not in {"failed", "interrupted"}
                ):
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Failed item disagrees with its authoritative run.", 409,
                    )
            else:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                    "Item has an unknown status.", 409,
                )
        self.session.commit()
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

### Tests — `backend/tests/test_dataset_experiment_coordinator_reconciliation.py`

Re-declare `G3LocalPipeline`, `_seed_dataset`, `_services`, `_experiment`, `_item`,
plus a helper that seeds Attempts/Runs directly:

```python
def _add_attempt_run(session, *, item, attempt_id, attempt_number, run_id, status):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status=status,
        parameters_json={}, error_type=("ANALYSIS_FAILED" if status == "failed" else None),
        error_message=("boom" if status == "failed" else None),
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item.id,
        attempt_number=attempt_number, analysis_run_id=run_id,
    ))
    session.commit()
```

Tests (all real DB Sessions):

- `test_running_item_with_pending_run_stays_running`
- `test_running_item_with_completed_run_becomes_completed`
- `test_running_item_with_failed_run_becomes_failed_and_projects_error`
  (asserts `last_error_type == "ANALYSIS_FAILED"`, `last_error_message == "boom"`)
- `test_running_item_with_interrupted_run_becomes_failed`
- `test_running_item_without_attempt_fails_experiment_closed`
- `test_attempt_referencing_missing_run_fails_experiment_closed`
- `test_multiple_active_attempts_fail_experiment_closed` (two Runs
  `pending`/`running` on one Item)
- `test_completed_item_with_non_completed_run_fails_experiment_closed`
- `test_failed_item_without_attempt_is_allowed`
- `test_select_queued_items_returns_manifest_order_and_limit`

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_coordinator_reconciliation.py`.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_coordinator_reconciliation.py -v
```
Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'reconcile_items'`.
- [ ] **Implement minimal code:** add `ReconcileSummary`, `reconcile_items`,
  `select_queued_items`, `_load_attempts_by_item`, `_load_runs_by_id`.
- [ ] **Run exact GREEN command:** same pytest invocation.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_launch.py -q
```
- [ ] **Commit checkpoint:** `feat: add dataset experiment item reconciliation`

---

## Task 3 — Coordinator core: fence, heartbeat, terminal, bounded scheduling, best-effort (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (add
  `refresh_coordinator_heartbeat`, `mark_experiment_completed_with_failures`,
  `_fail_experiment`, `_mark_item_failed`).
- Create: `backend/app/dataset_experiments/coordinator.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_fencing.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_terminal.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_scheduling.py`

### Service helpers (add)

```python
    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token):
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
        return int(result.rowcount or 0) == 1

    def mark_experiment_completed_with_failures(self, experiment_id, coordinator_token):
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
        return int(result.rowcount or 0) == 1

    def _fail_experiment(self, experiment_id, coordinator_token, error_type, error_message):
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
        return int(result.rowcount or 0) == 1

    def _mark_item_failed(self, item_id, error_type, error_message):
        with self.session.no_autoflush:
            self.session.execute(
                update(DatasetExperimentItemModel)
                .where(
                    DatasetExperimentItemModel.id == item_id,
                    DatasetExperimentItemModel.status.in_(("queued", "running")),
                )
                .values(
                    status="failed", last_error_type=error_type,
                    last_error_message=(error_message or "")[:1000],
                    updated_at=datetime.now(timezone.utc),
                )
                .execution_options(synchronize_session=False)
            )
        self.session.commit()
```

### `coordinator.py` (exact)

```python
from __future__ import annotations

import time
from enum import Enum

from app.core.errors import PlatformError
from app.dataset_experiments.model import DatasetExperimentModel


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
    return exc.code.startswith("DATASET_EXPERIMENT_")


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
            summary = ds.reconcile_items(experiment_id)
            ds.revalidate_frozen_identity(experiment_id)
            if summary.queued == 0 and summary.running == 0:
                if summary.failed > 0:
                    if not ds.mark_experiment_completed_with_failures(experiment_id, coordinator_token):
                        return CoordinatorOutcome.FENCE_LOST
                    return CoordinatorOutcome.COMPLETED_WITH_FAILURES
                if summary.completed > 0:
                    return CoordinatorOutcome.INFERENCE_COMPLETE
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                    "Experiment has no items.", 409,
                )
            free_slots = max(0, experiment.max_concurrency - summary.running)
            if free_slots == 0:
                return CoordinatorOutcome.WAITING
            started = 0
            for item_id in ds.select_queued_items(experiment_id, limit=free_slots):
                try:
                    attempt = ds.start_item_attempt(
                        experiment_id=experiment_id, item_id=item_id,
                        analysis_service=analysis,
                    )
                    ds.launch_item_attempt(
                        experiment_id=experiment_id, item_id=item_id,
                        attempt_id=attempt.id, analysis_service=analysis,
                    )
                    started += 1
                except PlatformError as exc:
                    if is_experiment_level(exc):
                        raise
                    ds._mark_item_failed(item_id, exc.code, exc.message)
            return CoordinatorOutcome.SCHEDULED if started else CoordinatorOutcome.WAITING
        except PlatformError as exc:
            if is_experiment_level(exc):
                ds._fail_experiment(experiment_id, coordinator_token, exc.code, exc.message)
                return CoordinatorOutcome.INVARIANT_FAILED
            raise
        except Exception:
            ds._fail_experiment(
                experiment_id, coordinator_token,
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unexpected coordinator error.",
            )
            return CoordinatorOutcome.INVARIANT_FAILED
```

### Test files

- `test_dataset_experiment_coordinator_fencing.py`:
  `test_matching_token_updates_heartbeat`,
  `test_stale_token_returns_fence_lost_without_writes`,
  `test_non_running_experiment_returns_terminal`.
- `test_dataset_experiment_coordinator_terminal.py`:
  `test_completed_with_failures_sets_status_and_exits`,
  `test_all_success_returns_inference_complete_and_leaves_running`
  (asserts Experiment still `running`, no `DatasetEvaluationModel` row, status
  is neither `evaluating` nor `completed`),
  `test_empty_experiment_fails_closed`.
- `test_dataset_experiment_coordinator_scheduling.py`:
  `test_max_concurrency_one_starts_one`,
  `test_max_concurrency_two_starts_two`,
  `test_existing_running_items_consume_slots`,
  `test_never_oversubscribes`,
  `test_queued_items_started_in_manifest_order`,
  `test_item_level_failure_marks_item_failed_and_continues`,
  `test_experiment_level_failure_stops_scheduling_and_fails_experiment`,
  `test_scheduling_uses_g3a_then_g3b` (asserts an `AnalysisRun` exists and the
  provider launch count is one per started Item),
  `test_no_direct_prepare_run_or_provider_launch` (source scan of
  `coordinator.py`).

All tests build a `DatasetExperimentCoordinator` whose `services_factory`
returns the real test `DatasetExperimentService`/`AnalysisService` on a fresh
Session, using the G3-A local scaffolding. A helper runs exactly one `step`:

```python
def _step_once(client, experiment_id, token, *, max_concurrency=None):
    session_factory = client.app.state.database.session_factory
    def services_factory(session):
        registry = PipelineRegistry([G3LocalPipeline()])
        executor_registry = FakeRegistry({"local_cpu": FakeProvider("local_cpu")})
        ds = DatasetExperimentService(session, registry, None, executor_registry)
        analysis = AnalysisService(session, registry, client.app.state.job_manager,
                                   executor_registry=executor_registry)
        return ds, analysis
    coordinator = DatasetExperimentCoordinator(
        session_factory=session_factory, services_factory=services_factory,
    )
    return coordinator.step(experiment_id, token)
```

### Steps

- [ ] **Write failing tests** (three files above). RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_fencing.py \
  backend/tests/test_dataset_experiment_coordinator_terminal.py \
  backend/tests/test_dataset_experiment_coordinator_scheduling.py -v
```
Expected RED: `ModuleNotFoundError: app.dataset_experiments.coordinator` /
missing service helper attributes.
- [ ] **Implement minimal code:** service helpers + `coordinator.py`.
- [ ] **Run exact GREEN command:** same pytest invocation.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_coordinator_reconciliation.py \
  backend/tests/test_dataset_experiment_start.py \
  backend/tests/test_dataset_experiment_launch.py -q
```
- [ ] **Commit checkpoint:** `feat: add dataset experiment coordinator core`

---

## Task 4 — Worker loop + control-plane wiring (behavior)

**Files:**
- Create: `backend/app/dataset_experiments/wiring.py`
- Create: `backend/app/dataset_experiments/worker.py`
- Create: `backend/tests/test_dataset_experiment_worker.py`

### `wiring.py` (control-plane only)

`build_control_plane_dependencies(settings) -> ControlPlaneDependencies` with
fields `registry, model_release_store, executor_registry, identity_resolver,
orchestrator_commit_resolver, runtime_commit_config, project_root, data_root`.
It reuses `create_pipeline_registry`, `build_local_providers`,
`ModelReleaseStore` + `load_model_release_defaults`, `ExecutionCertificateStore`
+ `load_execution_certificates`, and, when `RemoteProfile.from_env(settings)`
succeeds, `RemoteGpuExecutorProvider` + `CoordinatorJobManager`; `identity_resolver`
defaults to `resolve_remote_recording_identity`, `orchestrator_commit_resolver`
to `resolve_local_orchestrator_commit`, `runtime_commit_config` to the profile
commit (or None). `project_root`/`data_root` come from `settings`. Imports no
torch/model code.

### `worker.py` (exact structure)

```python
import argparse
import time

DEFAULT_POLL_INTERVAL = 1.0


def run_coordinator(experiment_id, coordinator_token, *, settings=None,
                    poll_interval=DEFAULT_POLL_INTERVAL, max_iterations=None):
    from app.core.config import Settings
    from app.db.base import Base, load_domain_models
    from app.db.migrations import run_additive_migrations
    from app.db.session import Database
    from app.dataset_experiments.coordinator import (
        DatasetExperimentCoordinator, EXIT_OUTCOMES,
    )
    from app.dataset_experiments.service import DatasetExperimentService
    from app.dataset_experiments.wiring import build_control_plane_dependencies
    from app.analysis.service import AnalysisService

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
            project_root=deps.project_root, data_root=deps.data_root,
        )
        return ds, analysis

    coordinator = DatasetExperimentCoordinator(
        session_factory=database.session_factory, services_factory=services_factory,
        poll_interval=poll_interval, max_iterations=max_iterations,
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

### Tests — `backend/tests/test_dataset_experiment_worker.py`

- `test_worker_loop_exits_on_terminal_outcome` (monkeypatch the coordinator
  class's `step` to return `EXPERIMENT_TERMINAL`; assert `run_coordinator`
  returns 0 and calls `step` once).
- `test_worker_loop_honors_max_iterations` (`step` returns `WAITING`; with
  `max_iterations=2` and a no-op sleep, returns 0 after two steps).
- `test_coordinator_packages_import_is_torch_free` (fresh subprocess imports
  `app.dataset_experiments.coordinator`, `.job_manager`, `.wiring`, `.worker`
  without `torch`/`ultralytics`).

### Steps

- [ ] Write failing tests; RED command
  `python -m pytest backend/tests/test_dataset_experiment_worker.py -v`.
- [ ] Implement `wiring.py` and `worker.py`.
- [ ] GREEN; focused regressions
  (`test_dataset_experiment_job_manager` behavior via `test_dataset_experiment_start.py`).
- [ ] Commit: `feat: add dataset experiment coordinator worker and wiring`.

---

## Task 5 — Regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_dataset_experiment_coordinator_regression.py`
- No production change.

Guards:

- `DatasetExperiment` and `AnalysisRun` schemas unchanged (exact column sets).
- `coordinator.py` source contains no `prepare_run(`, no `provider.launch(`,
  no `DatasetEvaluation`, no `evaluating`, no `completed` assignment, and no
  coordinator-token rotation.
- `worker.py`/`wiring.py`/`job_manager.py` import no `torch`/`ultralytics`
  (fresh subprocess).
- `remote_execution/*` unchanged (scope diff empty).

Regression commands:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
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
a fresh final pytest summary (no partial-output inference).

Commit: `test: add phase G G3C regression matrix`.

---

## Test Matrix

| Area | Test file |
|---|---|
| start ownership / CAS / spawn | `test_dataset_experiment_start.py` |
| token fence / heartbeat | `test_dataset_experiment_coordinator_fencing.py` |
| reconciliation / invariants | `test_dataset_experiment_coordinator_reconciliation.py` |
| bounded scheduling / best-effort / composition | `test_dataset_experiment_coordinator_scheduling.py` |
| terminal / G5 handoff | `test_dataset_experiment_coordinator_terminal.py` |
| worker loop / import isolation | `test_dataset_experiment_worker.py` |
| regression guards | `test_dataset_experiment_coordinator_regression.py` |
| G1/G2/G3-A/G3-B + remote regressions | existing suites |
| full backend | `pytest backend/tests -q` |

---

## Self-Review

1. G3-C is control-plane only; coordinator/worker/job-manager/wiring import no
   torch/ultralytics/model code.
2. New Item execution composes G3-A then G3-B; no `prepare_run`, no
   `provider.launch` in coordinator.
3. Start ownership is a durable CAS; duplicate starts yield one coordinator.
4. Token fence is durable; stale coordinator cannot heartbeat or schedule.
5. Reconciliation uses latest-Attempt Run authority; never mutates Runs; detects
   impossible ownership fail-closed.
6. Error classification is explicit: `DATASET_EXPERIMENT_*` → experiment-level;
   other `PlatformError` → Item-level; non-`PlatformError` → fail-closed
   experiment-level. No broad `except Exception -> Item.failed`.
7. Per-Item pre-ownership `prepare_run` failure is projected into `Item.failed`
   without fabricating Run/Attempt.
8. Bounded by `max_concurrency`, deterministic `manifest_order`, no
   oversubscription, no global scheduler.
9. Terminal: `completed_with_failures` on failures; all-success returns
   `INFERENCE_COMPLETE`, leaves `running`, creates no evaluation, sets neither
   `evaluating` nor `completed`.
10. No schema/migration change, no `remote_execution/*` change, no G4/G5 behavior.
11. Small transactions; nothing spans I/O, probes, launch, spawn, or sleep.
12. Every task has exact files/interfaces/tests/commands/checkpoints; no
    TODO/TBD/XXX placeholders; no cross-test-module imports.

---

## Out-of-Scope Guardrails During Implementation

- If G3-C appears to need evaluation creation, `evaluating`/`completed`, Retry
  Failed, token rotation, restart recovery, or a new Experiment state, STOP —
  that is G4/G5.
- If a schema/migration change appears required, STOP and report.
- If the all-success handoff cannot be resolved without creating evaluation or a
  new state, STOP and report the exact conflict.
- If the coordinator cannot remain torch-free, STOP.
