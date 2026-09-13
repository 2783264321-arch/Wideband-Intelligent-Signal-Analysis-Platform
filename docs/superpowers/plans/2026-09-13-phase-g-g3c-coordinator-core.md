# Phase G G3-C Coordinator Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G3-C sub-gate):** Complete the approved Phase G **G3 — Orchestrator
Core** by turning the sealed G3-A/G3-B per-Item primitives into normal
dataset-level execution under a durable, generation-fenced coordinator:
`pending -> running` ownership + `coordinator_token`, a fenced single-iteration
coordinator, Item reconciliation from `AnalysisRun` authority, bounded
`max_concurrency` scheduling in deterministic `manifest_order`, best-effort
continuation, and the terminal inference transitions. G3-C is normal
orchestration only; it is not G4 recovery/retry and not G5 evaluation/API.

**Architecture:** A control-plane-only coordinator package
(`backend/app/dataset_experiments/{coordinator,job_manager,worker,wiring}.py`)
plus additive service methods on the sealed `DatasetExperimentService`. The
supplied coordinator generation participates in the **final durable claims**
(Transaction A Item claim, Transaction B launch-intent claim, reconciliation
projections, Item-failure projection) via additive optional
`coordinator_token` parameters — not check-then-act. Existing G3-A/G3-B callers
that omit the token retain their exact sealed behavior. Each coordinator
iteration is one testable `step(...)` that opens its own short-lived Session; a
thin worker loop calls `step` then sleeps a bounded interval. New Item execution
always composes `start_item_attempt` then `launch_item_attempt`. No schema
change; no `remote_execution/*` change; no `prepare_run`/`provider.launch` calls
from the coordinator.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, default engine, no PRAGMAs,
`expire_on_commit=False`), subprocess job managers, pytest 9. No GPU, no SSH, no
torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
(§5 state machines, §9 coordinator loop, §10 bounded concurrency, §12 restart
recovery, §14.1/§14.2 error semantics, §15 invariants, §20 G3).

**Sealed dependencies:** G3-A / G3-B plans; G2 `AnalysisService.prepare_run` /
`launch_prepared_run`.

**Base:** `feature/m9-2-implementation @ 6eb2682c8e09b4a5d930af1719e7bb4d8a71205a`.

---

## Phase G Gate Structure (authoritative §20)

G3-A + G3-B + **G3-C (this plan)** together complete **G3 — Orchestrator Core**.
G4 owns Retry Failed, local ambiguity recovery, coordinator-token
rotation/restart, duplicate-active-Attempt recovery, remote restart recovery.
G5 owns DatasetEvaluation, `running -> evaluating`, evaluation reconciliation,
REST.

---

## Process / Job-Manager Pattern Audit (verified)

- Job managers spawn `[sys.executable, "-m", "<module>", <id>]` (remote adds
  `--coordinator-token`) with `cwd=backend_root`, `WSP_*` env, DEVNULL stdio,
  `shell=False`, returning `process.pid` (`analysis/job_manager.py:16`,
  `benchmarks/job_manager.py:16`, `remote_execution/coordinator_job_manager.py:26`).
- Workers open their own engine/session (`benchmarks/worker.py:109`):
  `Database(url)`, `load_domain_models()`, `Base.metadata.create_all`,
  `run_additive_migrations`, then `session_factory()`.
- Parent PID persistence convention: `BenchmarkService.start_evaluation`
  (`benchmarks/service.py:606`) marks the entity `failed` and raises on spawn
  failure; otherwise persists `worker_pid`. G3-C mirrors this, adding a
  `status == "running"` guard so a fast terminal coordinator is not overwritten.
- Coordinator fencing precedent: `remote_execution/coordinator.py:97`
  re-reads the token in the same session before side effects.
- No existing DatasetExperiment job manager/worker/coordinator exists; G3-C
  follows the conventions above.

---

## Global Constraints

1. **New Item execution composes G3-A then G3-B only**
   (`start_item_attempt` then `launch_item_attempt`). No `prepare_run`, no
   `provider.launch` in the coordinator.
2. **Generation fencing is part of the durable claims**, not a prior SELECT:
   Transaction A Item claim, Transaction B launch-intent claim, reconciliation
   projections, and Item-failure projection all include the Experiment
   generation (`id`, `status == "running"`, `coordinator_token`) in the same
   atomic write when a token is supplied.
