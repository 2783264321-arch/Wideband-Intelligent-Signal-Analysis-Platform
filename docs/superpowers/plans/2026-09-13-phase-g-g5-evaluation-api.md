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
    ds.start_linked_evaluation(experiment_id, evaluation_id, coordinator_token,
                               benchmark_job_manager)
    # generation-fenced pre-spawn ownership; NOT the manual benchmark start
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

## DURABLE EVALUATION START CLAIM

Generation ownership alone proves "I am the current Experiment generation" but
NOT "I am the unique starter of this DatasetEvaluation". Two actors can both see
`pending + worker_pid NULL` and both spawn (same-token duplicate actor; manual
`/run` vs automatic start; T1 spawn → PID persist loss → T2 takeover before the
worker flips `running`).

Existing fields are sufficient: the benchmark worker does not require the
evaluation to be `pending`; its first action writes `status="running"` again. G5
therefore uses `pending -> running` as the durable single-winner start claim. NO
schema column is added.

Both manual and automatic start use a CAS that atomically transitions
`pending -> running` (with `worker_pid IS NULL`); the automatic path additionally
embeds an `EXISTS` Experiment generation/link predicate in the SAME statement.
Only the CAS winner may spawn. This gives DatasetEvaluation the same
duplicate-execution protection that `AnalysisRun.launch_requested_at` gives the
G3-B local first launch.

Consequences:

- claim committed before spawn (durable `running` marker);
- T1 spawn → PID persist loss → T2 sees `running`, never re-claims `pending`, so
  it never spawns a second worker for the same Evaluation;
- crash after claim before spawn is recovered by the existing
  `mark_stale_running_evaluations_interrupted` → metrics-only retry;
- manual-vs-manual, automatic-vs-automatic, and manual-vs-automatic all collapse
  to exactly one CAS winner.

---

## EVALUATION START CRASH WINDOWS

`start_evaluation` spawns the worker and only then persists `worker_pid`. The
durable state machine is:

The durable start claim is `pending -> running` (see DURABLE EVALUATION START
CLAIM below): the Evaluation becomes `running` in the SAME commit that proves
generation ownership and uniquely claims the start, BEFORE any subprocess spawn.

| Status | `worker_pid` | Meaning | G5 action |
|---|---|---|---|
| `pending` | `NULL` | linked, start not yet claimed | claim `pending -> running`, then spawn |
| `running` | `NULL` | start claimed; spawn reached or not | wait / reconcile; restart stale handler → `interrupted` |
| `running` | set | worker started/PID persisted | wait |
| `pending` | set | legacy pre-claim state only | wait (defensive) |
| `completed` | any | metrics done | Experiment → `completed` |
| `interrupted` | any | restart interrupted the worker | metrics-only retry: reset `pending`, re-claim, spawn |
| `failed` | any | genuine metric failure | Experiment → `failed` |

Crash windows and outcomes (automatic path uses
`DatasetExperimentService.start_linked_evaluation`; manual benchmark API uses
`DatasetBenchmarkService.start_evaluation`; both use the same
`pending -> running` start claim):

1. **Crash after link commit, before the start claim** → `pending` + `worker_pid
   NULL`. Recovery's coordinator claims and starts it. Safe.
2. **Crash after the start claim, before spawn** → Evaluation durable `running`.
   The pre-G4 startup handler `mark_stale_running_evaluations_interrupted` turns
   it into `interrupted`; the coordinator resets `pending` (generation-fenced),
   re-claims, and spawns. No duplicate spawn from `pending`.
3. **Definitive spawn failure before any process exists** →
   `start_linked_evaluation` commits a generation-fenced Evaluation
   `running -> failed` (`BENCHMARK_FAILED`) and raises
   `DATASET_EXPERIMENT_EVALUATION_FAILED`; coordinator fails the Experiment under
   `expected_status="evaluating"`.
4. **Spawn succeeded but PID persistence uncertain** →
   `start_linked_evaluation` returns `"uncertain"`; the Experiment stays
   `evaluating` and the Evaluation stays `running`; the coordinator returns
   `WAITING` (never a false failure). The next step/restart reconciles.
5. **Crash after spawn, before PID persist** → Evaluation `running` +
   `worker_pid NULL`. Restart's stale handler marks it `interrupted`; metrics-only
   retry. The orphaned local benchmark process is presumed dead, consistent with
   the sealed stale handler. No second worker is spawned from `pending`.
