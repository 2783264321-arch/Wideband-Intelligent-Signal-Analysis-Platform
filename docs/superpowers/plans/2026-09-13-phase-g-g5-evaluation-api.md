# Phase G G5 — Evaluation Integration + API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G5 sub-gate):** Close the Phase G `run -> evaluate -> completed` loop and
expose the V1 DatasetExperiment REST surface. G5 owns full-success formal
`DatasetEvaluation` creation with exact frozen membership, `running -> evaluating`,
evaluating-state coordinator reconciliation, evaluation restart/recovery, explicit
Retry Evaluation, and the DatasetExperiment endpoints. G5 does not touch frontend,
metrics science, or G6 real acceptance.

**Architecture:** Add transaction-neutral `prepare_*` seams to
`DatasetBenchmarkService` (mirroring the sealed G2 `prepare_run` pattern) so the
coordinator can create the evaluation and link it to the Experiment in ONE
commit. Add an `evaluating` branch to the sealed G3-C coordinator, make G4
recovery status-aware for `evaluating` while skipping inference repair, and add
`backend/app/dataset_experiments/router.py` over existing service methods.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, no PRAGMAs, `expire_on_commit=False`),
FastAPI TestClient, subprocess job managers, pytest 9. No GPU, no SSH, no torch.

**Spec:**
`docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
§5, §9, §12, §13, §14.3, §15, §16, §18.3, §18.5, §20.

**Base:** `feature/m9-2-implementation @ 45565aec11298f7dc1112af49507ad5ccbeb780c`.

---

## Global Constraints

1. Formal `DatasetEvaluation` is created only after 100% inference success
   (`completed == expected`, `failed == 0`, `queued == 0`, `running == 0`).
2. Automatic evaluation cannot be duplicated: evaluation creation and Experiment
   link share one commit; `dataset_evaluation_id` is the durable marker.
3. Benchmark metrics remain the existing authority; G5 reimplements no metrics.
4. Evaluating never schedules inference and creates ZERO `AnalysisRun`s.
5. Retry Evaluation creates zero Attempts and zero `AnalysisRun`s and mutates no
   `DetectionResult`.
6. Evaluating restart never re-enters inference recovery.
7. Completed evaluation closes the Experiment in the SAME coordinator iteration.
8. No schema/model/migration change. If one proves necessary: STOP.
9. No `remote_execution/*` change; no frontend; no new metrics/science.
10. Every durable transition has one explicit commit boundary; no transaction
    spans benchmark/coordinator subprocess spawn, sleep, or metric computation.
11. SQLAlchemy autobegin is closed before any subprocess spawn (as sealed in G4).
12. G4 Retry Failed, cutoffs, generation fencing, local A→B recovery, ambiguity
    fail-closed, and remote delegation are preserved exactly.

---

## Existing Benchmark Audit

### `DatasetBenchmarkService` transaction behavior (confirmed)

`backend/app/benchmarks/service.py`:

- `create_evaluation(...)` (`496-614`) builds the frozen manifest, validates
  membership, constructs `DatasetEvaluationModel` + `DatasetEvaluationItemModel`
  rows, then **`self.session.commit()`** (`612`) and `refresh` (`613`). It is
  NOT transaction-neutral.
- `start_evaluation(evaluation_id, job_manager)` (`615-636`) requires
  `status == "pending"`; calls `job_manager.start(evaluation.id)` (`622`) BEFORE
  persisting the PID. On spawn exception it sets `status="failed"`,
  `error_type="BENCHMARK_FAILED"`, `completed_at=now`, commits (`624-628`).
  On success it sets `worker_pid` and commits (`633-634`). The subprocess worker
  owns the `pending -> running` transition.
- `retry_evaluation(evaluation_id)` (`639-655`) requires `failed`/`interrupted`,
  resets `status="pending"` and clears metrics/error/progress/`worker_pid`, then
  **`self.session.commit()`** (`653`). It does NOT spawn.
- `mark_stale_running_evaluations_interrupted(session)` (`41-54`) sets every
  `running` evaluation to `interrupted` + `BENCHMARK_INTERRUPTED` and commits.
- `get_evaluation` (`658-662`), `list_evaluations` (`665-668`),
  `list_items` (`671-693`), `compare_evaluations` (`696-748`).

### Benchmark worker (`backend/app/benchmarks/worker.py`)

`execute_benchmark` (`109-209`): first action sets `status="running"`,
`started_at=now`, clears errors, commits (`122-127`); then loads, computes,
persists metrics, sets `status="completed"` + `completed_at`, commits
(`188-191`). On any exception it sets `status="failed"` with the error and clears
metrics, commits (`192-208`).

### Benchmark router (`backend/app/benchmarks/router.py`)

Manual endpoints exist (`prepare`, `resolve-runs`, create/list/get/items,
`/{id}/run`, `/{id}/retry`, `compare`, imported batches). Manual create uses
`create_evaluation` (self-committing). Manual `run` uses `start_evaluation`.
Router pattern: `with request.app.state.database.session_factory() as session:`
and `app.state.benchmark_job_manager`.

### Benchmark model (`backend/app/benchmarks/model.py`)

`DatasetEvaluationModel` has `dataset_evaluation_id`-equivalent `id`, `status`,
`worker_pid`, `error_type/message`, `started_at/completed_at`, metrics JSON,
`recording_manifest_hash`, `evaluation_protocol`, `protocol_config_json`.
`DatasetEvaluationItemModel` has `evaluation_id`, `manifest_order`,
`recording_id`, `analysis_run_id`, `status`, `gt_count`, `prediction_count`.

### DatasetExperiment surfaces

- Model has `dataset_evaluation_id` (`model.py:44`).
- `service.py`: `get_experiment`, `list_items`, `list_attempts(item_id)` (`84`),
  `_to_read` derived counts + `attempt_count` (`95-143`), `start_experiment`
  (`919`), `retry_failed` (`1042`), `_fail_experiment`, `_mark_item_failed`.
  No `mark_experiment_completed`, no `link_evaluation`, no `retry_evaluation`,
  no `list_experiments`.
- `coordinator.py`: `status != "running"` → `EXPERIMENT_TERMINAL` (`82-83`);
  no evaluating branch.
- `recovery.py` (G4): `_ACTIVE_RECOVERY_STATUSES = ("running",)`;
  `claim_experiment_generation` CAS `status == "running"`;
  `start_recovered_coordinator` PID guard `status == "running"`;
  `recover_dataset_experiments` runs `reconcile_items` + repair for every claim.
- `main.py`: recovery block steps 1-4; `benchmark_job_manager` state exists; no
  dataset-experiment router or `dataset_experiment_job_manager` state.
- No `dataset_experiments/router.py`.

### Confirmed gaps G5 must fill

- Transaction-neutral benchmark `prepare_evaluation` / `prepare_retry_evaluation`.
- Benchmark `start_evaluation` refactor: it currently `session.get()`s before
  `job_manager.start()`, i.e. spawns inside an autobegun transaction.
- Status-aware `refresh_coordinator_heartbeat` / `_fail_experiment`
  (`expected_status="running"` default) because the sealed helpers are
  running-only and cannot serve `evaluating`.
- Atomic Experiment evaluation-link ownership + coordinator evaluating branch.
- Generation-fenced evaluation writes (interrupted reset; restart normalization).
- Status-aware G4 recovery for `evaluating` (claim, PID guard, skip inference).
- Retry Evaluation service method with Experiment+Evaluation snapshots and
  generation-fenced compensation.
- DatasetExperiment REST router + `list_experiments` + item `latest_analysis_run_id`.

---

## ATOMIC EVALUATION OWNERSHIP

Problem: `create_evaluation()` self-commits. Composing
`create_evaluation` then `Experiment.dataset_evaluation_id = id; commit` creates:

```text
DatasetEvaluation COMMIT
→ crash
→ Experiment.dataset_evaluation_id NULL / status running
→ restart creates another DatasetEvaluation
```

Resolution (no schema change): a transaction-neutral prepare seam + a
one-commit link. `dataset_evaluation_id` is a real FK to
`dataset_evaluations.id`, so the staged evaluation row MUST be flushed into the
SAME transaction before the raw Experiment link UPDATE (immediate-FK databases
would otherwise reject the UPDATE referencing a not-yet-inserted row):

```text
Transaction (ONE commit):
    evaluation = benchmarks.prepare_evaluation(...)   # staged, NO commit, NO spawn
    session.flush()                                    # INSERT evaluation + items, NO COMMIT
    ds.link_evaluation(experiment_id, evaluation.id, coordinator_token)
        CAS UPDATE dataset_experiments
          SET dataset_evaluation_id = evaluation.id, status = 'evaluating'
          WHERE id = :id AND coordinator_token = :token
            AND status = 'running' AND dataset_evaluation_id IS NULL
    COMMIT   # evaluation rows + link + status become durable together

Outside transaction:
    benchmarks.start_evaluation(evaluation_id, benchmark_job_manager)
```

- If the link CAS misses, the caller `session.rollback()`s; the flushed
  evaluation + items disappear with the transaction, so no orphan row is ever
  committed.
- `dataset_evaluation_id IS NOT NULL` is the durable "already created" marker.
- `link_evaluation` CAS includes the coordinator generation and
  `status='running'`; an old/stale generation gets rowcount 0 → `FENCE_LOST`.
- `create_evaluation` public behavior is preserved exactly by composing
  `prepare_evaluation` + commit + refresh.
- No schema change is required; atomic linking is achieved by the seam.

---

## EVALUATION START CRASH WINDOWS

`start_evaluation` spawns the worker and only then persists `worker_pid`. The
durable state machine is:

| Status | `worker_pid` | Meaning | G5 action |
|---|---|---|---|
| `pending` | `NULL` | linked, spawn not durably requested | start it |
| `pending` | set | spawn requested; worker not yet `running` | wait |
| `running` | any | worker computing metrics | wait |
| `completed` | any | metrics done | Experiment → `completed` |
| `interrupted` | any | restart interrupted the worker | benchmark retry (metrics only) then start |
| `failed` | any | genuine metric failure | Experiment → `failed` |

Crash windows and outcomes:

1. **Crash after link commit, before spawn** → `pending` + `worker_pid NULL`.
   Recovery's coordinator starts it. Safe.
2. **Spawn failure** → sealed `start_evaluation` marks evaluation `failed`
   (`BENCHMARK_FAILED`); coordinator observes `failed` → Experiment `failed`.
3. **Crash after spawn, before PID persist** → `pending` + `worker_pid NULL`.
   Restart re-starts it. The orphaned local benchmark process is presumed dead,
   consistent with the sealed `mark_stale_running_evaluations_interrupted`
   assumption that local benchmark processes do not survive platform restart.
   (Conservative accepted behavior, documented; duplicate metric runs are
   idempotent and never rerun inference.)
4. **Worker spawned, still `pending` before `running`** → `pending` +
   `worker_pid` set; coordinator waits, never re-spawns.
5. **Restart while `running`** → pre-G4 step 2 marks it `interrupted`;
   coordinator retries metrics, no inference.
6. **Restart after `completed`** → coordinator sees `completed` and closes the
   Experiment.
7. **Restart with `pending` + `worker_pid` set** (spawn requested but worker
   died before flipping): G5 evaluating recovery durably marks the linked
   evaluation `interrupted` (local worker presumed dead) so the coordinator
   retries metrics instead of waiting forever. This prevents a permanently
   stuck `Experiment=evaluating / Evaluation=pending`.

There is no durable case of `Experiment=evaluating` + linked evaluation
`pending` + `worker_pid NULL` that is not started, because the recovery
coordinator always starts `pending`-with-no-PID evaluations.

---

## EVALUATING COORDINATOR STATE MACHINE

New outcome `CoordinatorOutcome.EXPERIMENT_COMPLETED` (added to `EXIT_OUTCOMES`).
`_step` branches BEFORE inference reconciliation:

```text
if status == "evaluating":  -> _step_evaluating(...)   # NO reconcile_items, NO scheduling
if status != "running":     -> EXPERIMENT_TERMINAL
else:                       -> existing running inference path
```

`_step_evaluating` uses ONLY evaluating-guarded primitives (defined below). Full
control flow:

```text
# heartbeat under the evaluating status guard
if not ds.refresh_coordinator_heartbeat(experiment_id, token, expected_status="evaluating"):
    return FENCE_LOST
try:
    if experiment.dataset_evaluation_id is None:
        raise PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                            "Evaluating experiment has no linked DatasetEvaluation.", 409)
    experiment, evaluation = ds.validate_evaluation_linkage(experiment_id)
    if evaluation.status == "completed":
        if not ds.mark_experiment_completed(experiment_id, token):
            return FENCE_LOST
        return EXPERIMENT_COMPLETED
    if evaluation.status == "failed":
        session.rollback()
        ds._fail_experiment(experiment_id, token,
                            "DATASET_EXPERIMENT_EVALUATION_FAILED",
                            evaluation.error_message, expected_status="evaluating")
        return INVARIANT_FAILED
    if evaluation.status == "interrupted":
        ds.reset_interrupted_evaluation(experiment_id, evaluation.id, token)  # one commit
        return WAITING
    if evaluation.status == "pending" and evaluation.worker_pid is None:
        session.rollback()                                  # close autobegin before spawn
        benchmarks.start_evaluation(evaluation.id, benchmark_job_manager)
        return WAITING
    if evaluation.status in {"pending", "running"}:
        return WAITING
    raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Linked DatasetEvaluation has an unknown status.", 409)
except PlatformError as exc:
    session.rollback()
    if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
        return CoordinatorOutcome.FENCE_LOST
    ds._fail_experiment(experiment_id, token, exc.code, exc.message,
                        expected_status="evaluating")
    return CoordinatorOutcome.INVARIANT_FAILED
except Exception:
    session.rollback()
    ds._fail_experiment(experiment_id, token,
                        "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                        "Unexpected evaluating coordinator error.",
                        expected_status="evaluating")
    return CoordinatorOutcome.INVARIANT_FAILED
```

### Status-aware coordinator primitives (backward compatible)

The sealed G3-C/G4 helpers are running-only. G5 makes them status-aware via an
OPTIONAL keyword whose default preserves sealed behavior exactly. It does NOT
globally widen any old helper to `status IN ("running","evaluating")`.

Signatures and the ONLY change vs the sealed bodies:

```python
    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token,
                                      *, expected_status="running"):
        # seam body unchanged EXCEPT the WHERE adds `status == expected_status`
        # in place of the literal `status == "running"`; still commits and
        # returns rowcount == 1.

    def _fail_experiment(self, experiment_id, coordinator_token, error_type,
                         error_message, *, expected_status="running"):
        # seam body unchanged EXCEPT the WHERE adds `status == expected_status`
        # in place of the literal `status == "running"`; still commits and
        # returns rowcount == 1.

    def mark_experiment_completed(self, experiment_id, coordinator_token):
        # evaluating-only by design (only reached from the evaluating branch):
        # WHERE id AND coordinator_token AND status == 'evaluating';
        # SET status='completed', completed_at=now, error_type=NULL,
        #     error_message=NULL; commits; returns rowcount == 1.
```

The COMPLETE executable bodies for all three are given in TASK 2 below. Existing
G1-G4 callers omit `expected_status`, so their running-only behavior is
byte-identical. The evaluating branch always passes
`expected_status="evaluating"`.

Guarantees: every evaluating path performs zero `reconcile_items` inference
scheduling, zero `start_item_attempt`, zero `launch_item_attempt`.

### All-success handoff is wiring-gated (sealed-behavior preservation)

The sealed G3-C all-success behavior must remain byte-compatible for coordinators
that were NOT wired for evaluation (for example the G3-C and G4 regression tests
that construct a coordinator without benchmark dependencies):

```text
if summary.queued == 0 and summary.running == 0 and summary.failed == 0 and summary.completed > 0:
    if benchmark_services_factory is None or benchmark_job_manager is None:
        return CoordinatorOutcome.INFERENCE_COMPLETE      # sealed G3-C/G4 behavior
    evaluation = self._ensure_evaluation(...)             # G5 atomic link (status now evaluating)
    session.rollback()                                    # autobegin boundary
    try:
        benchmarks.start_evaluation(evaluation.id, benchmark_job_manager)
    except PlatformError:
        # link already committed => Experiment.status == 'evaluating'
        session.rollback()
        ds._fail_experiment(experiment_id, token,
                            "DATASET_EXPERIMENT_EVALUATION_FAILED",
                            "Unable to start formal DatasetEvaluation.",
                            expected_status="evaluating")
        return CoordinatorOutcome.INVARIANT_FAILED
    return CoordinatorOutcome.WAITING
```

Special-case: this handoff is the ONLY place where a `running` step can mutate a
now-`evaluating` Experiment. Benchmark start failure is therefore projected with
`expected_status="evaluating"`, never through the running-only catch.

Production coordinators (spawned by `run_coordinator`) always pass both benchmark
dependencies, so they create and start the formal evaluation. Unwired coordinators
keep returning `INFERENCE_COMPLETE` and leaving the Experiment `running`, exactly
as sealed. This is why G5 adds the dependencies as OPTIONAL constructor keywords
and does not change the sealed constructor positionally.

`mark_experiment_completed` is token- AND `status='evaluating'`-guarded so a
stale generation cannot complete the Experiment.

### Linkage validation (`validate_evaluation_linkage`)

Fails closed (`DATASET_EXPERIMENT_INVARIANT_VIOLATION`) unless ALL hold:

- `dataset_evaluation_id` is not `NULL` and the evaluation row exists;
- `evaluation.recording_manifest_hash == experiment.recording_manifest_hash`;
- `evaluation.dataset_name/dataset_split/label_space` match the Experiment;
- `evaluation.evaluation_protocol == experiment.evaluation_protocol`;
- `evaluation.pipeline_id/pipeline_version == experiment.plugin_id/plugin_version`;
- evaluation items and Experiment items have identical `manifest_order` sets;
- each evaluation item's `recording_id` equals the matching Experiment item;
- each Experiment item is `completed`;
- each evaluation item `analysis_run_id` equals the item's latest successful
  Attempt's `completed` `AnalysisRun` id, and that Run's `recording_id` matches;
- `evaluation.expected_recordings == len(items)`, `missing_recordings == 0`,
  `coverage == 1.0`.

No mismatched evaluation is silently repaired.

---

## EVALUATING RESTART RECOVERY

Current G4 is running-only in THREE places, so changing the tuple alone is
insufficient:

1. `_ACTIVE_RECOVERY_STATUSES` selection;
2. `claim_experiment_generation` CAS `status == "running"`;
3. `start_recovered_coordinator` PID guard `status == "running"`;
4. plus the unconditional `reconcile_items` + local repair after claim.

G5 makes takeover status-aware:

```text
_ACTIVE_RECOVERY_STATUSES = ("running", "evaluating")

claim_experiment_generation(..., statuses=("running", "evaluating"))
    CAS WHERE status IN :statuses AND token = :expected AND cutoff predicate
    ON success writes coordinator_token=<fresh>, worker_pid=NULL, heartbeat_at=now

start_recovered_coordinator(..., statuses=("running", "evaluating"))
    PID guard WHERE token = :fresh AND status IN :statuses

recover_dataset_experiments:
    for each claimed experiment:
        if status == "evaluating":
            normalize_orphaned_evaluation(
                experiment_id, evaluation_id, claimed_token)   # atomic, generation-fenced
            # NO reconcile_items, NO repair_local_pending_runs, NO AnalysisRun
        else:  # running
            existing reconcile + local repair
        session.rollback()                 # autobegin boundary
        start_recovered_coordinator(...)   # status-aware
```

### Generation-fenced restart normalization (`normalize_orphaned_evaluation`)

This is a DatasetExperiment-recovery-owned write, NOT a plain benchmark helper,
because it must prove current Experiment generation ownership in the SAME atomic
UPDATE. One DB-side statement:

```python
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
        .values(status="interrupted",
                error_type="BENCHMARK_INTERRUPTED",
                error_message="Benchmark worker did not survive platform restart.",
                completed_at=now)
        .execution_options(synchronize_session=False)
```

`rowcount == 1` → normalized. On `rowcount == 0`:

1. fresh generation check on the Experiment:
   `status == "evaluating" AND coordinator_token == coordinator_token
   AND dataset_evaluation_id == evaluation_id`;
2. missing/mismatched → `DATASET_EXPERIMENT_FENCE_LOST` (rollback, zero mutation);
3. otherwise legitimate no-op (the evaluation is no longer `pending` + PID), return.

Preserved: fixed startup cutoff, same-startup takeover protection, stale-token
fencing, no subprocess spawn inside a DB transaction. Evaluating recovery creates
ZERO `AnalysisRun`s and performs no inference repair.

---

## AUTOMATIC MEMBERSHIP

Build in frozen `manifest_order`:

```text
for each Experiment Item (ordered by manifest_order):
    require item.status == "completed"
    attempts = attempts for item ordered by attempt_number
    latest = attempts[-1]
    run = session.get(AnalysisRun, latest.analysis_run_id)
    require run is not None
    require run.status == "completed"
    require run.recording_id == item.recording_id
    require run.pipeline_id == experiment.plugin_id
    require run.pipeline_version == experiment.plugin_version
    require run.executor == experiment.executor
    append {"recording_id": item.recording_id, "analysis_run_id": run.id}
```

Then `prepare_evaluation(..., items=membership, allow_incomplete=False,
evaluation_protocol=experiment.evaluation_protocol,
recording_manifest_hash=experiment.recording_manifest_hash)`. Partial success
(`failed > 0`) is rejected before membership building by the summary gate, so no
`DatasetEvaluation` is ever created. No historical Run outside the Experiment can
enter membership because selection is per-Experiment Item's latest Attempt.

---

## RETRY EVALUATION

Explicit Retry Evaluation preconditions (checked BEFORE any write):

```text
Experiment.status == "failed"
Experiment.dataset_evaluation_id is not None
ds.validate_evaluation_linkage(experiment_id) succeeds   # exact linkage required
all Items completed; no queued/running/failed Items
linked evaluation status in {"failed", "interrupted"}
```

Durable order (metrics only), symmetric with G4 Retry Failed:

```text
experiment_snapshot = {status, completed_at, heartbeat_at, coordinator_token,
                       worker_pid, error_type, error_message,
                       dataset_evaluation_id}
evaluation_snapshot = benchmarks.snapshot_retry_evaluation(evaluation_id)

TRANSACTION 1 (ONE commit):
    benchmarks.prepare_retry_evaluation(evaluation_id)   # staged reset to pending, NO commit
    CAS UPDATE dataset_experiments
        SET status='evaluating', coordinator_token=<fresh>, worker_pid=NULL,
            heartbeat_at=now, completed_at=NULL, error_type=NULL, error_message=NULL
        WHERE id=:id AND status='failed' AND dataset_evaluation_id=:evaluation_id
    IF rowcount != 1: rollback; raise DATASET_EXPERIMENT_INVALID_TRANSITION
    COMMIT

Outside transaction:
    pid = job_manager.start(experiment_id, fresh_token)
    ON raise: _restore_retry_evaluation(...)   # generation-fenced compensation below
    guarded PID persist:
        UPDATE dataset_experiments SET worker_pid=:pid
        WHERE id=:id AND coordinator_token=:fresh AND status='evaluating'
```

### Retry Evaluation compensation (generation-fenced, G4-equivalent)

```text
_restore_retry_evaluation(experiment_id, retry_token, evaluation_id,
                          experiment_snapshot, evaluation_snapshot):
    ONE transaction:
      1. FIRST acquire ownership ===
         UPDATE dataset_experiments
         SET status = experiment_snapshot.status,
             completed_at = experiment_snapshot.completed_at,
             heartbeat_at = experiment_snapshot.heartbeat_at,
             coordinator_token = experiment_snapshot.coordinator_token,
             worker_pid = experiment_snapshot.worker_pid,
             error_type = experiment_snapshot.error_type,
             error_message = experiment_snapshot.error_message
         WHERE id == experiment_id
           AND status == 'evaluating'
           AND coordinator_token == retry_token
           AND dataset_evaluation_id == evaluation_id
      2. IF rowcount != 1:
             rollback
             ZERO DatasetEvaluation mutation
             return False          # caller raises DATASET_EXPERIMENT_FENCE_LOST
      3. ONLY the CAS winner, in the SAME transaction:
             benchmarks.restore_retry_evaluation(evaluation_id, evaluation_snapshot)
      4. COMMIT
    return True
```

`restore_retry_evaluation` itself stays transaction-neutral; the ownership CAS is
what makes the compensation safe. If a newer generation already owns the
Experiment, the compensation performs zero writes to EITHER object.

### PID-persist CAS miss after a successful spawn

If `_restore` is not involved (spawn succeeded) but the guarded PID UPDATE
affects zero rows because a newer generation already owns the Experiment:

- do NOT compensate;
- the stale spawned coordinator is fenced by the newer token and exits with
  `FENCE_LOST` on its next step;
- `retry_evaluation` re-reads and raises `DATASET_EXPERIMENT_FENCE_LOST`.

Crash after Transaction 1 commit before/at spawn: Experiment `evaluating` with
fresh token, evaluation `pending`; the next startup recovery starts a coordinator
which starts the evaluation. Crash-resume is the accepted retry outcome.

`retry_evaluation` MUST NOT change any Item, create any Attempt, create any
`AnalysisRun`, or mutate any `DetectionResult`.

Public `DatasetBenchmarkService.retry_evaluation` behavior (`failed`/`interrupted`
→ `pending`, commit) is preserved by composing `prepare_retry_evaluation` +
commit + refresh.

---

## REST API

New `backend/app/dataset_experiments/router.py`, prefix `/api/dataset-experiments`:

| Method | Path | Delegates to |
|---|---|---|
| POST | `""` (201) | `create_experiment` |
| POST | `/{id}/run` (202) | `start_experiment(id, dataset_experiment_job_manager)` |
| GET | `""` | `list_experiments` |
| GET | `/{id}` | `get_experiment` |
| GET | `/{id}/items` | `list_items` |
| GET | `/{id}/items/{item_id}/attempts` | `list_attempts(item_id, experiment_id=id)` |
| POST | `/{id}/retry-failed` (202) | `retry_failed(id, dataset_experiment_job_manager)` |
| POST | `/{id}/retry-evaluation` (202) | `retry_evaluation(id, dataset_experiment_job_manager)` |

No pause/cancel/delete/priority. Router does not duplicate service logic; it
only binds a Session and calls the service. `main.py` includes the router and
sets `app.state.dataset_experiment_job_manager =
DatasetExperimentJobManager(settings)`.

---

## READ MODELS

Derived, never persisted:

- `DatasetExperimentRead` already exposes `expected_items`, `queued_items`,
  `running_items`, `completed_items`, `failed_items`, `attempt_count` (`_to_read`).
- Add derived `latest_analysis_run_id` to `DatasetExperimentItemRead`, computed in
  `list_items` from the item's latest Attempt; `NULL` when no Attempt.
- Add `list_experiments()` returning `DatasetExperimentRead` sorted by
  `created_at, id`.
- `list_attempts(item_id, *, experiment_id=None)` validates Experiment ownership
  when `experiment_id` is provided, returning 404/400 on mismatch.
- Attempts keep exposing the Attempt → `analysis_run_id` binding only; AnalysisRun
  status authority stays on `AnalysisRun`.

No persisted counters, no `current_analysis_run_id`, no new `attempt_count`
column.

---

## TRANSACTION BOUNDARIES

| Durable transition | Commit boundary | Must NOT span |
|---|---|---|
| Evaluation prepare + flush + Experiment link + `evaluating` | one commit | benchmark spawn |
| Benchmark start (`start_evaluation` refactor) | one commit for guarded PID persist | SELECT/autobegin and subprocess spawn: read→rollback→spawn→new txn |
| Evaluating completion projection | `mark_experiment_completed` one commit | — |
| Evaluating failure projection | `_fail_experiment(expected_status="evaluating")` one commit | — |
| Interrupted evaluation reset (metrics retry) | one commit: Experiment generation fence FIRST, then evaluation reset | benchmark spawn |
| Retry Evaluation (evaluation reset + Experiment `evaluating` + token) | one commit | coordinator spawn |
| Retry Evaluation compensation | one commit: ownership CAS FIRST, then restore both | — |
| Retry Evaluation PID persist | one commit | — |
| Evaluating recovery claim | one commit | — |
| Evaluating restart normalization | one commit: generation-fenced atomic UPDATE | — |
| Evaluating recovery PID persist | one commit | coordinator spawn |

Autobegin rule: `_step_evaluating` closes the Session (`session.rollback()`)
immediately before `start_evaluation`, and `recover_dataset_experiments` closes
before `start_recovered_coordinator`. A caller-side rollback is NOT sufficient on
its own for the benchmark start: the sealed `start_evaluation` performs
`session.get(...)` (autobegin) internally. G5 therefore refactors
`start_evaluation` itself to read/validate, rollback, then spawn with NO open
transaction (see below). After the refactor, the statement "`start_evaluation`
does not spawn inside a transaction" is TRUE by construction; it is NOT true of
the sealed pre-G5 implementation.

---

## TASK 1 — Benchmark transaction-neutral seams (behavior)

**Files:**
- Modify: `backend/app/benchmarks/service.py`.
- Create: `backend/tests/test_benchmark_transaction_seams.py`.

Add:

```python
    def prepare_evaluation(self, *, name, dataset_name, dataset_split, label_space,
                           recording_manifest_hash, items, allow_incomplete=False,
                           evaluation_protocol=DEFAULT_PHYSICAL_TF_PROTOCOL):
        # MOVE the existing create_evaluation body VERBATIM from
        #   frozen = self._build_frozen_manifest(...)
        # through
        #   self.session.add_all(item_rows)
        # and then `return evaluation` WITHOUT commit/refresh.
    def create_evaluation(self, **kwargs):
        evaluation = self.prepare_evaluation(**kwargs)
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation

    def start_evaluation(self, evaluation_id, job_manager):
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("BENCHMARK_NOT_FOUND", "DatasetEvaluation was not found.", 404)
        if evaluation.status != "pending":
            raise PlatformError("INVALID_BENCHMARK_TRANSITION",
                                "Only pending evaluations can be started.", 409)

        # Close the read/autobegin transaction BEFORE spawning the subprocess.
        self.session.rollback()

        try:
            worker_pid = job_manager.start(evaluation_id)   # ZERO open DB transaction
        except Exception as exc:
            now = datetime.now(timezone.utc)
            try:
                with self.session.no_autoflush:
                    self.session.execute(
                        update(DatasetEvaluationModel)
                        .where(
                            DatasetEvaluationModel.id == evaluation_id,
                            DatasetEvaluationModel.status == "pending",
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
        # running/completed/failed status back to pending/running.
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
        self.session.refresh(evaluation)
        return evaluation

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

    def retry_evaluation(self, evaluation_id):
        evaluation = self.prepare_retry_evaluation(evaluation_id)
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
```

`retry_evaluation` public result is unchanged (status `pending`, metrics cleared).
`mark_linked_evaluation_interrupted` is intentionally NOT a benchmark helper:
restart normalization is an Experiment-generation-fenced write owned by
`dataset_experiments/recovery.py` (see Task 4).

Tests:

- `test_prepare_evaluation_stages_without_commit` — call
  `prepare_evaluation`; a fresh Session sees no evaluation row; no PID/spawn.
- `test_create_evaluation_composes_prepare_and_commit` — existing public result.
- `test_start_evaluation_spawns_outside_transaction` — `on_start(evaluation_id)`
  opens a fresh Session over the SAME `benchmark_session` and asserts
  `not benchmark_session.in_transaction()` at Popen time.
- `test_start_evaluation_spawn_failure_fresh_guarded_update` — spawn raises; a
  fresh Session sees `failed`/`BENCHMARK_FAILED` committed; no stale ORM write.
- `test_start_evaluation_fast_worker_terminal_not_overwritten` — a fake job
  manager sets `completed` during `start`; the parent PID write leaves status
  `completed` and only records `worker_pid`.
- `test_prepare_retry_evaluation_stages_without_commit` — no commit; fresh Session
  still sees the old status.
- `test_retry_evaluation_public_behavior_unchanged`.
- `test_restore_retry_evaluation_roundtrip`.
- Existing benchmark suites unchanged.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_benchmark_transaction_seams.py -v
```
  Expected RED: `AttributeError ... prepare_evaluation` / `prepare_retry_evaluation`.
- [ ] Implement the seams by refactoring the existing bodies.
- [ ] GREEN: same invocation.
- [ ] Focused regressions:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_benchmark_worker.py backend/tests/test_benchmark_api.py \
  backend/tests/test_benchmark_membership.py -q
```
- [ ] Commit: `feat: add benchmark transaction-neutral evaluation seams`

---

## TASK 2 — DatasetExperiment evaluation ownership + Retry Evaluation (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`.
- Modify: `backend/app/dataset_experiments/schema.py` (derived read field).
- Create: `backend/tests/test_dataset_experiment_evaluation_ownership.py`.

Add to `DatasetExperimentService` (imports: `DatasetBenchmarkService`,
`DatasetEvaluationModel`, `DatasetEvaluationItemModel`):

```python
    # --- status-aware (backward-compatible) primitives ---
    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token,
                                      *, expected_status="running"):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == expected_status,
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def _fail_experiment(self, experiment_id, coordinator_token, error_type,
                         error_message, *, expected_status="running"):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == expected_status,
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

    def mark_experiment_completed(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "evaluating",
                    )
                    .values(status="completed",
                            completed_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    # --- evaluation ownership ---
    def link_evaluation(self, experiment_id, evaluation_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                        DatasetExperimentModel.dataset_evaluation_id.is_(None),
                    )
                    .values(dataset_evaluation_id=evaluation_id, status="evaluating")
                    .execution_options(synchronize_session=False)
                )
            rowcount = int(result.rowcount or 0)
            if rowcount != 1:
                self.session.rollback()
                return False
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True

    def build_evaluation_membership(self, experiment_id):
        experiment = self._get(experiment_id)
        items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all())
        if not items:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Experiment has no items.", 409)
        attempts_by_item = self._load_attempts_by_item([item.id for item in items])
        run_ids = {
            attempt.analysis_run_id
            for attempts in attempts_by_item.values() for attempt in attempts
        }
        runs_by_id = self._load_runs_by_id(run_ids)
        membership = []
        for item in items:
            if item.status != "completed":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Evaluation requires every item completed.", 409)
            attempts = attempts_by_item.get(item.id, [])
            if not attempts:
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Completed item has no attempt.", 409)
            latest = attempts[-1]
            run = runs_by_id.get(latest.analysis_run_id)
            if run is None or run.status != "completed":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Latest attempt has no completed AnalysisRun.", 409)
            if run.recording_id != item.recording_id:
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "AnalysisRun recording mismatch.", 409)
            if (run.pipeline_id != experiment.plugin_id
                    or run.pipeline_version != experiment.plugin_version
                    or run.executor != experiment.executor):
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "AnalysisRun identity does not match the frozen experiment.", 409)
            membership.append({"recording_id": item.recording_id,
                               "analysis_run_id": run.id})
        return membership

    def validate_evaluation_linkage(self, experiment_id):
        experiment = self._get(experiment_id)
        evaluation_id = experiment.dataset_evaluation_id
        if evaluation_id is None:
            raise PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                                "Experiment has no linked DatasetEvaluation.", 409)
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked DatasetEvaluation is missing.", 409)
        if evaluation.recording_manifest_hash != experiment.recording_manifest_hash:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation manifest hash mismatch.", 409)
        if (evaluation.dataset_name != experiment.dataset_name
                or evaluation.dataset_split != experiment.dataset_split
                or evaluation.label_space != experiment.dataset_label_space):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation dataset identity mismatch.", 409)
        if evaluation.evaluation_protocol != experiment.evaluation_protocol:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation protocol mismatch.", 409)
        if (evaluation.pipeline_id != experiment.plugin_id
                or evaluation.pipeline_version != experiment.plugin_version):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation pipeline identity mismatch.", 409)
        experiment_items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all())
        evaluation_items = list(self.session.scalars(
            select(DatasetEvaluationItemModel)
            .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
            .order_by(DatasetEvaluationItemModel.manifest_order)
        ).all())
        membership = self.build_evaluation_membership(experiment_id)
        if len(evaluation_items) != len(experiment_items) or len(membership) != len(experiment_items):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation membership size mismatch.", 409)
        for evaluation_item, expected in zip(evaluation_items, membership):
            if (evaluation_item.recording_id != expected["recording_id"]
                    or evaluation_item.analysis_run_id != expected["analysis_run_id"]):
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Linked evaluation membership mismatch.", 409)
        if (evaluation.expected_recordings != len(experiment_items)
                or evaluation.missing_recordings != 0
                or evaluation.coverage != 1.0):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation is not complete.", 409)
        return experiment, evaluation

    def reset_interrupted_evaluation(self, experiment_id, evaluation_id,
                                     coordinator_token):
        """Generation-fenced metrics-only reset of an interrupted linked evaluation.

        FIRST fence Experiment ownership in the SAME transaction, then stage the
        evaluation reset. Stale generation -> FENCE_LOST with zero evaluation
        mutation.
        """
        try:
            with self.session.no_autoflush:
                fence = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "evaluating",
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
                if int(fence.rowcount or 0) != 1:
                    self.session.rollback()
                    raise PlatformError(
                        "DATASET_EXPERIMENT_FENCE_LOST",
                        "Coordinator generation no longer owns the experiment.", 409)
                DatasetBenchmarkService(self.session).prepare_retry_evaluation(evaluation_id)
            self.session.commit()
        except PlatformError:
            raise
        except Exception:
            self.session.rollback()
            raise
        return True

    def list_experiments(self):
        experiments = list(self.session.scalars(
            select(DatasetExperimentModel)
            .order_by(DatasetExperimentModel.created_at, DatasetExperimentModel.id)
        ).all())
        return [self._to_read(experiment) for experiment in experiments]

    def retry_evaluation(self, experiment_id, job_manager):
        experiment = self._get(experiment_id)
        if experiment.status != "failed":
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Only a failed experiment can retry evaluation.", 409)
        if experiment.dataset_evaluation_id is None:
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Experiment has no linked evaluation to retry.", 409)
        items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
        ).all())
        if not items:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Experiment has no items.", 409)
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        if (counts["completed"] != len(items) or counts["failed"] or
                counts["queued"] or counts["running"]):
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Retry Evaluation requires complete inference.", 409)

        experiment, evaluation = self.validate_evaluation_linkage(experiment_id)
        if evaluation.status not in {"failed", "interrupted"}:
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Linked evaluation is not retryable.", 409)

        experiment_snapshot = {
            "status": experiment.status,
            "completed_at": experiment.completed_at,
            "heartbeat_at": experiment.heartbeat_at,
            "coordinator_token": experiment.coordinator_token,
            "worker_pid": experiment.worker_pid,
            "error_type": experiment.error_type,
            "error_message": experiment.error_message,
            "dataset_evaluation_id": experiment.dataset_evaluation_id,
        }
        benchmarks = DatasetBenchmarkService(self.session)
        evaluation_snapshot = benchmarks.snapshot_retry_evaluation(evaluation.id)
        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            benchmarks.prepare_retry_evaluation(evaluation.id)
            claimed = self.session.execute(
                update(DatasetExperimentModel)
                .where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "failed",
                    DatasetExperimentModel.dataset_evaluation_id == evaluation.id,
                )
                .values(status="evaluating", coordinator_token=token, worker_pid=None,
                        heartbeat_at=now, completed_at=None,
                        error_type=None, error_message=None)
                .execution_options(synchronize_session=False)
            )
            if int(claimed.rowcount or 0) != 1:
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                    "Retry Evaluation lost its ownership CAS.", 409)
            self.session.commit()
        except PlatformError:
            raise
        except Exception:
            self.session.rollback()
            raise

        try:
            worker_pid = job_manager.start(experiment_id, token)
        except Exception as exc:
            restored = self._restore_retry_evaluation(
                experiment_id, token, evaluation.id,
                experiment_snapshot, evaluation_snapshot)
            if not restored:
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "Retry Evaluation lost its generation; no evaluation "
                                    "was mutated.", 409) from exc
            raise PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                                "Unable to start DatasetExperiment coordinator for "
                                "evaluation retry.") from exc

        try:
            with self.session.no_autoflush:
                persisted = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                        DatasetExperimentModel.status == "evaluating",
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            if int(persisted.rowcount or 0) != 1:
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "A newer generation owns the experiment.", 409)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.expire_all()
        return self.session.get(DatasetExperimentModel, experiment_id)

    def _restore_retry_evaluation(self, experiment_id, retry_token, evaluation_id,
                                  experiment_snapshot, evaluation_snapshot):
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "evaluating",
                        DatasetExperimentModel.coordinator_token == retry_token,
                        DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                    )
                    .values(
                        status=experiment_snapshot["status"],
                        completed_at=experiment_snapshot["completed_at"],
                        heartbeat_at=experiment_snapshot["heartbeat_at"],
                        coordinator_token=experiment_snapshot["coordinator_token"],
                        worker_pid=experiment_snapshot["worker_pid"],
                        error_type=experiment_snapshot["error_type"],
                        error_message=experiment_snapshot["error_message"],
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(claimed.rowcount or 0) != 1:
                    self.session.rollback()
                    return False
                DatasetBenchmarkService(self.session).restore_retry_evaluation(
                    evaluation_id, evaluation_snapshot)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True
```

`list_attempts` gains `*, experiment_id=None` ownership validation.
`list_items` computes derived `latest_analysis_run_id`.
`DatasetExperimentItemRead` gains `latest_analysis_run_id: str | None = None`.

Tests (`test_dataset_experiment_evaluation_ownership.py`):
- `test_evaluating_heartbeat_succeeds_current_token`
- `test_evaluating_heartbeat_stale_token_fence_lost`
- `test_evaluating_failure_projection_current_token`
- `test_evaluating_failure_projection_stale_token_noop`
- `test_running_heartbeat_default_behavior_unchanged`
- `test_mark_experiment_completed_guards_token_and_status`
- `test_link_evaluation_cas_sets_link_and_evaluating_once`
- `test_link_evaluation_stale_token_returns_false`
- `test_build_evaluation_membership_manifest_order_and_success_runs`
- `test_validate_evaluation_linkage_rejects_manifest_mismatch`
- `test_validate_evaluation_linkage_rejects_protocol_mismatch`
- `test_validate_evaluation_linkage_rejects_membership_mismatch`
- `test_reset_interrupted_evaluation_current_generation`
- `test_reset_interrupted_evaluation_stale_generation_fence_lost` (two Sessions:
  A loads interrupted under T1; B rotates T1→T2; A reset → `FENCE_LOST`;
  evaluation remains `interrupted`)
- `test_retry_evaluation_requires_failed_with_complete_inference`
- `test_retry_evaluation_rejects_incomplete_inference`
- `test_retry_evaluation_rejects_invalid_linkage` (membership/manifest/protocol
  mismatch → rejected, zero spawn)
- `test_retry_evaluation_resets_evaluation_and_sets_evaluating_one_commit`
- `test_retry_evaluation_creates_zero_attempts_and_runs`
- `test_retry_evaluation_spawn_failure_restores_exact_projections` (Experiment
  terminal snapshot AND Evaluation snapshot restored)
- `test_retry_evaluation_stale_compensation_zero_mutation` (B rotates T1→T2
  before compensation; `_restore_retry_evaluation` returns False; ZERO
  Evaluation and Experiment mutation; `FENCE_LOST`)
- `test_retry_evaluation_concurrent_one_winner`
- `test_retry_evaluation_pid_cas_loss_does_not_compensate_newer_generation`
- `test_list_experiments_derived_counts`
- `test_list_attempts_ownership_validation`

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_evaluation_ownership.py -v
```
  Expected RED: `AttributeError ... mark_experiment_completed`.
- [ ] Implement.
- [ ] GREEN; focused regressions (`test_dataset_experiment_retry_failed.py`,
  `test_dataset_experiment_read_model.py`).
- [ ] Commit: `feat: add dataset experiment evaluation ownership`

---

## TASK 3 — Coordinator evaluating branch + outcome + wiring (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/coordinator.py`.
- Modify: `backend/app/dataset_experiments/worker.py`.
- Modify: `backend/app/dataset_experiments/wiring.py` (benchmark job manager).
- Create: `backend/tests/test_dataset_experiment_coordinator_evaluating.py`.

Coordinator constructor gains optional keyword args
`benchmark_services_factory=None, benchmark_job_manager=None` (preserving the
sealed signature when omitted). Add `EXPERIMENT_COMPLETED` to `CoordinatorOutcome`
and `EXIT_OUTCOMES`. `_step` branches to `_step_evaluating` for
`status == "evaluating"`. For a running experiment, after the summary gate shows
all-success, `_step` calls `_ensure_evaluation` then `start_evaluation`.

`_ensure_evaluation`:

```text
if experiment.dataset_evaluation_id is not None: return it
membership = ds.build_evaluation_membership(experiment_id)
evaluation = benchmarks.prepare_evaluation(
    name=f"{experiment.name} evaluation",
    dataset_name=experiment.dataset_name, dataset_split=experiment.dataset_split,
    label_space=experiment.dataset_label_space,
    recording_manifest_hash=experiment.recording_manifest_hash,
    items=membership, allow_incomplete=False,
    evaluation_protocol=experiment.evaluation_protocol)
self.session.flush()          # INSERT evaluation + items into the SAME txn (FK visibility), NO COMMIT
if not ds.link_evaluation(experiment_id, evaluation.id, coordinator_token):
    self.session.rollback()   # flushed evaluation rows disappear; no orphan
    raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST", "link lost", 409)
return evaluation
```

Note: `prepare_evaluation` returns two staged objects; the flush makes them
visible to the raw FK-bearing Experiment link UPDATE in the same transaction
without committing. If the link CAS loses, the rollback discards them.

Worker/wiring: `run_coordinator` builds `LocalBenchmarkJobManager(settings)` and
`benchmark_services_factory=lambda session: DatasetBenchmarkService(session)`,
passing them to the coordinator constructor.

Tests:
- `test_all_success_creates_exactly_one_linked_evaluation` (allow_incomplete
  False, exact membership, Experiment `evaluating`).
- `test_all_success_without_benchmark_wiring_returns_inference_complete` (sealed
  G3-C/G4 behavior preserved for unwired coordinators: no evaluation created,
  Experiment stays `running`).
- `test_prepare_flush_link_rollback_leaves_zero_evaluation_rows` (a stale link
  CAS → rollback → zero committed `DatasetEvaluation`/item rows).
- `test_repeated_step_creates_no_duplicate` (one `DatasetEvaluationModel`, same
  `dataset_evaluation_id`).
- `test_stale_generation_cannot_link_or_complete`.
- `test_benchmark_start_failure_after_link_fails_evaluating_experiment` (link
  commits, `start_evaluation` raises; Experiment ends `failed`, never stranded
  `evaluating`).
- `test_evaluation_pending_without_pid_is_started_once`.
- `test_evaluation_pending_with_pid_waits` (no second spawn).
- `test_evaluation_running_waits`.
- `test_evaluation_completed_closes_experiment_same_iteration`.
- `test_evaluation_interrupted_retries_metrics_only`.
- `test_evaluation_failed_fails_experiment`.
- `test_missing_linked_evaluation_fails_closed`.
- `test_every_evaluating_path_creates_zero_analysis_runs`.
- `test_partial_success_never_creates_evaluation`.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_coordinator_evaluating.py -v
```
  Expected RED: non-running `evaluating` currently returns `EXPERIMENT_TERMINAL`
  and never creates an evaluation.
- [ ] Implement.
- [ ] GREEN; focused regressions (`test_dataset_experiment_coordinator_terminal.py`,
  `..._scheduling.py`, `..._fencing.py`).
- [ ] Commit: `feat: add dataset experiment evaluating coordinator`

---

## TASK 4 — G5 evaluating recovery takeover (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/recovery.py`.
- Create: `backend/tests/test_dataset_experiment_evaluating_recovery.py`.

Change `_ACTIVE_RECOVERY_STATUSES = ("running", "evaluating")`;
`claim_experiment_generation(..., statuses=("running", "evaluating"))` and
`start_recovered_coordinator(..., statuses=("running", "evaluating"))` use
`status IN statuses`; `recover_dataset_experiments` branches per claimed status:
evaluating → `normalize_orphaned_evaluation(experiment_id, evaluation_id,
claimed_token)` (recovery-owned generation-fenced atomic UPDATE defined in
EVALUATING RESTART RECOVERY) + skip reconcile/repair; running → sealed G4 path.

Tests:
- `test_evaluating_experiment_receives_fresh_generation_and_coordinator`.
- `test_evaluating_recovery_creates_zero_analysis_runs_and_no_repair`.
- `test_same_startup_cutoff_protects_evaluating`.
- `test_stale_evaluating_coordinator_fenced`.
- `test_pending_with_pid_normalized_to_interrupted`.
- `test_stale_recovery_cannot_normalize_after_generation_rotation` (A claims
  evaluating under T1; B owns newer T2; A normalization under T1 →
  `FENCE_LOST`; Evaluation unchanged).
- `test_normalization_zero_rows_with_current_generation_is_noop`.
- `test_running_evaluation_stale_handler_then_metrics_retry`.
- `test_evaluating_completed_after_restart_closes_experiment`.
- `test_running_experiment_recovery_unchanged`.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_evaluating_recovery.py -v
```
  Expected RED: evaluating experiment is not selected; `claimed == 0`.
- [ ] Implement.
- [ ] GREEN; focused regressions (`test_dataset_experiment_restart_recovery.py`,
  `test_dataset_experiment_local_launch_recovery.py`).
- [ ] Commit: `feat: add dataset experiment evaluating recovery`

---

## TASK 5 — REST router + main wiring (behavior)

**Files:**
- Create: `backend/app/dataset_experiments/router.py`.
- Modify: `backend/app/main.py`.
- Create: `backend/tests/test_dataset_experiment_api.py`.

Router implements the EXACT V1 surface above, following the benchmark router
Session pattern and using `app.state.dataset_experiment_job_manager`. `main.py`
constructs `app.state.dataset_experiment_job_manager =
DatasetExperimentJobManager(settings)` and `include_router(dataset_experiments_router)`.

Tests (per endpoint success / not-found / invalid transition / response shape):
- create 201; run 202; list; get; items (derived counts + `latest_analysis_run_id`);
  attempts (ownership); retry-failed 202; retry-evaluation 202.
- not-found 404 for get/items/attempts/run/retry.
- invalid transition 409 (e.g. run a completed experiment, retry-failed when not
  `completed_with_failures`).
- unknown extra field rejected on create.

### Steps

- [ ] Write tests. RED command:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_api.py -v
```
  Expected RED: 404 for every `/api/dataset-experiments` route.
- [ ] Implement.
- [ ] GREEN; focused regressions (benchmark API + main startup tests).
- [ ] Commit: `feat: add dataset experiment REST API`

---

## TASK 6 — G5 regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_dataset_experiment_g5_regression.py`.
- Update `backend/tests/test_dataset_experiment_g4_regression.py` guards that G5
  intentionally supersedes (recovery/coordinator now evaluation-aware): assert
  the G4 *invariants* (no `prepare_run(`, no `provider.launch(`, no SSH, no torch,
  schemas unchanged) instead of the pre-G5 absence of `DatasetEvaluation`.

Guards:
- DatasetExperiment / Item / Attempt / AnalysisRun / DatasetEvaluation schemas
  unchanged (exact column sets).
- `recovery.py` and `coordinator.py` remain torch/ultralytics-free and never call
  `provider.launch(` / `prepare_run(` / SSH.
- `_ACTIVE_RECOVERY_STATUSES == ("running", "evaluating")`.
- Evaluating branch never references scheduling primitives.
- No frontend/remote/metric files changed.

Regression commands:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_benchmark_transaction_seams.py \
  backend/tests/test_dataset_experiment_evaluation_ownership.py \
  backend/tests/test_dataset_experiment_coordinator_evaluating.py \
  backend/tests/test_dataset_experiment_evaluating_recovery.py \
  backend/tests/test_dataset_experiment_api.py \
  backend/tests/test_dataset_experiment_g5_regression.py -v

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_retry_failed.py \
  backend/tests/test_dataset_experiment_local_launch_recovery.py \
  backend/tests/test_dataset_experiment_restart_recovery.py \
  backend/tests/test_dataset_experiment_startup_order.py \
  backend/tests/test_dataset_experiment_g4_regression.py \
  backend/tests/test_dataset_experiment_coordinator_regression.py \
  backend/tests/test_benchmark_worker.py backend/tests/test_benchmark_api.py \
  backend/tests/test_benchmark_membership.py -q

PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Baseline before G5: `1599 passed, 28 skipped`; require a fresh summary with
0 failed / 0 errors. Commit: `test: add phase G G5 regression matrix`.

---

## UPDATED TEST MATRIX

### Status-aware primitives
- evaluating heartbeat, current token → succeeds;
- evaluating heartbeat, stale token → `FENCE_LOST`;
- evaluating failure projection, current token → Experiment `failed`;
- evaluating failure projection, stale token → zero mutation;
- running heartbeat/failure default behavior unchanged;
- benchmark-start failure after link does not strand `evaluating`.

### Benchmark spawn boundary
- `start_evaluation` calls `job_manager.start` with
  `benchmark_session.in_transaction() == False` (probe inside `on_start`);
- fast benchmark worker terminal state is not overwritten by parent PID
  persistence;
- spawn failure uses a fresh guarded UPDATE (no stale ORM write).

### Retry Evaluation
- exact Experiment terminal projection restored on spawn failure;
- exact Evaluation projection restored on spawn failure;
- stale compensation after `T1 -> T2` → ZERO Evaluation and Experiment mutation,
  `FENCE_LOST`;
- invalid linkage (membership/manifest/protocol) rejected with zero spawn;
- concurrent Retry Evaluation → exactly one winner;
- PID CAS loss after successful spawn does not compensate a newer generation;
- zero new Attempts / AnalysisRuns / Item mutations.

### Evaluating write fencing
- stale coordinator cannot reset `interrupted -> pending`;
- stale recovery cannot normalize `pending + PID -> interrupted`;
- current generation resets/normalizes exactly once.

### Atomic ownership
- `prepare_evaluation` + `flush` + link is one commit;
- link CAS failure rollback leaves zero orphan Evaluation/item rows;
- repeated coordinator step creates exactly one linked Evaluation;
- evaluating paths create zero AnalysisRuns.

### Retained
All previously planned G5 tests plus the complete G1-G4 regression suites.

---

## G4 REGRESSION GUARANTEES

G5 preserves exactly:

- Retry Failed semantics and generation-fenced compensation;
- the ONE fixed startup cutoff and same-startup takeover protection;
- generation fencing and stale-token `FENCE_LOST`;
- local A→B same-Run first launch and marker-before-launch ordering;
- local ambiguity fail-closed (`ANALYSIS_LAUNCH_AMBIGUOUS`, zero relaunch);
- remote GPU recovery delegation and the startup order;
- the pre-spawn no-open-transaction rule;
- no duplicate active Attempt, no completed-Item rerun;
- existing G1-G4 callers of `refresh_coordinator_heartbeat` / `_fail_experiment`
  keep running-only behavior because `expected_status` defaults to `"running"`.

G5 adds no scheduling path into the evaluating branch and does not weaken any
running path.

---

## Self-Review

1. Formal evaluation only after 100% inference success. PASS
2. Automatic evaluation cannot be duplicated (one-commit link + durable
   `dataset_evaluation_id` + generation-fenced CAS). PASS
3. Exact Experiment-owned Run membership only (latest successful Attempt per
   frozen Item). PASS
4. No partial evaluation (`allow_incomplete=False`, failed>0 blocked). PASS
5. Evaluating never schedules inference (`_step` branches before reconcile). PASS
6. Retry Evaluation creates zero AnalysisRuns/Attempts and no DetectionResult
   mutation. PASS
7. Evaluating restart does not re-enter inference recovery (status-aware takeover
   skips reconcile/repair). PASS
8. Completed evaluation closes the Experiment in the same coordinator iteration. PASS
9. Benchmark metrics remain the existing authority (no metric code changed). PASS
10. G4 generation/cutoff guarantees intact (additive status awareness only). PASS
11. No transaction crosses subprocess spawn; autobegin closed before spawn. PASS
12. No schema/G6/frontend leakage. PASS
13. evaluating heartbeat/failure actually work through production helpers with
    `expected_status="evaluating"`. PASS
14. Evaluating failure can durably persist `Experiment -> failed`; missing/
    mismatched linkage fails closed. PASS
15. `start_evaluation` truly spawns with `session.in_transaction() == False`
    after the refactor. PASS
16. Old coordinator cannot reset an interrupted Evaluation after token rotation
    (Experiment generation fence in the same transaction). PASS
17. Old recovery generation cannot normalize `pending + PID -> interrupted`
    after token rotation (atomic `EXISTS` generation guard). PASS
18. Retry Evaluation stale compensation cannot mutate either object. PASS
19. Retry Evaluation restores the COMPLETE terminal Experiment projection and
    requires exact valid linked membership. PASS
20. Automatic Evaluation creation has no FK/order/orphan window (flush + one
    commit). PASS
21. All-success benchmark-start failure cannot strand `evaluating`. PASS
22. No inference path is reachable from evaluating. PASS

---

## Scope Audit

Must NOT change: `backend/app/db/**`, any `model.py`, migrations,
`backend/app/remote_execution/**`, benchmark metric modules
(`app/evaluation/**`), `app/analysis/**` science, frontend, AssetManifest
hashing, canonical remote request hashing.

Expected G5 production surface: `backend/app/benchmarks/service.py`,
`backend/app/dataset_experiments/service.py`, `.../coordinator.py`,
`.../recovery.py`, `.../worker.py`, `.../wiring.py`, new `.../router.py`,
`.../schema.py`, `backend/app/main.py`.

If a schema change proves necessary:
`STOP — G5 evaluation ownership requires schema change: <reason>`.

---

## Verification

- `git diff --check` clean.
- Every task recorded with RED command, observable RED failure, GREEN command,
  focused regressions, and commit checkpoint.
- Complete G5 suite green; G1-G4 + benchmark + remote/local/evaluation recovery
  suites green; full backend fresh summary with 0 failed / 0 errors.
- Scope scan confirms only the intended files changed.