3. **`coordinator_token=None` preserves sealed G3-A/G3-B behavior exactly.**
4. **Reconciliation is all-or-nothing** and validates every Attempt.
5. **One normal coordinator per pending Experiment** via a durable CAS.
6. **No evaluation/new state.** Never create `DatasetEvaluation`, never set
   `evaluating` or `completed`.
7. **No schema/migration change; no `remote_execution/*` change.**
8. **Control-plane only** (no torch/ultralytics/model code).
9. **Small transactions**; nothing spans Recording I/O, probes, provider launch,
   subprocess spawn, or sleep.

---

## Proposed G3-C Files / Interfaces

New:
- `backend/app/dataset_experiments/coordinator.py` — `CoordinatorOutcome`,
  `EXIT_OUTCOMES`, `is_experiment_level`, `DatasetExperimentCoordinator`.
- `backend/app/dataset_experiments/job_manager.py` — `DatasetExperimentJobManager`.
- `backend/app/dataset_experiments/worker.py` — `run_coordinator`, `main`.
- `backend/app/dataset_experiments/wiring.py` — `build_control_plane_dependencies`.

Modified:
- `backend/app/dataset_experiments/service.py` — generation-fenced additions to
  `start_item_attempt`, `launch_item_attempt`, `_claim_queued_item`,
  `_claim_launch_intent`; add `start_experiment`, `ReconcileSummary`,
  `reconcile_items`, `select_queued_items`, `refresh_coordinator_heartbeat`,
  `mark_experiment_completed_with_failures`, `_fail_experiment`,
  `_mark_item_failed`, `_require_experiment_generation`,
  `_load_attempts_by_item`, `_load_runs_by_id`.

Exact interfaces:

```python
class CoordinatorOutcome(str, Enum):
    SCHEDULED = "scheduled"
    WAITING = "waiting"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    INFERENCE_COMPLETE = "inference_complete"
    EXPERIMENT_TERMINAL = "experiment_terminal"
    FENCE_LOST = "fence_lost"
    INVARIANT_FAILED = "invariant_failed"

EXIT_OUTCOMES = frozenset({... the five terminal outcomes ...})

def is_experiment_level(exc: PlatformError) -> bool: ...

class DatasetExperimentCoordinator:
    def __init__(self, *, session_factory, services_factory, sleep_fn=time.sleep,
                 poll_interval=1.0, max_iterations=None): ...
    def step(self, experiment_id: str, coordinator_token: str) -> CoordinatorOutcome: ...

class DatasetExperimentJobManager:
    DEFAULT_COORDINATOR_MODULE = "app.dataset_experiments.worker"
    def __init__(self, settings): ...
    def start(self, experiment_id: str, coordinator_token: str) -> int: ...

def build_control_plane_dependencies(settings): ...
def run_coordinator(experiment_id, coordinator_token, *, settings=None,
                    poll_interval=1.0, max_iterations=None) -> int: ...
```

Additive service signatures (existing callers remain valid):

```python
def start_item_attempt(self, *, experiment_id, item_id, analysis_service,
                       coordinator_token: str | None = None): ...
def launch_item_attempt(self, *, experiment_id, item_id, attempt_id, analysis_service,
                        coordinator_token: str | None = None): ...
def _claim_queued_item(self, *, item_id, experiment_id,
                       coordinator_token: str | None = None) -> int: ...
def _claim_launch_intent(self, *, attempt_id, item_id, requested_at,
                         experiment_id=None, coordinator_token: str | None = None) -> int: ...
def reconcile_items(self, experiment_id, coordinator_token: str | None = None) -> ReconcileSummary: ...
def select_queued_items(self, experiment_id, limit) -> list[str]: ...
def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token) -> bool: ...
def mark_experiment_completed_with_failures(self, experiment_id, coordinator_token) -> bool: ...
def _fail_experiment(self, experiment_id, coordinator_token, error_type, error_message) -> bool: ...
def _mark_item_failed(self, item_id, error_type, error_message, *,
                      experiment_id, coordinator_token: str | None = None) -> bool: ...
def _require_experiment_generation(self, experiment_id, coordinator_token) -> None: ...
def start_experiment(self, experiment_id, job_manager) -> DatasetExperimentModel: ...
```

---

## Corrected Generation Fencing Design