6. **Worker spawned, `running` + PID set** → coordinator waits, never re-spawns.
7. **Restart while `running`** → pre-G4 step 2 marks it `interrupted`;
   coordinator retries metrics, no inference.
8. **Restart after `completed`** → coordinator sees `completed` and closes the
   Experiment.

Legacy state `pending` + `worker_pid` set (historical/manual) is handled
defensively by the evaluating-recovery normalization (`pending + PID ->
interrupted`); the normal G5 automatic start never produces it because the claim
runs BEFORE spawn.

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
        # Atomic generation-fenced unique start claim, then spawn. Return value is
        # one of "started" / "uncertain" / "already_started" -> WAITING; raises
        # FENCE_LOST / EVALUATION_FAILED, handled by the outer except.
        ds.start_linked_evaluation(experiment_id, evaluation.id, token,
                                   benchmark_job_manager)
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

### Coordinator caller semantics

Both call sites — the `running -> evaluating` all-success handoff and the
`_step_evaluating` `pending` branch — call `ds.start_linked_evaluation(...)` and
map its result identically:

```text
"started"         -> WAITING
"uncertain"       -> WAITING
"already_started" -> WAITING          # CAS lost while generation still current
FENCE_LOST        -> FENCE_LOST       # generation/link no longer current
EVALUATION_FAILED -> Experiment failed via expected_status="evaluating"
```

`"already_started"` is never treated as an error or a generation loss; it means
another actor holds the single Evaluation start claim and this actor must not
spawn.

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
    # From here the Experiment is durably `evaluating`. EVERY PlatformError below
    # is handled with evaluating ownership; none may reach the running-only catch.
    try:
        ds.start_linked_evaluation(experiment_id, evaluation.id, token,
                                   benchmark_job_manager)
        return CoordinatorOutcome.WAITING   # started / uncertain / already_started
    except PlatformError as exc:
        session.rollback()
        if exc.code == "DATASET_EXPERIMENT_FENCE_LOST":
            return CoordinatorOutcome.FENCE_LOST
        error_type = (
            "DATASET_EXPERIMENT_EVALUATION_FAILED"
            if exc.code == "DATASET_EXPERIMENT_EVALUATION_FAILED"
            else exc.code
        )
        if not ds._fail_experiment(experiment_id, token, error_type, exc.message,
                                   expected_status="evaluating"):
            return CoordinatorOutcome.FENCE_LOST
        return CoordinatorOutcome.INVARIANT_FAILED
    except Exception:
        # Preserve accepted start-uncertainty semantics: do not false-fail an
        # outcome whose physical spawn state may be unknowable.
        session.rollback()
        return CoordinatorOutcome.WAITING
