# Phase G G3-B Durable First Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal (G3-B sub-gate):** Implement the second sub-gate of the approved Phase G
**G3 — Orchestrator Core**: **G3-B — Durable First Launch**. For an
already-G3-A-owned `DatasetExperimentAttempt`, durably commit launch intent
(`Attempt.launch_requested_at`) for **local** executors in its own Transaction B,
and only after that commit invoke the sealed G2
`AnalysisService.launch_prepared_run(run_id)` primitive. G3-B performs the normal
first-launch path only: it never prepares/creates a Run, never retries, never
recovers ambiguity, and never implements coordinator/worker scheduling.

**Architecture:** One additive method on the sealed G1/G3-A
`DatasetExperimentService` (`launch_item_attempt`) plus one private helper
(`_claim_launch_intent`). For local executors it performs a conditional CAS
(`launch_requested_at IS NULL -> now`) and commits Transaction B before invoking
the reused G2 launch primitive; for `remote_gpu` it does not write
`launch_requested_at` and relies on the existing coordinator-token/fenced remote
semantics (spec §8). No table/column/migration change; no new launch logic.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, default engine, no PRAGMAs,
`expire_on_commit=False`), pytest 9. No GPU, no SSH, no torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
(§7 prepare/launch seam, §8 launch ambiguity local vs remote, §12 restart
recovery, §14.2 error families, §20 G3).

**Sealed dependencies:**
- G3-A: `docs/superpowers/plans/2026-09-13-phase-g-g3-transaction-a-ownership.md`
  (`start_item_attempt`, `_claim_queued_item`, `_next_attempt_number`).
- G2: `docs/superpowers/plans/2026-09-13-phase-g-g2-analysis-prepare-launch-seam.md`
  (`prepare_run`, `launch_prepared_run`).

**Base:** `feature/m9-2-implementation @ 96298d41c91ca3090f60f423c53423a08a4aa4ef`.

---

## Phase G Gate Structure (authoritative §20, refined into sub-gates)

| Sub-gate | Scope | Status |
|---|---|---|
| **G3-A — Transaction A Ownership** | revalidate; `prepare_run`; Attempt; Item `queued->running`; one commit; no launch | sealed |
| **G3-B — Durable First Launch** (this plan) | Transaction B (`Attempt.launch_requested_at`) for local, committed before physical launch; then `AnalysisService.launch_prepared_run`; normal path only | this document |
| **G3-C — Coordinator Core** | worker/job manager; Experiment `pending->running`; bounded `max_concurrency`; deterministic scheduling; reconciliation; best-effort continuation | deferred |
| **G4 — Retry and Recovery** | Retry Failed; local ambiguity fail-closed; coordinator-token fencing/restart; duplicate-active-Attempt recovery | deferred |
| G5 | DatasetEvaluation lifecycle + REST | deferred |
| G6 | real acceptance | deferred |

G3-B MUST NOT implement any G3-C/G4/G5 behavior.

---

## Global Constraints

1. **No launch before durable intent (local).** For `local_cpu`/`local_gpu`, the
   `Attempt.launch_requested_at` commit MUST succeed before any provider call.
2. **Transaction B is separate from Transaction A.** G3-B never calls
   `prepare_run`, never creates an `AnalysisRun`, never re-binds an Attempt.
3. **Reuse the G2 primitive.** The physical launch is exactly
   `AnalysisService.launch_prepared_run(run_id)`; G3-B never calls
   `provider.launch` directly.
4. **No recovery/retry.** G3-B never clears `launch_requested_at`, never creates a
   replacement Run/Attempt, never relaunches an ambiguous local Run.
5. **Remote semantics unchanged.** `remote_gpu` does not use the
   `launch_requested_at` fence; it reuses existing coordinator-token/fenced
   recovery (spec §8). G3-B does not invent a remote recovery model.
6. **Same Session.** The passed `AnalysisService` MUST share the exact
   `DatasetExperimentService` `Session` (Transaction B, refresh, and launch
   primitives all operate on it).
7. **No schema/migration change.** `DatasetExperimentAttemptModel`,
   `DatasetExperimentItemModel`, `DatasetExperimentModel`, migrations, and the
   `analysis_runs` schema are untouched.
8. **No canonical/science change.** Canonical request hashing, fingerprinting,
   AssetManifest V1, ModelRelease authority, execution-certificate semantics, and
   scientific pipelines are untouched.