`DATASET_EXPERIMENT_FENCE_LOST` (409) is a new code: the supplied generation no
longer matches the persisted Experiment (`status != "running"` or
`coordinator_token` differs) at a durable claim. The coordinator maps it to
`FENCE_LOST` (rollback; no Item mutation; never fails the newer generation).

`_require_experiment_generation(experiment_id, token)` runs a fresh core SELECT
(same Session/transaction) after a failed fenced claim to distinguish fence loss
from a genuine item-state invariant.

### Transaction A generation fence (no G3-A behavior change)

`start_item_attempt(..., coordinator_token=None)` → `_claim_queued_item(...,
coordinator_token=...)`. When a token is supplied, the Item `queued -> running`
CAS also requires, in the same statement:

```sql
AND EXISTS (SELECT 1 FROM dataset_experiments e
            WHERE e.id = :experiment_id
              AND e.status = 'running'
              AND e.coordinator_token = :coordinator_token)
```

If token rotation occurs during `prepare_run`'s external I/O, the final CAS
matches 0 rows; `_require_experiment_generation` detects the mismatch → rollback
Transaction A (staged Run discarded, no Attempt, Item stays `queued`), raise
`DATASET_EXPERIMENT_FENCE_LOST`. With `coordinator_token=None` the statement is
byte-identical to the sealed G3-A claim.

### Transaction B generation fence (no G3-B behavior change)

`launch_item_attempt(..., coordinator_token=None)` → `_claim_launch_intent(...,
experiment_id=..., coordinator_token=...)`. When a token is supplied, the
`launch_requested_at IS NULL -> now` CAS also requires the same Experiment
`EXISTS` generation condition. If rotation occurs after Transaction A but before
Transaction B: the CAS loses, the coordinator raises
`DATASET_EXPERIMENT_FENCE_LOST`, and performs zero physical launch; the durable
Transaction A ownership remains (G4 owns launch recovery). With
`coordinator_token=None` the claim is byte-identical to sealed G3-B.

Once Transaction B has durably committed under a valid generation, existing G3-B
launch-ambiguity semantics remain authoritative. No long transaction spans
prepare/probe/provider launch.

### Generation fence for coordinator-owned Item writes

- `_mark_item_failed(..., experiment_id, coordinator_token=...)`: the Item
  `queued|running -> failed` UPDATE carries the same Experiment `EXISTS`
  generation condition. Rowcount 0 with a stale generation →
  `DATASET_EXPERIMENT_FENCE_LOST` (no Item mutation); rowcount 0 with a valid
  generation (Item already terminal) is an idempotent no-op.
- `reconcile_items(experiment_id, coordinator_token=...)`: acquires the
  generation write fence **inside the same transaction** via an idempotent
  token-guarded Experiment UPDATE (`SET status='running' WHERE id AND
  status='running' AND coordinator_token=:token`), which takes SQLite's write
  lock and serializes against G4 token rotation; if it matches 0 rows → raise
  `DATASET_EXPERIMENT_FENCE_LOST` (nothing committed). The Item projections then
  commit in that same held transaction, so a stale generation cannot commit Item
  mutations after rotation.

Test A/B/C/D coverage is in Task 3 and Task 6.

### SQLite contention

A genuine simultaneous writer race may surface `SQLITE_BUSY`/`OperationalError`.
G3-C fails closed: rollback, zero side effects, no automatic retry, no
`BEGIN IMMEDIATE`, no WAL/busy-timeout PRAGMAs.

---

## Initial Coordinator Durable Ownership Ordering

`start_experiment(experiment_id, job_manager)`:

1. Load Experiment; missing → `DATASET_EXPERIMENT_NOT_FOUND` (404).
2. In an explicit `try/except: rollback; raise`, CAS
   `UPDATE ... SET status='running', coordinator_token=:token, started_at=:now,
   heartbeat_at=:now, worker_pid=NULL, error_type=NULL, error_message=NULL
   WHERE id=:id AND status='pending'`; `rowcount != 1` → rollback +
   `DATASET_EXPERIMENT_INVALID_TRANSITION` (409); commit.
3. Spawn `job_manager.start(experiment_id, token)` (no DB transaction held).
   Spawn failure → token-guarded `SET status='failed', worker_pid=NULL,
   error_type='DATASET_EXPERIMENT_ORCHESTRATION_FAILED',
   error_message=str(exc)[:1000], completed_at=:now`; commit; raise
   `PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED", ...)`.