```

Special-case: this handoff is the ONLY place where a `running` step can mutate a
now-`evaluating` Experiment. It never calls the unfenced
`DatasetBenchmarkService.start_evaluation`; it calls
`DatasetExperimentService.start_linked_evaluation` and projects EVERY post-link
`PlatformError` (including `DATASET_EXPERIMENT_INVARIANT_VIOLATION` from
`_diagnose_start_claim_miss`) with `expected_status="evaluating"`, never through
the running-only catch. No post-link PlatformError escapes.

### Start-uncertainty semantics (conceptual class `DATASET_EXPERIMENT_EVALUATION_START_UNCERTAIN`)

Chosen mechanism: `start_linked_evaluation` returns the sentinel string
`"uncertain"` (no exception) and callers map it to `WAITING`. The conceptual
class name is documented for logs/diagnostics only; it is NOT a raised error,
because raising would risk callers misclassifying it as a scientific failure.

Once the link commits, the Experiment is durably `evaluating`. From that point:

- **Definitive spawn failure** = the job manager raises BEFORE creating any
  process AND the generation-fenced `running -> failed` Evaluation write commits.
  Only then may the coordinator project `Experiment evaluating -> failed` with
  `error_type="DATASET_EXPERIMENT_EVALUATION_FAILED"` (via
  `_fail_experiment(expected_status="evaluating")`).
- **Start state uncertain** = the subprocess may have started but PID persistence
  failed, or a DB error occurred after physical spawn, or the persistence outcome
  cannot be proven. Required behavior:

  ```text
  Experiment remains evaluating (no failed projection)
  DatasetEvaluation remains running (durable start claim)
  no Item mutation; no Attempt; no AnalysisRun; no inference rerun
  coordinator returns WAITING (via start_linked_evaluation returning "uncertain")
  ```

  The next coordinator iteration or startup recovery reconciles the actual
  DatasetEvaluation status (the worker may already be running/completed). Parent
  PID persistence uncertainty alone never fails the Experiment, and because the
  Evaluation is already `running`, no actor can re-claim `pending` and spawn a
  second worker.

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
- the Experiment item `manifest_order` sequence EXACTLY equals the
  DatasetEvaluation item `manifest_order` sequence (compared explicitly, not via
  ordered zip alone);
- for every paired row:
  `evaluation_item.manifest_order == experiment_item.manifest_order`,
  `evaluation_item.recording_id == experiment_item.recording_id`, and
  `evaluation_item.analysis_run_id` equals the Experiment item's latest
  successful Attempt's `completed` `AnalysisRun` id;
- each Experiment item is `completed`;
- each DatasetEvaluation item `status == "included"` (the repository's exact
  full-membership item status; partial `missing_run` is already impossible here);
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
| Durable Evaluation start claim (`pending -> running`) | one commit (manual + automatic; automatic adds Experiment `EXISTS`) | benchmark subprocess spawn |
| Benchmark manual start (`start_evaluation` refactor) | start-claim commit, then PID persist commit | SELECT/autobegin and subprocess spawn: claim→rollback→spawn→new txn |
| Automatic linked start (`start_linked_evaluation`) | start-claim commit; fenced failure write commit; fenced PID commit | subprocess spawn runs between claim commit and PID txn |
| Automatic definitive spawn failure (`running -> failed`) | one fenced commit | — |
| Start-uncertainty (PID commit failure after spawn) | no failure commit | never fails Experiment; returns `"uncertain"` |
| Evaluating completion projection | `mark_experiment_completed` one commit | — |
| Evaluating failure projection | `_fail_experiment(expected_status="evaluating")` one commit | — |
| Interrupted evaluation reset (metrics retry) | one commit: Experiment generation fence FIRST, then evaluation reset | benchmark spawn |
| Retry Evaluation (evaluation reset + Experiment `evaluating` + token) | one commit | coordinator spawn |
| Retry Evaluation compensation | one commit: ownership CAS FIRST, then restore both | — |
| Retry Evaluation PID persist | one commit | — |
| Evaluating recovery claim | one commit | — |
| Evaluating restart normalization | one commit: generation-fenced atomic UPDATE | — |
| Evaluating recovery PID persist | one commit | coordinator spawn |

Autobegin rule: `_step_evaluating` and the all-success handoff call
`ds.start_linked_evaluation(...)`, which opens NO transaction before
`benchmark_job_manager.start(...)` (the pre-spawn fence commits, then it
`session.rollback()`s); `recover_dataset_experiments` closes before
`start_recovered_coordinator`. For the manual API, the refactored
`DatasetBenchmarkService.start_evaluation` reads/validates, rolls back, then
spawns with NO open transaction; the sealed pre-G5 `start_evaluation` did NOT,
which is why it is refactored. After the refactor, "no benchmark subprocess spawn
occurs inside an autobegun transaction" is TRUE by construction.

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
        # Transaction A: durable single-winner start claim (pending -> running).
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        DatasetEvaluationModel.status == "pending",
                        DatasetEvaluationModel.worker_pid.is_(None),
                    )
                    .values(status="running", started_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            if int(claimed.rowcount or 0) != 1:
                self.session.rollback()
                row = self.session.get(DatasetEvaluationModel, evaluation_id)
                if row is None:
                    raise PlatformError("BENCHMARK_NOT_FOUND",
                                        "DatasetEvaluation was not found.", 404)
                raise PlatformError("INVALID_BENCHMARK_TRANSITION",
                                    "Evaluation was already started.", 409)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        # No open transaction before spawn.
        self.session.rollback()

        try:
            worker_pid = job_manager.start(evaluation_id)
        except Exception as exc:
            # Claim is now running: definitive spawn failure -> running -> failed.
            now = datetime.now(timezone.utc)
            try:
                with self.session.no_autoflush:
                    self.session.execute(
                        update(DatasetEvaluationModel)
                        .where(
                            DatasetEvaluationModel.id == evaluation_id,
                            DatasetEvaluationModel.status == "running",
                            DatasetEvaluationModel.worker_pid.is_(None),
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
        # running/completed/failed status.
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
        return self.session.get(DatasetEvaluationModel, evaluation_id)

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

    def _evaluation_is_dataset_experiment_managed(self, evaluation_id):
        from app.dataset_experiments.model import DatasetExperimentModel
        return bool(self.session.scalar(
            select(exists().where(
                DatasetExperimentModel.dataset_evaluation_id == evaluation_id
            ))
        ))

    def retry_evaluation(self, evaluation_id):
        # OWNERSHIP: a DatasetExperiment-linked Evaluation is orchestration-managed
        # and MUST NOT be retried through the generic benchmark API; that would
        # leave Experiment=failed while the Evaluation advances out-of-band.
        self.get_evaluation(evaluation_id)   # 404 if missing
        if self._evaluation_is_dataset_experiment_managed(evaluation_id):
            raise PlatformError(
                "BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT",
                "This DatasetEvaluation is managed by DatasetExperiment; use the "
                "DatasetExperiment Retry Evaluation endpoint.",
                409,
            )
        evaluation = self.prepare_retry_evaluation(evaluation_id)
        self.session.commit()
        self.session.refresh(evaluation)
        return evaluation
```