9. **Production scope (behavior):** `backend/app/dataset_experiments/service.py`.
   Plus one behavior-preserving documentation-only docstring correction in
   `backend/app/analysis/service.py` (§10 audit).

---

## Repository Mapping (verified against HEAD 96298d4)

- `backend/app/dataset_experiments/model.py`
  - `DatasetExperimentAttemptModel` (`:100`): `id`, `experiment_item_id`,
    `attempt_number`, `analysis_run_id`, `launch_requested_at` (nullable),
    `created_at`; `UNIQUE(experiment_item_id, attempt_number)` and
    `UNIQUE(analysis_run_id)`.
  - `DatasetExperimentItemModel` (`:67`), `DatasetExperimentModel` (`:16`).
- `backend/app/dataset_experiments/service.py`
  - G3-A `start_item_attempt` (`:415`), `_claim_queued_item` (`:472`),
    `_next_attempt_number` (`:487`); `_get` (`:35`).
- `backend/app/analysis/service.py`
  - `launch_prepared_run(run_id)` (`:249`): loads the persisted Run; rejects a
    still-staged Run; requires `status == "pending"` and `worker_pid is None`;
    for `remote_gpu` reads the frozen `coordinator_token`; calls
    `provider.launch(...)`; commits `worker_pid`; on exception marks the Run
    `failed` (`error_type="ANALYSIS_FAILED"`, `error_message=str(exc)[:1000]`) and
    raises `PlatformError("ANALYSIS_FAILED", ...)`. Never creates a Run and never
    regenerates the remote token.
  - Docstring currently says `G3 Transaction A COMMIT / G4 Transaction B COMMIT`
    (`:252`–`:255`) — stale after the G3-A/G3-B/G3-C refinement (see §10 audit).
- `backend/app/analyses`/`backend/app/analysis/model.py` — `AnalysisRunModel`
  (`executor`, `status`, `worker_pid`, `execution_metadata_json`).
- `backend/app/remote_execution/recovery.py`
  - `mark_stale_local_cpu_runs_interrupted` (`:22`), `find_orphaned_remote_runs`
    (`:40`), `rotate_coordinator_token` (`:49`),
    `coordinate_orphaned_remote_runs` (`:77`). None reference
    `launch_requested_at`.
- `backend/app/remote_execution/coordinator.py`
  - Token fence at `:106` (`metadata.get("coordinator_token") !=
    self._coordinator_token` → `_StaleFence`); remote fencing is coordinator-token
    based.
- `backend/app/remote_execution/startup.py` — `build_coordinator_metadata`
  (`:13`) freezes the token into the Run metadata at prepare time.
- Confirmed: `launch_requested_at` is referenced only by the
  `DatasetExperimentAttempt` model/read schema and G3-A/G3-B code; no remote
  execution or recovery code uses it.
- Relevant tests: `backend/tests/test_dataset_experiment_attempt.py`,
  `test_dataset_experiment_attempt_remote.py`,
  `test_dataset_experiment_attempt_concurrency.py`,
  `test_dataset_experiment_g3_regression.py`,
  `backend/tests/test_analysis_launch_prepared_run.py`,
  `backend/tests/test_remote_startup_recovery.py`,
  `backend/tests/test_remote_coordinator_fencing.py`,
  `executor_fixtures.py`, `benchmark_fixture.py`.

### Test command conventions