4. Persist `worker_pid` with `UPDATE ... WHERE id=:id AND
   coordinator_token=:token AND status='running'` (terminal guard); commit; return
   refreshed Experiment.

Crash windows:

| Window | Durable state | Owner |
|---|---|---|
| Before CAS commit | `pending` | G3-C (single CAS winner) |
| After CAS, before spawn | `running` + token, no `worker_pid`, no process | G4 recovery |
| After spawn, before `worker_pid` commit | coordinator running, `worker_pid` NULL | G4 |
| Spawn raises | `failed` + `ORCHESTRATION_FAILED` | G3-C |
| Fast terminal before `worker_pid` commit | terminal status preserved; `worker_pid` not overwritten | G3-C guard |

---

## Coordinator-Token Fence

`step` order (already accepted): load Experiment → token mismatch →
`FENCE_LOST`; `status != "running"` → `EXPERIMENT_TERMINAL`; then token-guarded
heartbeat (`rowcount != 1` → `FENCE_LOST`). Do not regress to
heartbeat-before-status.

A stale coordinator cannot heartbeat, cannot reconcile, cannot mark Items,
cannot claim Items/launch intent, and cannot schedule.

---

## Single-Iteration Architecture

`step(experiment_id, coordinator_token)` opens one short-lived Session, builds
`(DatasetExperimentService, AnalysisService)` via `services_factory(session)`,
runs the deterministic order, closes the Session, returns an outcome. It never
sleeps.

```
1. fence: load experiment, verify token
2. if experiment.status != "running": return EXPERIMENT_TERMINAL
3. heartbeat (token-guarded)
4. reconcile Items (generation-fenced)
5. revalidate_frozen_identity (read-only)
6. terminal inference conditions
7. if running: free_slots, select queued by manifest_order, start via G3-A + G3-B
8. return outcome
```

The worker loop performs `step -> sleep -> repeat` and exits on `EXIT_OUTCOMES`.

---

## Reconciliation Algorithm (strict)

`reconcile_items(experiment_id, coordinator_token=None)`:

1. Acquire the in-transaction generation write fence when a token is supplied
   (idempotent Experiment UPDATE; `rowcount != 1` → `FENCE_LOST`).
2. Load Items (manifest order), Attempts (by item/attempt_number), Runs
   (batched). Empty Items → invariant failure.
3. **Validate EVERY Attempt** (including non-latest): referenced Run exists;
   `Run.recording_id == Item.recording_id`; `Run.pipeline_id/pipeline_version/
   executor` equal the frozen Experiment identity. Any violation → invariant
   failure.
4. `latest` = greatest `attempt_number`; `active_attempts` = Attempts whose Run
   status is `pending`/`running`.
5. Enforce per Item status:

| Item status | Required | Violation |
|---|---|---|
| `queued` | `active_attempts == 0` (terminal historical Attempts allowed for G4 Retry Failed) | invariant |
| `running`, latest Run `pending`/`running` | `active_attempts == [latest]` | invariant |
| `running`, latest Run terminal | `active_attempts == 0`; then project `completed` or `failed` (+ bounded error projection) | invariant |
| `completed` | latest Run `completed` AND `active_attempts == 0` | invariant |
| `failed` | (no Attempt) OR (latest Run `failed`/`interrupted`), AND `active_attempts == 0` | invariant |
| other | unknown status | invariant |

6. Commit Item projections; on any exception, rollback and re-raise (all-or-nothing).
7. Return `ReconcileSummary(expected, queued, running, completed, failed)`.

No state is repaired. `AnalysisRun` rows are never mutated.

---

## Error Classification (explicit)

```python
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
    "REMOTE_IMPLEMENTATION_MISMATCH",
    "PIPELINE_ASSET_MISMATCH",
})

def is_experiment_level(exc: PlatformError) -> bool:
    if exc.code.startswith("DATASET_EXPERIMENT_"):
        return True
    if exc.code in EXPERIMENT_LEVEL_CODES:
        return True
    if exc.code in ITEM_LEVEL_CODES:
        return False
    return True  # unknown -> fail closed (experiment-level)
```

`DATASET_EXPERIMENT_FENCE_LOST` is checked before `is_experiment_level` and maps
to `FENCE_LOST`.