`prepare_retry_evaluation` stays transaction-neutral and does NOT reject linked
evaluations: it is used internally by the evaluating coordinator interrupted
recovery and by DatasetExperiment explicit Retry Evaluation. Generic retry
behavior for UNLINKED evaluations is byte-compatible.

NOTE: G5 adds `exists` to the `app.benchmarks.service` SQLAlchemy imports
(`from sqlalchemy import exists, func, select, update`).

Tests:

- `test_prepare_evaluation_stages_without_commit` — call
  `prepare_evaluation`; a fresh Session sees no evaluation row; no PID/spawn.
- `test_create_evaluation_composes_prepare_and_commit` — existing public result.
- `test_start_evaluation_spawns_outside_transaction` — `on_start(evaluation_id)`
  opens a fresh Session over the SAME `benchmark_session` and asserts
  `not benchmark_session.in_transaction()` at Popen time.
- `test_start_evaluation_claim_committed_before_spawn` — a fresh Session at
  `on_start` sees Evaluation `running` + `started_at` already committed.
- `test_start_evaluation_concurrent_two_sessions_one_winner` — two Sessions call
  manual `start_evaluation`; exactly one CAS winner; exactly one
  `job_manager.start` call; loser raises `INVALID_BENCHMARK_TRANSITION`.
- `test_start_evaluation_spawn_failure_running_to_failed` — spawn raises after the
  claim; a fresh Session sees `failed`/`BENCHMARK_FAILED` (never reverted to
  pending).
- `test_start_evaluation_fast_worker_terminal_not_overwritten` — a fake job
  manager sets `completed` during `start`; the parent PID write leaves status
  `completed` and only records `worker_pid`.
- `test_prepare_retry_evaluation_stages_without_commit` — no commit; fresh Session
  still sees the old status.
- `test_retry_evaluation_public_behavior_unchanged` (UNLINKED failed/interrupted
  → pending, metrics cleared).
- `test_retry_evaluation_rejects_dataset_experiment_linked_failed` — a
  `DatasetExperiment.dataset_evaluation_id` referencing this failed Evaluation →
  `BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT` (409); Experiment and Evaluation
  unchanged.
- `test_retry_evaluation_rejects_dataset_experiment_linked_interrupted` — same
  for an interrupted linked Evaluation; zero mutation.