- Single: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`

---

## Resolved Local vs Remote Semantics (spec §7 vs §8)

**Spec §7** globally describes `Attempt.launch_requested_at -> COMMIT ->
launch_prepared_run()` and says a physical launch must never precede the
Transaction B commit.

**Spec §8** then narrows this: “The durable launch-intent fence applies only to
local launches and does not change remote_gpu recovery semantics,” and “Reuse
existing fenced remote recovery semantics for pending/running remote_gpu
AnalysisRuns.”

**Candidate interpretations evaluated:**

- **A — write `launch_requested_at` for both local and remote; only local
  recovery interprets it as the ambiguity fence.**
  Keeps a uniform Transaction B, but writes a column no remote code reads and
  risks a future recovery misreading it as a remote fence — the exact
  “new remote model” §8 forbids.
- **B — only local executors use `launch_requested_at`; `remote_gpu` continues
  entirely through existing coordinator-token/fenced recovery.**
  Matches §8 literally and the current architecture: `launch_requested_at` has no
  remote consumer, and remote recovery is token-based already.

**Resolution: choose B.** Justification from repository evidence:
1. `launch_requested_at` has no reference in any remote execution/recovery code,
   while remote fencing is already implemented via `coordinator_token`
   (`coordinator.py:106`, `recovery.rotate_coordinator_token`).
2. The remote Run's durable execution identity (frozen `coordinator_token`) is
   already committed in **Transaction A** by G3-A (`prepare_run` + Attempt +
   commit); remote recovery re-coordinates pending/running remote Runs.
3. Writing `launch_requested_at` for remote would introduce a second, unused
   intent marker that could be misread as a fence, contradicting §8's “does not
   change remote_gpu recovery semantics”.

Therefore:
- **Local (`local_cpu`, `local_gpu`):** Transaction B is mandatory: claim
  `launch_requested_at` (CAS) and commit before `launch_prepared_run`.
- **Remote (`remote_gpu`):** no `launch_requested_at` write; G3-B validates the
  ownership chain and calls `launch_prepared_run`, reusing the frozen token and
  existing fenced recovery. G4 owns remote ambiguity/recovery.

No contradiction remains after this reading; no STOP is required. If the
architect later prefers interpretation A, it is a localized change (write the
marker for remote too) with no behavior change to remote recovery, because no
remote code reads it.

---

## Transaction B Ordering

Exact durable order for one eligible, G3-A-owned local Attempt:

```
load/validate Experiment -> Item -> Attempt -> AnalysisRun ownership
        ↓
ensure eligible first launch (Run pending, launch_requested_at IS NULL)
        ↓
conditional CAS: UPDATE dataset_experiment_attempts
                 SET launch_requested_at = :now
                 WHERE id = :attempt_id
                   AND experiment_item_id = :item_id
                   AND launch_requested_at IS NULL
        ↓ rowcount == 1
COMMIT Transaction B
        ↓ (only after commit returns)
AnalysisService.launch_prepared_run(run_id)
        ↓
(launch_prepared_run owns its own post-launch result commit)
```

- Transaction A (G3-A) and Transaction B (G3-B) are never combined.
- No `prepare_run`, no new `AnalysisRun`, no Attempt creation.
- A Transaction B commit failure yields no launch and leaves durable G3-A
  ownership intact (`launch_requested_at` still NULL).
- For `remote_gpu`, the Transaction-B block is skipped (interpretation B); the
  launch runs against the already-committed token.

---

## Transaction Ownership

| Step | Owner | Commit boundary |
|---|---|---|
| `prepare_run` + Attempt + Item `running` | G3-A | Transaction A commit |
| `launch_requested_at = now` (local only) | G3-B | Transaction B commit (own) |
| physical `provider.launch` | G2 `launch_prepared_run` | post-launch result commit |
| Run `pending -> running` | inference worker / remote coordinator | worker transaction |
| terminal Run projection to Item, retry, ambiguity recovery, fencing | G3-C / G4 | their own |

---

## State Transition Table

| Object | From | To | Guard | Owner |
|---|---|---|---|---|
| Attempt | `launch_requested_at IS NULL` | `= now` (local) | conditional CAS rowcount == 1 | G3-B |
| Attempt | `launch_requested_at IS NULL` | unchanged (remote) | no fence written (spec §8) | G3-B |
| AnalysisRun | `pending` | `pending` (+`worker_pid` local) | `launch_prepared_run` success | G2/G3-B |
| AnalysisRun | `pending` | `failed` | `provider.launch` exception | G2 `launch_prepared_run` |
| AnalysisRun | `pending` | `running`/terminal | worker/coordinator | worker/G3-C |
| Item | `running` | unchanged | G3-B never projects Run state into Item | G3-B |
| Experiment | `running` | unchanged | G3-B requires `running` | G3-B |

---

## Local Crash-Window Table

| Window | Durable state | Launch outcome | Action |
|---|---|---|---|
| After G3-A commit, before Transaction B | `launch_requested_at NULL`, Run `pending`, no `worker_pid` | no launch attempted | First launch still safe (recovery/G4 may perform it) |
| Transaction B commit fails | `launch_requested_at NULL`, Run `pending` | no launch; no provider call | Fail closed; G3-A ownership intact |
| After Transaction B commit, before/during/after physical launch | `launch_requested_at != NULL`, Run `pending` (or `running`/terminal if worker progressed) | possibly ambiguous | G4 fails closed; requires NEW Attempt + NEW AnalysisRun via Retry Failed |
| Normal | `launch_requested_at != NULL`, Run `pending`, `worker_pid` set | launched | Worker later moves Run to `running`/terminal |

G3-B implements none of the recovery actions; it only guarantees the durable
commit-before-launch ordering.

---

## Concurrency / CAS Analysis

**Local claim primitive (required).** Two callers (e.g. a restarted coordinator)
may both observe `launch_requested_at IS NULL`. A single conditional UPDATE
guards first launch:

```sql
UPDATE dataset_experiment_attempts
SET launch_requested_at = :now
WHERE id = :attempt_id
  AND experiment_item_id = :item_id
  AND launch_requested_at IS NULL