Rationale: for a frozen, revalidated Experiment, `ANALYSIS_RUN_NOT_LAUNCHABLE`,
plugin/release/certificate/pipeline identity codes, and missing-Recording
indicate global identity drift or orchestration corruption (stop), while
Recording-specific input/source/provider failures are Item-level. Unknown codes
fail closed.

---

## Bounded-Concurrency Algorithm

After reconciliation: `active = summary.running`; `free_slots = max(0,
max_concurrency - active)`; `select_queued_items(experiment_id, limit=free_slots)`
ordered by `manifest_order ASC`; start at most `len(selected)`. No
oversubscription; no global scheduler.

---

## Best-Effort Scheduling Composition

```python
for item_id in ds.select_queued_items(experiment_id, limit=free_slots):
    try:
        attempt = ds.start_item_attempt(experiment_id=..., item_id=item_id,
                                        analysis_service=analysis,
                                        coordinator_token=coordinator_token)
        ds.launch_item_attempt(experiment_id=..., item_id=item_id,
                               attempt_id=attempt.id, analysis_service=analysis,
                               coordinator_token=coordinator_token)
        started += 1
    except PlatformError as exc:
        if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
            raise
        if is_experiment_level(exc):
            raise
        ds._mark_item_failed(item_id, exc.code, exc.message,
                             experiment_id=experiment_id,
                             coordinator_token=coordinator_token)
```

A per-Item scheduling failure (e.g. a Recording-specific `prepare_run` failure
that G3-A rolled back) is projected into `Item.failed` via `_mark_item_failed`
with no fabricated Run/Attempt. Experiment-level failures stop scheduling.

---

## Terminal Inference Conditions

When `queued == 0 and running == 0`:

- `failed > 0` → `mark_experiment_completed_with_failures` (token-guarded) →
  `COMPLETED_WITH_FAILURES`.
- `failed == 0 and completed > 0` → `INFERENCE_COMPLETE` with **no Experiment
  mutation** (Experiment stays `running`; no `DatasetEvaluation`; no `evaluating`;
  no `completed`).
- all zero (empty) → invariant failure.

**All-success G5 handoff (preserved):** `INFERENCE_COMPLETE` is the explicit
seam; the worker exits on it. G5 replaces this branch (create/link
`DatasetEvaluation`, set `running -> evaluating`, continue into evaluation
reconciliation). G4 restarting a `running` inference-complete Experiment
recomputes `INFERENCE_COMPLETE` idempotently and exits. No new persisted state.

---

## G3-C vs G4 vs G5 Boundary

- **G3-C:** generation-fenced start ownership + token; fenced step loop;
  reconciliation; bounded scheduling; best-effort; `completed_with_failures`;
  all-success handoff seam.
- **G4:** Retry Failed; failed Item requeue; local ambiguity recovery; remote
  restart recovery; coordinator-token rotation; stale-coordinator recovery;
  duplicate-active-Attempt recovery; startup DatasetExperiment recovery.
- **G5:** DatasetEvaluation; `running -> evaluating`; evaluation reconciliation;
  Retry Evaluation; REST.
- G3-C never creates evaluation, never sets `evaluating`/`completed`, never
  rotates the token, never requeues failed Items, never recovers a crashed
  coordinator.

---

## Transaction Boundaries

- `step`: one short-lived Session; closed before sleep.
- `start_experiment`: one commit for the CAS, spawn (no transaction held), one
  commit for `worker_pid`.
- Each Item: G3-A Transaction A commit, G3-B Transaction B + result commit.
- `reconcile_items`, `_mark_item_failed`, `mark_...`, `_fail_experiment`: each
  owns a small transaction with explicit rollback.
- No transaction spans Recording I/O, probes, provider launch, spawn, or sleep.

---

# TASK 1 — Generation-fenced primitives (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`
- Create: `backend/tests/test_dataset_experiment_generation_fence.py`

### Exact production changes