- `test_prepare_retry_evaluation_allows_linked_evaluation` — the neutral seam does
  NOT reject a linked Evaluation (internal use).
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
        if (len(evaluation_items) != len(experiment_items)
                or len(membership) != len(experiment_items)):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation membership size mismatch.", 409)
        experiment_order = [item.manifest_order for item in experiment_items]
        evaluation_order = [item.manifest_order for item in evaluation_items]
        if evaluation_order != experiment_order:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation manifest order mismatch.", 409)
        for experiment_item, evaluation_item, expected in zip(
                experiment_items, evaluation_items, membership):
            if (evaluation_item.manifest_order != experiment_item.manifest_order
                    or evaluation_item.recording_id != expected["recording_id"]
                    or evaluation_item.analysis_run_id != expected["analysis_run_id"]):
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Linked evaluation membership mismatch.", 409)
            if evaluation_item.status != "included":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Linked evaluation item is not fully included.", 409)
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
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise
        return True

    def start_linked_evaluation(self, experiment_id, evaluation_id,
                                coordinator_token, benchmark_job_manager):
        """Generation-fenced + uniquely-claimed automatic evaluation start.

        The start claim is the durable Evaluation ``pending -> running`` CAS,
        additionally requiring current Experiment generation/link ownership via
        ``EXISTS``. This single statement proves, atomically:
          1. current Experiment generation/link ownership;
          2. the Evaluation is startable (pending, no worker);
          3. this actor uniquely claims the Evaluation start.
        Only the CAS winner may physically spawn.

        Returns:
          "started"         -> claim committed + spawn + PID persisted
          "uncertain"       -> claim committed + spawn succeeded, PID uncertain
          "already_started" -> generation current but Evaluation already claimed
        Raises ``DATASET_EXPERIMENT_FENCE_LOST`` when ownership is lost and
        ``DATASET_EXPERIMENT_EVALUATION_FAILED`` on a definitive spawn failure
        (generation-fenced Evaluation ``running -> failed`` already committed).
        """
        # A. atomic generation-fenced unique start claim: pending -> running.
        try:
            with self.session.no_autoflush:
                claim = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        DatasetEvaluationModel.status == "pending",
                        DatasetEvaluationModel.worker_pid.is_(None),
                        exists().where(
                            DatasetExperimentModel.id == experiment_id,
                            DatasetExperimentModel.status == "evaluating",
                            DatasetExperimentModel.coordinator_token == coordinator_token,
                            DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                        ),
                    )
                    .values(status="running", started_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            if int(claim.rowcount or 0) != 1:
                self.session.rollback()
                result = self._diagnose_start_claim_miss(
                    experiment_id, evaluation_id, coordinator_token)
                self.session.rollback()   # close diagnostic reads before returning
                return result
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        # No open transaction before spawn.
        self.session.rollback()

        # B. physical spawn
        try:
            worker_pid = benchmark_job_manager.start(evaluation_id)
        except Exception as exc:
            # running -> failed, generation-fenced (claim put the Evaluation at running).
            try:
                with self.session.no_autoflush:
                    failed = self.session.execute(
                        update(DatasetEvaluationModel)
                        .where(
                            DatasetEvaluationModel.id == evaluation_id,
                            DatasetEvaluationModel.status == "running",
                            DatasetEvaluationModel.worker_pid.is_(None),
                            exists().where(
                                DatasetExperimentModel.id == experiment_id,
                                DatasetExperimentModel.status == "evaluating",
                                DatasetExperimentModel.coordinator_token == coordinator_token,
                                DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                            ),
                        )
                        .values(status="failed", error_type="BENCHMARK_FAILED",
                                error_message=str(exc)[:1000],
                                completed_at=datetime.now(timezone.utc))
                        .execution_options(synchronize_session=False)
                    )
                if int(failed.rowcount or 0) != 1:
                    self.session.rollback()
                    raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                        "Evaluation failure write lost its generation.", 409)
                self.session.commit()
            except PlatformError:
                self.session.rollback()
                raise
            except Exception:
                self.session.rollback()
                raise
            raise PlatformError("DATASET_EXPERIMENT_EVALUATION_FAILED",
                                "Unable to start formal DatasetEvaluation.") from exc

        # C. generation-fenced PID persistence; writes ONLY worker_pid.
        try:
            with self.session.no_autoflush:
                persisted = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        exists().where(
                            DatasetExperimentModel.id == experiment_id,
                            DatasetExperimentModel.status == "evaluating",
                            DatasetExperimentModel.coordinator_token == coordinator_token,
                            DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                        ),
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            if int(persisted.rowcount or 0) != 1:
                # Generation lost AFTER physical spawn. Do NOT compensate; the
                # newer generation is authority. The Evaluation is already
                # `running` (start claim), so T2 MUST NOT re-spawn it.
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "Evaluation PID persist lost its generation.", 409)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            # spawn state uncertain: subprocess may be running; Evaluation stays
            # `running`; do not fail Experiment.
            self.session.rollback()
            return "uncertain"
        return "started"

    def _diagnose_start_claim_miss(self, experiment_id, evaluation_id,
                                   coordinator_token):
        """Zero-row start-claim diagnosis.

        FIRST diagnose Experiment generation/link ownership. Stale -> FENCE_LOST.
        If the generation is still current, the miss is an idempotent start race
        (another actor already claimed/advanced the Evaluation), never
        FENCE_LOST; return ``"already_started"``. Impossible states fail closed.
        """
        generation = self.session.execute(
            select(DatasetExperimentModel.status,
                   DatasetExperimentModel.coordinator_token,
                   DatasetExperimentModel.dataset_evaluation_id)
            .where(DatasetExperimentModel.id == experiment_id)
        ).one_or_none()
        if (generation is None or generation[0] != "evaluating"
                or generation[1] != coordinator_token
                or generation[2] != evaluation_id):
            raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                "Coordinator generation no longer owns the experiment.", 409)
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked DatasetEvaluation is missing.", 409)
        if (evaluation.status in {"running", "completed", "failed"}
                or (evaluation.status == "pending" and evaluation.worker_pid is not None)):
            return "already_started"
        raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Evaluation start claim missed in an impossible state.", 409)

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
            self.session.rollback()
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
- `test_validate_evaluation_linkage_rejects_manifest_order_mismatch` (evaluation
  item orders `10,11,12` vs Experiment `0,1,2` but aligned recording/run pairs →
  rejected)