```

Only a `rowcount == 1` claimer may proceed to `launch_prepared_run`. A loser
receives `rowcount == 0` and raises `DATASET_EXPERIMENT_INVARIANT_VIOLATION`
without any provider call. In-memory flags are not used. As in G3-A, a genuine
simultaneous SQLite writer race may surface `SQLITE_BUSY`/`OperationalError`
instead of `rowcount == 0`; G3-B fails closed and rolls back in that case and does
not retry, reinterpret the BUSY as a successful claim, or add
`BEGIN IMMEDIATE`/WAL/busy-timeout PRAGMAs.

**Remote.** No `launch_requested_at` CAS (interpretation B). Remote double-launch
is outside G3-B's scope: V1 has one coordinator per experiment, and existing
coordinator-token fencing plus G4 recovery govern remote ambiguity. G3-B does not
alter `remote_execution/*`.

---

## Same-Session Analysis

`launch_item_attempt` requires `analysis_service.session is self.session`
(enforced and tested like G3-A). Transaction B is committed by
`DatasetExperimentService` on that Session; `launch_prepared_run` then owns its
own post-launch commit on the same Session. A mismatch fails closed with
`DATASET_EXPERIMENT_INVARIANT_VIOLATION` and performs no launch.

Post-commit reads (`self.session.refresh(attempt)`) are best-effort: a read
failure after the Transaction B commit does not mean the commit rolled back. The
database is the source of truth. G3-B does not refresh between the Transaction B
commit and the physical launch.

---

## Failure Semantics

| Failure | Durable effect | Launch |
|---|---|---|
| Ownership chain invalid / missing rows | nothing committed | none |
| Experiment not `running` | nothing committed | none |
| Run not `pending` / already launched | nothing committed | none |
| Transaction B CAS `rowcount == 0` (already requested / lost race) | loser commits nothing | none |
| Transaction B `commit()` fails | rollback; `launch_requested_at` NULL | none |
| `launch_prepared_run` fails | `launch_requested_at` remains set; Run becomes `failed` (`ANALYSIS_FAILED`, raw text in `error_message`); Item stays `running` until G3-C reconciliation | attempted once, no retry |

G3-B never clears `launch_requested_at`, never resets the Attempt to “never
launched”, never creates a replacement Run/Attempt, and never auto-retries.

---

## G3-B vs G3-C / G4 Boundary

- **G3-B (this plan):** durable local launch intent (Transaction B) + physical
  first launch via the G2 primitive; ownership-chain validation; single-claim
  CAS; remote first launch via existing semantics.
- **G3-C (deferred):** worker/job manager; Experiment `pending->running`;
  bounded concurrency; deterministic scheduling (calls G3-A + G3-B); Item
  reconciliation from `AnalysisRun` authority; best-effort continuation.
- **G4 (deferred):** Retry Failed; local ambiguity fail-closed; coordinator-token
  fencing/restart; duplicate-active-Attempt recovery; remote recovery.
- G3-B never starts a worker/coordinator, never schedules, never transitions the
  Experiment, never reconciles Items, never retries.

---

## Proposed G3-B Interfaces

Add to `DatasetExperimentService` (`backend/app/dataset_experiments/service.py`):

```python
def launch_item_attempt(
    self,
    *,
    experiment_id: str,
    item_id: str,
    attempt_id: str,
    analysis_service: "AnalysisService",
) -> DatasetExperimentAttemptModel:
    """G3-B: durably record local launch intent, then physically launch once."""

def _claim_launch_intent(self, *, attempt_id: str, item_id: str, requested_at) -> int:
    """Conditional CAS launch_requested_at IS NULL -> requested_at; returns rowcount."""
```

Documentation-only correction to `AnalysisService.launch_prepared_run`
(`backend/app/analysis/service.py`): replace the stale
`G3 Transaction A COMMIT / G4 Transaction B COMMIT` lines with
`G3-A Transaction A COMMIT / G3-B Transaction B COMMIT`. No behavior change.

---

## Task 1 — Local Durable First Launch (behavior)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`
- Create: `backend/tests/test_dataset_experiment_launch.py`

**Interfaces:** consumes G3-A ownership; `AnalysisService.launch_prepared_run`; `DatasetExperimentAttemptModel.launch_requested_at`.

### Exact production code (add)

Module-top imports: add `from datetime import datetime, timezone` and
`from app.analysis.model import AnalysisRunModel`.

```python
    # ---------- Transaction B + durable first launch (G3-B) ----------

    def launch_item_attempt(self, *, experiment_id, item_id, attempt_id, analysis_service):
        """G3-B: durably record local launch intent, then physically launch once.

        Validates the ownership chain Experiment -> Item -> Attempt -> AnalysisRun.
        For local executors it commits Transaction B
        (Attempt.launch_requested_at = now) BEFORE invoking the sealed G2
        AnalysisService.launch_prepared_run primitive. For remote_gpu it skips the
        local fence and reuses the existing coordinator-token/fenced semantics
        (spec section 8). Never prepares/creates a Run, never retries, never
        recovers.
        """
        if analysis_service is None or getattr(analysis_service, "session", None) is not self.session:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisService must share the DatasetExperimentService Session.",
                409,
            )

        experiment = self._get(experiment_id)
        if experiment.status != "running":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Experiment is not running; G3-B only launches a running experiment.",
                409,
            )

        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404
            )
        if item.experiment_id != experiment.id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Item does not belong to the expected experiment.",
                409,
            )

        attempt = self.session.get(DatasetExperimentAttemptModel, attempt_id)
        if attempt is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ATTEMPT_NOT_FOUND",
                "Dataset experiment attempt was not found.",
                404,
            )
        if attempt.experiment_item_id != item.id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Attempt does not belong to the expected item.",
                409,
            )

        run = self.session.get(AnalysisRunModel, attempt.analysis_run_id)
        if run is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Attempt does not reference a persisted analysis run.",
                409,
            )
        if run.recording_id != item.recording_id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisRun does not match the attempt's recording.",
                409,
            )
        if run.status != "pending":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Only a pending analysis run can be first-launched.",
                409,
            )

        if run.executor != "remote_gpu":
            now = datetime.now(timezone.utc)
            try:
                with self.session.no_autoflush:
                    claimed = self._claim_launch_intent(
                        attempt_id=attempt_id, item_id=item_id, requested_at=now
                    )
                    if claimed != 1:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Launch intent is already recorded or the attempt is not eligible.",
                            409,
                        )
                self.session.commit()  # Transaction B (local only)
            except Exception:
                self.session.rollback()
                raise

        analysis_service.launch_prepared_run(run.id)
        self.session.refresh(attempt)
        return attempt

    def _claim_launch_intent(self, *, attempt_id, item_id, requested_at):
        result = self.session.execute(
            update(DatasetExperimentAttemptModel)
            .where(
                DatasetExperimentAttemptModel.id == attempt_id,
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                DatasetExperimentAttemptModel.launch_requested_at.is_(None),
            )
            .values(launch_requested_at=requested_at)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)
```

### Exact tests — `backend/tests/test_dataset_experiment_launch.py`

Self-contained; re-declare the G3-A helpers (`G3LocalPipeline`, `_seed_dataset`,
`_services`, `_experiment`, `_item`) verbatim from
`backend/tests/test_dataset_experiment_attempt.py`, add an ownership helper, then:

```python
def _owned_attempt(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )
    return session, ds, analysis, provider, experiment, item, attempt


def _launch(ds, experiment, item, attempt, analysis):
    return ds.launch_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        attempt_id=attempt.id, analysis_service=analysis,
    )


def test_local_launch_commits_intent_before_physical_launch(client, monkeypatch):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    events = []
    real_commit = session.commit
    real_launch = analysis.launch_prepared_run

    def spy_commit():
        events.append("commit")
        return real_commit()

    def spy_launch(run_id):
        with client.app.state.database.session_factory() as fresh:
            assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is not None
        events.append("launch")
        return real_launch(run_id)

    monkeypatch.setattr(session, "commit", spy_commit)
    monkeypatch.setattr(analysis, "launch_prepared_run", spy_launch)

    launched = _launch(ds, experiment, item, attempt, analysis)

    assert events[0] == "commit"      # Transaction B committed first
    assert events[1] == "launch"      # physical launch second
    assert launched.launch_requested_at is not None
    assert provider.launches == [(launched.analysis_run_id, None)]  # exactly one local launch


def test_local_launch_records_intent_and_worker_pid(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    _launch(ds, experiment, item, attempt, analysis)
    with client.app.state.database.session_factory() as fresh:
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_attempt.launch_requested_at is not None
        assert stored_run.status == "pending"
        assert stored_run.worker_pid is not None


def test_transaction_b_commit_failure_does_not_launch(client, monkeypatch):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    launches = []
    monkeypatch.setattr(analysis, "launch_prepared_run", lambda run_id: launches.append(run_id))

    def boom():
        raise RuntimeError("transaction b commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        _launch(ds, experiment, item, attempt, analysis)
    assert launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None
        assert fresh.get(AnalysisRunModel, attempt.analysis_run_id).status == "pending"


def test_already_requested_local_attempt_cannot_first_launch_again(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    _launch(ds, experiment, item, attempt, analysis)
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == [(attempt.analysis_run_id, None)]  # no second launch


def test_launch_requires_same_session(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    other_session = client.app.state.database.session_factory()
    other_analysis = AnalysisService(
        other_session, PipelineRegistry([G3LocalPipeline()]), client.app.state.job_manager,
        executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, other_analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None


def test_launch_rejects_ownership_mismatch(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id="other-item",
            attempt_id=attempt.id, analysis_service=analysis,
        )
    assert exc.value.code == "DATASET_EXPERIMENT_ITEM_NOT_FOUND"
    assert provider.launches == []


def test_launch_rejects_non_pending_run(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    run = session.get(AnalysisRunModel, attempt.analysis_run_id)
    run.status = "completed"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []


def test_launch_rejects_pending_experiment(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    experiment.status = "pending"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []


class RaisingLocalProvider(FakeProvider):
    def launch(self, run_id, *, coordinator_token):
        raise RuntimeError("boom")


def test_launch_failure_preserves_intent_and_failed_run(client):
    _seed_dataset(client)
    raising = RaisingLocalProvider("local_cpu")
    session = client.app.state.database.session_factory()
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": raising})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(session, registry, client.app.state.job_manager,
                               executor_registry=executor_registry)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                    analysis_service=analysis)

    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "ANALYSIS_FAILED"
    with client.app.state.database.session_factory() as fresh:
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_attempt.launch_requested_at is not None  # intent preserved
        assert stored_run.status == "failed"
        assert stored_run.error_type == "ANALYSIS_FAILED"
        assert stored_run.error_message == "boom"
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_launch.py` exactly as above (without the placeholder line; the raising-provider source is `RaisingLocalProvider`).
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_launch.py -v
```
Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'launch_item_attempt'`.
- [ ] **Implement minimal code:** add `launch_item_attempt`, `_claim_launch_intent`, and the `datetime`/`AnalysisRunModel` imports exactly as above.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_analysis_launch_prepared_run.py -q
```
- [ ] **Commit checkpoint:** `feat: add durable local first launch seam`

---

## Task 2 — Real two-Session launch-intent race (verification; production unchanged)

**Files:**
- Create: `backend/tests/test_dataset_experiment_launch_concurrency.py`.
- Production: none expected.

Re-declare the Task 1 helpers locally. Add:

```python
def test_claim_launch_intent_real_stale_session(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    assert attempt.launch_requested_at is None

    now = datetime.now(timezone.utc)
    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at = now
        winner.commit()

    # Session A still has stale NULL ORM state: the real CAS must lose.
    assert attempt.launch_requested_at is None
    assert ds._claim_launch_intent(attempt_id=attempt.id, item_id=item.id, requested_at=now) == 0


def test_launch_item_attempt_real_stale_session_does_not_launch(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)

    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at = datetime.now(timezone.utc)
        winner.commit()

    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []  # loser never reaches physical launch

    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is not None
```

### Steps

- [ ] **Write test** `backend/tests/test_dataset_experiment_launch_concurrency.py`.
- [ ] **Run verification command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_launch_concurrency.py -v
```
Expected PASS after Task 1; a failure is a real defect — STOP and report. The real CAS must be exercised against the database (no monkeypatched rowcount).
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_launch.py \
  backend/tests/test_dataset_experiment_attempt_concurrency.py -q
```
- [ ] **Commit checkpoint:** `test: verify launch intent single-claim cas`

---

## Task 3 — Remote first-launch semantics (verification; production unchanged)

**Files:**
- Create: `backend/tests/test_dataset_experiment_launch_remote.py`.
- Production: none expected.

Re-declare the remote helpers from
`backend/tests/test_dataset_experiment_attempt_remote.py` (`G3RemotePipeline`,
`_Probe`, `_Store`, `_cert`, `_seed_remote_dataset`, `_services_remote`,
`_remote_experiment`, `_queued_item`) and add:

```python
def test_remote_launch_uses_frozen_token_without_local_fence(client, tmp_path):
    data_root = tmp_path / "data"
    _seed_remote_dataset(client, data_root)
    session, ds, analysis, provider = _services_remote(client, data_root)
    experiment = _remote_experiment(ds)
    item = _queued_item(session, experiment.id)
    attempt = ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                    analysis_service=analysis)

    frozen_token = session.get(
        AnalysisRunModel, attempt.analysis_run_id
    ).execution_metadata_json["coordinator_token"]

    launched = ds.launch_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                      attempt_id=attempt.id, analysis_service=analysis)

    assert launched.launch_requested_at is None  # remote does not write the local fence
    assert provider.launches == [(attempt.analysis_run_id, frozen_token)]  # launched once
    with client.app.state.database.session_factory() as fresh:
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_run.status == "pending"
        assert stored_run.worker_pid is not None


def test_remote_double_launch_fails_closed(client, tmp_path):
    data_root = tmp_path / "data"
    _seed_remote_dataset(client, data_root)
    session, ds, analysis, provider = _services_remote(client, data_root)
    experiment = _remote_experiment(ds)
    item = _queued_item(session, experiment.id)
    attempt = ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                    analysis_service=analysis)
    ds.launch_item_attempt(experiment_id=experiment.id, item_id=item.id,
                           attempt_id=attempt.id, analysis_service=analysis)
    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(experiment_id=experiment.id, item_id=item.id,
                               attempt_id=attempt.id, analysis_service=analysis)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"  # G2 guard (worker_pid set)
    assert len(provider.launches) == 1
```

### Steps

- [ ] **Write test** `backend/tests/test_dataset_experiment_launch_remote.py` exactly as above.
- [ ] **Run verification command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_launch_remote.py -v
```
Expected PASS after Task 1; failure is a real defect — STOP and report.
- [ ] **Focused regressions (remote semantics unchanged):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt_remote.py \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_remote_coordinator_fencing.py -q
```
- [ ] **Commit checkpoint:** `test: verify remote first launch semantics`

---

## Task 4 — Docstring correction + regression matrix + full verification (verification only)

**Files:**
- Modify (documentation only): `backend/app/analysis/service.py` — update the
  `launch_prepared_run` docstring ordering lines.
- Create: `backend/tests/test_dataset_experiment_launch_regression.py`.
- No behavior change.

### Documentation-only correction

In `AnalysisService.launch_prepared_run`, replace:

```
            G3 Transaction A COMMIT
            G4 Transaction B COMMIT
```

with:

```
            G3-A Transaction A COMMIT
            G3-B Transaction B COMMIT
```

This is documentation-only; `launch_prepared_run` behavior and all G2 tests are
unchanged.

### Exact regression guards

```python
from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
)

_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}
_ATTEMPT_COLUMNS = {
    "id", "experiment_item_id", "attempt_number", "analysis_run_id",
    "launch_requested_at", "created_at",
}


def test_analysis_run_schema_unchanged_by_g3b():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_attempt_schema_unchanged_by_g3b():
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS


def test_launch_prepared_run_docstring_numbering_is_current():
    doc = AnalysisService.launch_prepared_run.__doc__
    assert "G3-A Transaction A COMMIT" in doc
    assert "G3-B Transaction B COMMIT" in doc
    assert "G4 Transaction B COMMIT" not in doc


def test_dataset_experiment_service_does_not_call_provider_launch_directly():
    import inspect

    from app.dataset_experiments import service as dataset_service

    source = inspect.getsource(dataset_service)
    assert "provider.launch(" not in source
    assert "launch_prepared_run(" in source
```

### Steps

- [ ] **Apply the documentation-only docstring correction** in `backend/app/analysis/service.py`.
- [ ] **Add regression guards** `backend/tests/test_dataset_experiment_launch_regression.py`.
- [ ] **Run guards (expected PASS):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_launch_regression.py -v
```
- [ ] **Run the complete G3-B suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_launch.py \
  backend/tests/test_dataset_experiment_launch_concurrency.py \
  backend/tests/test_dataset_experiment_launch_remote.py \
  backend/tests/test_dataset_experiment_launch_regression.py -v
```
- [ ] **Run G1 + G2 + G3-A regression:**
```bash
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
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py \
  backend/tests/test_analysis_launch_prepared_run.py \
  backend/tests/test_analysis_create_run_compat.py \
  backend/tests/test_analysis_seam_regression.py \
  backend/tests/test_remote_source_hash.py \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_remote_coordinator_fencing.py -q
```
- [ ] **Run full backend suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```
Require 0 failed / 0 errors (G3-A sealed baseline: `1456 passed, 28 skipped`; G3-B must not reduce passed or introduce failures). If the tool times out, rerun in a detached session until pytest prints a final summary; do not infer PASS from a partial run.
- [ ] **Commit checkpoint:** `test: add phase G G3B regression matrix`

---

## Test Matrix

| Test file | Covers |
|---|---|
| `test_dataset_experiment_launch.py` | local Transaction B before launch; single launch; commit-failure no launch; duplicate fails; same-session; ownership mismatch; non-pending Run; pending Experiment; launch failure preserves intent + failed Run |
| `test_dataset_experiment_launch_concurrency.py` | real two-Session stale launch-intent CAS (single claim) |
| `test_dataset_experiment_launch_remote.py` | remote frozen-token launch without local fence; remote double-launch fails closed |
| `test_dataset_experiment_launch_regression.py` | schema unchanged; docstring numbering; no direct `provider.launch` in dataset service |
| G1/G2/G3-A suites | no regressions in ownership/prepare/launch/remote recovery |
| full backend suite | no wider regressions |

---

## Self-Review

1. Local Transaction B commits `launch_requested_at` before `launch_prepared_run`; commit-before-launch proven by spy test.
2. Transaction B commit failure → rollback, zero launches, ownership intact.
3. No `prepare_run`, no new `AnalysisRun`, no Attempt creation in G3-B.
4. Physical launch uses only the G2 `launch_prepared_run` primitive; no direct `provider.launch` in dataset orchestration.
5. Single-claim CAS prevents two local first-launch callers; a real two-Session test proves the loser never launches.
6. Remote does not write `launch_requested_at` and reuses frozen coordinator-token/fenced recovery (spec §8 resolution B).
7. Same-Session requirement enforced; mismatch fails closed.
8. Launch failure preserves durable intent and G2 failed-Run semantics; Item left for G3-C reconciliation; no retry/clear/replacement.
9. Ownership chain `Experiment -> Item -> Attempt -> AnalysisRun` validated; no historical/match-any Run selection.
10. No schema/migration/canonical/fingerprint/AssetManifest/ModelRelease/certificate/science change.
11. Docstring numbering corrected (documentation-only).
12. No G3-C/G4 behavior enters G3-B.
13. Each task has exact files, interfaces, tests, commands, and checkpoints.
14. No TODO/TBD/XXX/hand-wavy placeholders; no cross-test-module imports.

---

## Out-of-Scope Guardrails During Implementation

- If G3-B appears to need a new Result/Schema/migration, `prepare_run`, retry,
  ambiguity recovery, coordinator/worker, Experiment status transition, or Item
  reconciliation, STOP — that is G3-C/G4/G5 or frozen.
- If local commit-before-launch cannot be guaranteed, STOP.
- If the local/remote interpretation B is contradicted by repository evidence
  during implementation, STOP and report the exact contradiction.