Add `exists` to the SQLAlchemy imports and add
`DATASET_EXPERIMENT_FENCE_LOST` handling. `_require_experiment_generation`:

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
```

`_claim_queued_item` / `_claim_launch_intent` add the optional generation
`EXISTS` condition exactly as specified in "Corrected Generation Fencing Design".
`start_item_attempt`/`launch_item_attempt` gain
`coordinator_token: str | None = None`; on claim `rowcount != 1` they call
`_require_experiment_generation` (only when a token was supplied) then raise the
existing `DATASET_EXPERIMENT_INVARIANT_VIOLATION`.

### Tests — `backend/tests/test_dataset_experiment_generation_fence.py`

Re-declare G3-A local helpers and build ownership with a token:

```python
def _owned_attempt_with_token(client, token):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        analysis_service=analysis, coordinator_token=token,
    )
    return session, ds, analysis, provider, experiment, item, attempt
```

Tests:

- `test_start_item_attempt_none_token_matches_sealed_behavior` — token omitted,
  Item claim succeeds (existing G3-A behavior unchanged).
- `test_start_item_attempt_with_matching_token_claims` — generation matches,
  ownership committed.
- `test_start_item_attempt_fence_lost_before_transaction_a` — Experiment token
  rotated before the call → `DATASET_EXPERIMENT_FENCE_LOST`; fresh DB: no Run,
  no Attempt, Item `queued`, zero launches.
- `test_launch_item_attempt_fence_lost_before_transaction_b` — Transaction A
  committed under token T, rotate to T2, call `launch_item_attempt` with T →
  `DATASET_EXPERIMENT_FENCE_LOST`; fresh DB: Run `pending`, `worker_pid NULL`,
  `launch_requested_at NULL`, zero launches; Run+Attempt+Item running remain.
- `test_mark_item_failed_fence_lost` — rotate token, call
  `_mark_item_failed(..., coordinator_token=T)` → `FENCE_LOST`, Item unchanged.
- `test_reconcile_fence_lost_no_projection` — Item would project to `completed`,
  rotate token before `reconcile_items(token=T)` → `FENCE_LOST`, Item stays
  `running`.
- `test_generation_fence_none_token_is_unfenced_for_sealed_callers` — with
  `coordinator_token=None`, `_claim_queued_item`/`_claim_launch_intent` succeed
  regardless of Experiment token.

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_generation_fence.py`.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_generation_fence.py -v
```
Expected RED: missing `coordinator_token` parameter / missing fence behavior.
- [ ] **Implement minimal code** as specified.
- [ ] **Run exact GREEN command:** same invocation.
- [ ] **Focused regressions (sealed callers unchanged):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_attempt_concurrency.py \
  backend/tests/test_dataset_experiment_launch.py \
  backend/tests/test_dataset_experiment_launch_concurrency.py -q
```
- [ ] **Commit checkpoint:** `feat: add coordinator generation fencing to experiment primitives`

---

# TASK 2 — Item reconciliation + queued selection (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_reconciliation.py`

Implement `ReconcileSummary`, `reconcile_items` (strict rules + in-transaction
generation fence + try/commit/except rollback), `select_queued_items`,
`_load_attempts_by_item`, `_load_runs_by_id` exactly as specified.

Tests (real DB Sessions): all strict-invariant cases plus projections:

- `test_queued_item_with_active_attempt_fails_closed` (pending and running Runs)
- `test_active_attempt_must_be_latest`
- `test_terminal_item_with_hidden_older_active_attempt_fails_closed`
- `test_historical_attempt_referencing_missing_run_fails_closed`
- `test_historical_attempt_identity_mismatch_fails_closed`
- `test_queued_item_with_terminal_historical_attempts_is_allowed`
- `test_running_item_with_pending_run_stays_running`
- `test_running_item_with_completed_run_becomes_completed`
- `test_running_item_with_failed_run_becomes_failed_and_projects_error`
- `test_running_item_with_interrupted_run_becomes_failed`
- `test_completed_item_requires_completed_latest_run`
- `test_failed_item_without_attempt_is_allowed`
- `test_reconciliation_is_all_or_nothing` (Item1 would become `completed`,
  Item2 corrupt → invariant; fresh DB: Item1 still `running`, no partial commit)
- `test_reconcile_with_stale_token_fence_lost_no_projection`
- `test_select_queued_items_manifest_order_and_limit`

### Steps

RED → implement → GREEN → focused regressions
(`test_dataset_experiment_generation_fence.py`,
`test_dataset_experiment_attempt.py`). Commit:
`feat: add dataset experiment item reconciliation`.

---