- `test_validate_evaluation_linkage_rejects_item_status_corruption` (an
  evaluation item not `included` → rejected)
- `test_validate_evaluation_linkage_rejects_protocol_mismatch`
- `test_validate_evaluation_linkage_rejects_membership_mismatch`
- `test_reset_interrupted_evaluation_current_generation`
- `test_reset_interrupted_evaluation_stale_generation_fence_lost` (two Sessions:
  A loads interrupted under T1; B rotates T1→T2; A reset → `FENCE_LOST`;
  evaluation remains `interrupted`)
- `test_reset_interrupted_evaluation_platform_error_rollback_session_reusable`
  (`prepare_retry_evaluation` rejects on the current generation → rollback →
  session has no open transaction and remains usable)
- `test_start_linked_evaluation_current_generation_spawns_once`
- `test_start_linked_evaluation_claim_committed_before_spawn` (fresh Session at
  `on_start` sees Evaluation `running` + `started_at` committed)
- `test_start_linked_evaluation_same_token_two_actors_one_claim` (two Sessions
  with the SAME Experiment token call `start_linked_evaluation`; exactly one
  start claim; exactly one spawn; loser returns `"already_started"`)
- `test_start_linked_evaluation_vs_manual_run_one_claim` (automatic actor vs
  manual `/run`: exactly one `pending -> running` claim and one spawn)
- `test_start_linked_evaluation_stale_generation_zero_spawn` (T1 loses before the
  start claim → ZERO benchmark spawn)
- `test_start_linked_evaluation_no_open_transaction_before_spawn` (`on_start`
  asserts `not session.in_transaction()`)
- `test_start_linked_evaluation_spawn_failure_write_fenced` (definitive spawn
  failure commits generation-fenced `running -> failed`, raises
  `DATASET_EXPERIMENT_EVALUATION_FAILED`; a stale T1 → `FENCE_LOST`, zero write)
- `test_start_linked_evaluation_pid_uncertainty_returns_uncertain` (spawn
  succeeds, PID commit raises → returns `"uncertain"`, Evaluation stays
  `running`, no failed projection)
- `test_start_linked_evaluation_stale_after_spawn_zero_newer_mutation` (T1 loses
  after spawn; PID write loses fence; newer generation's Evaluation untouched)
- `test_t2_after_t1_spawn_does_not_respawn_same_evaluation` (T1 claim → `running`
  + spawn; PID persist loses to T2; T2 observes `running`/terminal and performs
  ZERO additional spawn)
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
- `test_retry_evaluation_prepare_platform_error_rollback_session_reusable` (a
  `prepare_retry_evaluation` rejection in Transaction 1 → rollback → session not
  in transaction and reusable)
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
all-success, `_step` calls `_ensure_evaluation` then
`ds.start_linked_evaluation(...)` (NOT the unfenced benchmark
`start_evaluation`).

`_ensure_evaluation`:

```text
# A legal atomic link moves BOTH dataset_evaluation_id AND status -> evaluating.
# Reaching this running-branch helper with a link already present is corruption.
if experiment.dataset_evaluation_id is not None:
    raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Running experiment already has a linked DatasetEvaluation.", 409)
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

Corrupt-state handling: `running` + non-NULL `dataset_evaluation_id` fails closed
with `DATASET_EXPERIMENT_INVARIANT_VIOLATION` (zero new DatasetEvaluation, zero
benchmark spawn, normal running-branch fail-closed projection). Repeated
legitimate processing is owned by the `evaluating` branch, never by this state.

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
- `test_automatic_start_stale_token_zero_spawn` (T1 loses before the start claim;
  ZERO benchmark spawn).
- `test_automatic_start_spawns_with_no_open_transaction` (`on_start` asserts
  `not benchmark_session.in_transaction()`).
- `test_automatic_start_spawn_failure_write_is_generation_fenced` (definitive
  spawn failure commits Evaluation `running -> failed` and fails Experiment under
  `expected_status="evaluating"`; a stale T1 cannot perform that write).
- `test_automatic_start_pid_persist_uncertainty_keeps_evaluating` (spawn succeeds,
  PID commit fails → Experiment remains `evaluating`, Evaluation stays `running`,
  coordinator returns `WAITING`, zero AnalysisRuns).
- `test_uncertain_start_converges_later` (after uncertainty, a subsequent step
  reconciles the Evaluation's `running`/`completed` state).
- `test_automatic_start_pid_persist_stale_generation_zero_newer_mutation` (T1
  loses after spawn; PID write loses fence; newer generation untouched).
- `test_t1_claim_then_t2_takeover_does_not_respawn` (T1 claims `running` + spawns,
  PID persist loses to T2; T2's coordinator observes `running` and performs ZERO
  additional spawn; metrics-only).
- `test_crash_after_claim_before_spawn_recovers_via_stale_handler` (Evaluation
  durable `running`; startup stale handler → `interrupted`; coordinator resets +
  re-claims + spawns once; ZERO AnalysisRuns).
- `test_running_experiment_with_preexisting_evaluation_link_fails_closed_zero_spawn`.
- `test_post_link_invariant_platformerror_fails_experiment_under_evaluating_guard`
  (`_ensure_evaluation` links; `start_linked_evaluation` then raises
  `DATASET_EXPERIMENT_INVARIANT_VIOLATION`; Experiment is failed under
  `expected_status="evaluating"`; NOT stranded `evaluating`).
- `test_post_link_stale_token_failure_projection_returns_fence_lost` (generation
  rotates before the failure projection → `FENCE_LOST`, zero mutation to the
  newer generation).
- `test_post_link_no_platformerror_uses_running_only_projection` (guard that no
  post-link path calls `_fail_experiment` with the default `expected_status`).
- `test_benchmark_start_failure_after_link_fails_evaluating_experiment` (link
  commits, definitive start failure; Experiment ends `failed`, never stranded
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

### Durable Evaluation start claim
- `pending -> running` CAS is a single-winner start claim (manual + automatic);
- two manual `/run`, two automatic same-token actors, and manual-vs-automatic
  each produce exactly one claim and one spawn;
- claim commit happens before Popen; no open transaction at Popen;
- definitive spawn failure is `running -> failed` (never reverted to pending);
- PID persistence writes only PID; fast worker terminal status preserved;
- T1 claim+spawn → PID persist loss → T2 does NOT re-spawn;
- crash after claim before spawn → stale-running handler → metrics-only retry;
- evaluated paths create zero AnalysisRuns.

### Benchmark spawn boundary
- `start_evaluation` calls `job_manager.start` with
  `benchmark_session.in_transaction() == False` (probe inside `on_start`);
- fast benchmark worker terminal state is not overwritten by parent PID
  persistence;
- manual spawn failure uses a fresh guarded `running -> failed` UPDATE.

### Automatic evaluation start fencing (`start_linked_evaluation`)
- current generation spawns exactly once;
- same-token duplicate actor → `"already_started"`, zero extra spawn;
- stale generation before the start claim → ZERO benchmark spawn;
- spawn occurs with `session.in_transaction() == False`;
- definitive spawn-failure `running -> failed` write is generation-fenced;
- stale generation cannot perform the spawn-failure write;
- spawn success + PID-persist uncertainty → returns `"uncertain"`, Experiment
  stays `evaluating`, Evaluation stays `running`, zero AnalysisRuns;
- stale generation after spawn loses the PID fence and mutates nothing for the
  newer generation;
- uncertain start converges on a later step/restart;
- fast worker terminal state not overwritten.

### Linkage invariants
- evaluation item `manifest_order` sequence must equal Experiment item sequence;
- per-row `manifest_order` + `recording_id` + expected `analysis_run_id`;
- evaluation item `status == "included"`;
- running + pre-existing evaluation link → fail closed, zero spawn/new evaluation.

### Retry Evaluation
- exact Experiment terminal projection restored on spawn failure;
- exact Evaluation projection restored on spawn failure;
- stale compensation after `T1 -> T2` → ZERO Evaluation and Experiment mutation,
  `FENCE_LOST`;
- invalid linkage (membership/manifest/protocol) rejected with zero spawn;
- concurrent Retry Evaluation → exactly one winner;
- PID CAS loss after successful spawn does not compensate a newer generation;
- zero new Attempts / AnalysisRuns / Item mutations.

### Linked generic retry ownership
- unlinked generic benchmark retry unchanged (`failed`/`interrupted` → `pending`);
- linked failed generic retry → `BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT` (409);
- linked interrupted generic retry → 409;
- rejection mutates neither Experiment nor Evaluation;
- DatasetExperiment explicit Retry Evaluation still works via the neutral seam;
- evaluating coordinator interrupted metrics retry still works via the neutral
  seam.

### Cross-status handoff
- a post-link invariant `PlatformError` fails the Experiment under
  `expected_status="evaluating"`; it is never stranded evaluating;
- a post-link stale-token failure projection returns `FENCE_LOST` with zero
  mutation;
- no post-link error uses the running-only failure projection.

### Session hygiene
- Retry Evaluation Transaction 1 `PlatformError` leaves the Session not in a
  transaction and reusable.

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
23. Every DatasetExperiment-forced Evaluation write is generation-fenced,
    including automatic benchmark START (start claim, spawn-failure write,
    PID persistence) via `start_linked_evaluation`. PASS
24. Stale DatasetExperiment generation cannot spawn a linked benchmark worker
    after ownership is lost. PASS
25. PID-persistence uncertainty does not falsely fail the Experiment and does not
    create AnalysisRuns; definitive spawn failure does fail it under the
    evaluating guard. PASS
26. Linked evaluation item `manifest_order` and item status are explicitly
    validated. PASS
27. `running` + pre-existing `dataset_evaluation_id` fails closed with zero spawn
    and zero new evaluation. PASS
28. Manual benchmark API behavior is unchanged (only DatasetExperiment
    orchestration uses the generation-fenced start claim). PASS
29. `pending -> running` is a durable single-winner start claim; same-token
    duplicate actors cannot double-spawn the Evaluation. PASS
30. Manual-vs-manual, automatic-vs-automatic, and manual-vs-automatic start races
    each produce exactly one claim and one spawn. PASS
31. Token takeover after spawn cannot cause T2 to spawn the same Evaluation,
    because the claim already committed `running`. PASS
32. The start claim commits before Popen and no transaction spans Popen. PASS
33. Definitive spawn failure persists `running -> failed` (never reverted to
    pending); PID persistence writes only PID. PASS
34. Crash after claim-before-spawn is recoverable via the existing
    `mark_stale_running_evaluations_interrupted` → metrics-only retry. PASS
35. Generic benchmark retry cannot bypass DatasetExperiment Retry Evaluation for
    a linked Evaluation (`BENCHMARK_MANAGED_BY_DATASET_EXPERIMENT`). PASS
36. Unlinked generic benchmark retry behavior is unchanged. PASS
37. `prepare_retry_evaluation` stays transaction-neutral and does not reject
    linked evaluations, so interrupted-recovery and explicit Retry Evaluation
    still work. PASS
38. Once the `running -> evaluating` link commits, EVERY `PlatformError` in the
    handoff is handled with evaluating ownership; none falls into the running-only
    catch. PASS
39. A post-link failure projection that loses the generation returns `FENCE_LOST`
    with zero mutation. PASS
40. Durable Evaluation `pending -> running` start claim remains unchanged. PASS
41. Retry Evaluation Transaction 1 `PlatformError` rolls back; no transaction
    leak. PASS
42. Linked generic retry rejection mutates neither Experiment nor Evaluation. PASS
43. No schema/G6/frontend/remote leakage. PASS

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