# TASK 3 — Coordinator core: fence, heartbeat, terminal, scheduling, best-effort (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (add
  `start_experiment`, `refresh_coordinator_heartbeat`,
  `mark_experiment_completed_with_failures`, `_fail_experiment`).
- Create: `backend/app/dataset_experiments/job_manager.py`
- Create: `backend/app/dataset_experiments/coordinator.py`
- Create: `backend/tests/test_dataset_experiment_start.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_fencing.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_terminal.py`
- Create: `backend/tests/test_dataset_experiment_coordinator_scheduling.py`

`start_experiment` follows "Initial Coordinator Durable Ownership Ordering"
(CAS in explicit try/except rollback; spawn; token+status guarded `worker_pid`).
`job_manager.py` mirrors the existing job managers.
`coordinator.py` implements the accepted `step` order and the classification /
FENCE_LOST mapping:

```python
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

Start tests (`test_dataset_experiment_start.py`):

- `test_start_experiment_pending_to_running_token_before_spawn`
- `test_start_experiment_real_two_session_stale_pending_cas` — Session A loads
  `pending`; Session B wins the real CAS and commits; Session A calls
  `start_experiment` → `DATASET_EXPERIMENT_INVALID_TRANSITION`; zero spawns;
  winner's token/status intact.
- `test_start_experiment_spawn_failure_marks_failed`
- `test_start_experiment_commit_failure_fails_closed` — monkeypatched
  `session.commit` raises → rollback, zero spawn.
- `test_start_experiment_does_not_overwrite_terminal_worker_pid` — job manager
  marks the Experiment `completed_with_failures` during `start`; parent's
  `worker_pid` update must not change the terminal status.
- `test_start_experiment_rejects_missing_and_non_pending`

Fencing tests (`test_dataset_experiment_coordinator_fencing.py`), real DB
Sessions:

- `test_matching_token_updates_heartbeat`
- `test_stale_token_returns_fence_lost_without_writes` (no heartbeat, no Item
  mutation, no scheduling)
- `test_non_running_experiment_returns_terminal` (checked before heartbeat)
- `test_rotation_before_transaction_a_creates_no_run_or_attempt` (case A)
- `test_rotation_before_transaction_b_blocks_launch_intent` (case B; Transaction
  A ownership remains durable, `launch_requested_at` NULL, zero launches)
- `test_rotation_before_reconciliation_commit_blocks_projection` (case C)
- `test_rotation_before_mark_item_failed_blocks_mutation` (case D)

Terminal tests (`test_dataset_experiment_coordinator_terminal.py`):

- `test_completed_with_failures_sets_status_and_exits`
- `test_all_success_returns_inference_complete_and_leaves_running` (Experiment
  still `running`; no `DatasetEvaluation`; status neither `evaluating` nor
  `completed`)
- `test_empty_experiment_fails_closed`

Scheduling + classification tests
(`test_dataset_experiment_coordinator_scheduling.py`), using the real G3-A/G3-B
primitives and a shared one-step helper:

- `test_max_concurrency_one_starts_one`
- `test_max_concurrency_two_starts_two`
- `test_existing_running_items_consume_slots`
- `test_never_oversubscribes`
- `test_queued_items_started_in_manifest_order`
- `test_item_level_failure_marks_item_failed_and_continues` (per-Item
  `EXECUTION_CAPABILITY_UNAVAILABLE` or `INPUT_INCOMPATIBLE`; the next queued Item
  still starts)
- `test_global_identity_code_stops_scheduling_and_fails_experiment`
  (`MODEL_RELEASE_MISMATCH` / `EXECUTION_NOT_CERTIFIED` via a per-Item injected
  `PlatformError` → Experiment `failed`, no further Items started)
- `test_impossible_launch_state_stops_and_fails_experiment`
  (`ANALYSIS_RUN_NOT_LAUNCHABLE` injected → Experiment `failed`, not Item-only)
- `test_unknown_platform_error_fails_closed` (code not in either set →
  experiment-level)
- `test_scheduling_uses_g3a_then_g3b` (Run created; one provider launch per Item)
- `test_no_direct_prepare_run_or_provider_launch` (source scan of
  `coordinator.py` contains neither `prepare_run(` nor `provider.launch(`)

Shared one-step helper (real test services on a fresh Session):

```python
def _step_once(client, experiment_id, token):
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

RED → implement → GREEN → focused regressions
(`test_dataset_experiment_generation_fence.py`,
`test_dataset_experiment_coordinator_reconciliation.py`,
`test_dataset_experiment_launch.py`). Commit:
`feat: add dataset experiment coordinator core`.

---

# TASK 4 — Worker loop + control-plane wiring (behavior)

**Files:**
- Create: `backend/app/dataset_experiments/wiring.py`
- Create: `backend/app/dataset_experiments/worker.py`
- Create: `backend/tests/test_dataset_experiment_worker.py`

`wiring.build_control_plane_dependencies(settings)` mirrors `main.py`'s
control-plane construction using public builders; no torch/model imports.

Worker loop as accepted (`step -> sleep -> repeat`; exits on `EXIT_OUTCOMES`;
`max_iterations` support).

Tests:

- `test_worker_loop_exits_on_terminal_outcome`
- `test_worker_loop_honors_max_iterations`
- `test_build_control_plane_dependencies_executes_torch_free` — fresh subprocess
  calls `build_control_plane_dependencies(settings)` under CPU/no-remote settings
  and asserts `torch`/`ultralytics` not in `sys.modules`.
- `test_wiring_dependencies_construct_coordinator_services` — the returned deps
  build a `DatasetExperimentService`/`AnalysisService` pair on a Session without
  error.
- `test_coordinator_packages_import_is_torch_free` (import-only, retained).

### Steps

RED → implement → GREEN → focused regressions
(`test_dataset_experiment_start.py`). Commit:
`feat: add dataset experiment coordinator worker and wiring`.

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

## Test Matrix

| Area | Test file |
|---|---|
| generation fence on primitives | `test_dataset_experiment_generation_fence.py` |
| start ownership / real stale CAS / worker_pid guard | `test_dataset_experiment_start.py` |
| reconciliation strict invariants / atomicity | `test_dataset_experiment_coordinator_reconciliation.py` |
| token fence / heartbeat | `test_dataset_experiment_coordinator_fencing.py` |
| bounded scheduling / best-effort | `test_dataset_experiment_coordinator_scheduling.py` |
| terminal / G5 handoff | `test_dataset_experiment_coordinator_terminal.py` |
| worker loop / executed wiring torch-free | `test_dataset_experiment_worker.py` |
| regression guards | `test_dataset_experiment_coordinator_regression.py` |
| G1/G2/G3-A/G3-B + remote | existing suites |
| full backend | `pytest backend/tests -q` |

---

## Self-Review

1. Generation fencing is part of the durable claims (A, B, reconciliation,
   Item-failure), not a prior SELECT. PASS
2. `coordinator_token=None` preserves sealed G3-A/G3-B behavior byte-for-byte. PASS
3. Reconciliation enforces `active_attempts == 0` for queued/terminal,
   `active_attempts == [latest]` for active running, validates every Attempt. PASS
4. Reconciliation is all-or-nothing with explicit rollback. PASS
5. `start_experiment` CAS is explicit try/except rollback; real stale-session
   test; SQLite BUSY fail-closed. PASS
6. Error classification table with explicit sets and fail-closed unknowns. PASS
7. Wiring test executes the builder and proves torch/ultralytics-free. PASS
8. Accepted parts preserved: INFERENCE_COMPLETE / `running` / no evaluation /
   no `evaluating` / no `completed` / worker exits / `completed_with_failures` /
   manifest-order / max_concurrency / G3-A→G3-B / control-plane only /
   short-lived Session / no sleep in step / no G4/G5 / no schema /
   no `remote_execution/*`. PASS
9. Coordinator step order `token → status → heartbeat` unchanged. PASS
10. Every task has exact files/interfaces/tests/commands/checkpoints; no
    TODO/TBD/XXX placeholders; no cross-test imports. PASS
- No STOP condition: additive fencing is achievable without breaking sealed
  G3-A/G3-B or schema changes.

---

## Out-of-Scope Guardrails During Implementation

- If generation fencing cannot be added without breaking sealed G3-A/G3-B
  behavior or requiring schema changes, STOP and report the blocker.
- If reconciliation cannot be made all-or-nothing, STOP.
- If G3-C appears to need evaluation, `evaluating`/`completed`, Retry Failed,
  token rotation, restart recovery, or a new state, STOP — G4/G5.
- If the coordinator cannot remain torch-free, STOP.
