# Phase G G3 Transaction A Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal:** Implement Phase G **DatasetExperiment Transaction A — durable
execution ownership**. For one eligible queued `DatasetExperimentItem`, atomically
commit a prepared `AnalysisRun` (via the sealed G2 `AnalysisService.prepare_run`),
a new `DatasetExperimentAttempt` binding that Item to that exact Run, and the
Item `queued -> running` transition, in one caller-owned transaction. G3 stops at
that commit: it never writes `launch_requested_at`, never launches, and never
starts a coordinator/worker.

**Architecture:** A single additive method on the sealed G1
`DatasetExperimentService` (`start_item_attempt`) plus two small private helpers
(`_claim_queued_item`, `_next_attempt_number`). It reuses the G1
`revalidate_frozen_identity` seam and the G2 `AnalysisService.prepare_run` seam on
the same SQLAlchemy `Session`. It adds no table, column, migration, or schema; it
does not launch. Concurrency safety is provided by a conditional (`queued ->
running`) `UPDATE` compare-and-swap on the Item, serialized by SQLite's
single-writer model, with the existing
`UNIQUE(experiment_item_id, attempt_number)` constraint as a backstop.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
SQLAlchemy 2.x, SQLite (rollback-journal, default engine, no WAL/busy_timeout
PRAGMAs, `expire_on_commit=False`), pytest 9. No GPU, no SSH, no torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
(§4.2/§4.3 data model, §7 Transaction A seam, §8 launch ambiguity, §14.2 error
families, §15 invariants, §20 G3).

**Sealed dependencies:**
- G1: `docs/superpowers/plans/2026-09-12-phase-g-g1-persistence-frozen-experiment.md`
  (models, migration, creation, `revalidate_frozen_identity`).
- G2: `docs/superpowers/plans/2026-09-13-phase-g-g2-analysis-prepare-launch-seam.md`
  (`AnalysisService.prepare_run` caller-owned; `launch_prepared_run` deferred to G4).

**Base:** `feature/m9-2-implementation @ b016b648c3db422f807b44057698eabd0818d77f`.

---

## Global Constraints

1. **G3 ends at Transaction A COMMIT.** No `launch_requested_at`, no
   `launch_prepared_run`, no provider `.launch`, no worker, no coordinator, no
   retry policy, no evaluation, no REST/frontend.
2. **Single transaction.** prepared `AnalysisRun` + new `DatasetExperimentAttempt`
   + Item `running` (+ any newly staged source-data hash cache) become durable
   together or not at all, in one `Session.commit()`.
3. **Same Session.** `start_item_attempt` requires the passed `AnalysisService`
   to share the exact `Session` object; otherwise it fails closed. No cross-session
   staging.
4. **Reuse, do not duplicate.** Frozen-identity checks use the G1
   `revalidate_frozen_identity` seam; run construction uses the G2
   `AnalysisService.prepare_run` seam. No independent provenance reconstruction.
5. **No persisted counter.** `attempt_number = max(existing) + 1` derived from
   authoritative Attempt rows under the Item claim.
6. **Item precondition is `queued`.** Completed items are never restarted;
   `failed -> queued` is explicit Retry Failed behavior (G4), not implicit G3.
7. **No schema change.** `backend/app/dataset_experiments/model.py`, migrations,
   `analysis_runs` schema, canonical hashing, fingerprinting, AssetManifest V1,
   ModelRelease authority, and certificate semantics are untouched.
8. **Production scope is one file:** `backend/app/dataset_experiments/service.py`.
9. **Same-session autoflush discipline.** The item claim is issued inside
   `Session.no_autoflush` so no staged Run/Attempt/hash is flushed before the
   compare-and-swap; prepare performs no commit/flush/rollback/launch (G2).

---

## Repository Mapping (verified against HEAD b016b64)

- `backend/app/dataset_experiments/model.py`
  - `DatasetExperimentItemModel` (`:67`): `id`, `experiment_id`,
    `manifest_order`, `recording_id`, `status` (default `queued`, indexed),
    `last_error_type`, `last_error_message`, timestamps; `UNIQUE(experiment_id,
    recording_id)` and `UNIQUE(experiment_id, manifest_order)` (`:69`–`:72`).
  - `DatasetExperimentAttemptModel` (`:100`): `id`, `experiment_item_id`,
    `attempt_number`, `analysis_run_id`, `launch_requested_at` (nullable),
    `created_at`; `UNIQUE(experiment_item_id, attempt_number)` and
    `UNIQUE(analysis_run_id)` (`:102`–`:105`).
- `backend/app/dataset_experiments/service.py`
  - `__init__(session, registry, model_release_store, executor_registry)` (`:27`).
  - `_get` (`:35`), read models (`:41`–`:127`).
  - `_manifest_preview` (`:131`), `_resolve_definition` (`:136`),
    `_resolve_release` (`:153`), `_validate_execution` (`:170`).
  - `create_experiment` (`:185`) — pattern for an atomic
    `try/except: rollback; raise` commit.
  - `revalidate_frozen_identity` (`:264`) — read-only G3+ seam; raises
    `DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED` /
    `DATASET_EXPERIMENT_INVARIANT_VIOLATION` /
    `DATASET_EXPERIMENT_ORCHESTRATION_FAILED`.
- `backend/app/analysis/service.py`
  - `prepare_run(*, recording_id, pipeline_id, executor, parameters,
    model_release_id=None) -> AnalysisRunModel` (`:110`) — validates, resolves
    release, checks availability, freezes provenance, `session.add(run)`, no
    commit/rollback/flush/launch. Run id is `run_<uuid4>` (`:163`/`:183`).
  - `_prepare_local_run` (`:162`), `_prepare_remote_run` (`:182`),
    `_freeze_remote_provenance` (`:206`).
  - `launch_prepared_run` (`:249`) — G4 only; G3 must not call it.
- `backend/app/remote_execution/source_hash.py` — G2 made
  `resolve_source_data_sha256` transaction-neutral (`:45`); it stages
  `recording.source_data_sha256` and returns.
- `backend/app/remote_execution/identity.py` — G2 reordered
  `resolve_remote_recording_identity` (`:39`) to `SELECT` GroundTruth before
  staging the source hash.
- `backend/app/db/session.py` — SQLite engine, `expire_on_commit=False` (`:13`);
  no PRAGMA/WAL/busy_timeout configuration.
- Relevant test owners: `backend/tests/test_dataset_experiment_models.py`,
  `test_dataset_experiment_read_model.py`, `test_dataset_experiment_creation.py`,
  `test_dataset_experiment_revalidation.py`, `test_benchmark_membership.py`,
  `test_analysis_prepare_local.py`, `test_analysis_prepare_remote.py`,
  `test_analysis_launch_prepared_run.py`, `test_analysis_create_run_compat.py`,
  `test_remote_source_hash.py`, `executor_fixtures.py`, `benchmark_fixture.py`.

### Test command conventions

- Single: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`

---

## Transaction Ownership

| Layer | Owns commit/rollback? | Owns physical launch? |
|---|---|---|
| `AnalysisService.prepare_run` (G2) | **No** | **No** |
| `DatasetExperimentService.start_item_attempt` (G3) | **Yes — Transaction A only** | **No** |
| `DatasetExperimentService.create_experiment` (G1) | Yes (creation) | No |
| G4 (`launch_requested_at` + `launch_prepared_run`) | Yes — Transaction B | Yes |

`start_item_attempt` is the "caller" of `prepare_run`: it is the orchestration
layer that owns and commits Transaction A. A failure anywhere inside Transaction A
rolls back the caller's Session, discarding the staged `AnalysisRun`, the
source-hash cache mutation (if any), the `DatasetExperimentAttempt`, and the Item
claim.

---

## State Transition Table

| Object | From | To | Guard | Owner |
|---|---|---|---|---|
| Experiment | `pending`/`running` | unchanged | must be in `{pending, running}` else invariant violation | G3 |
| Item | `queued` | `running` | conditional CAS `UPDATE ... WHERE status='queued'`, rowcount == 1 | G3 |
| Item | `failed` | `queued` | not in G3 | G4 Retry Failed |
| Item | `running`/`completed` | (reject) | `start_item_attempt` raises invariant violation | G3 |
| Attempt | (absent) | new row, `launch_requested_at = NULL` | `attempt_number = max+1`, unique | G3 |
| Attempt | `launch_requested_at NULL` | `now()` | not in G3 | G4 |
| AnalysisRun | (absent) | `pending` | via G2 `prepare_run` | G2 (staged) / G3 (commit) |
| AnalysisRun | `pending` | `running`/terminal | not in G3 | G4 + worker |

---

## Concurrency / Attempt-Number Audit

**Writer model.** The production engine is SQLite with the default rollback
journal and no PRAGMAs (`db/session.py`). SQLite permits exactly one writer at a
time; a write statement acquires the reserved lock and serializes against other
writers. There is no `SELECT ... FOR UPDATE`.

**Chosen V1 mechanism (no new counter).**
1. A transactional compare-and-swap claims the Item:
   `UPDATE dataset_experiment_items SET status='running'
    WHERE id=:item_id AND experiment_id=:experiment_id AND status='queued'`;
   the method requires `rowcount == 1`.
2. `attempt_number = max(existing attempt_number for this item) + 1` is derived
   **after** the claim, inside the same transaction/no-autoflush block.
3. The existing `UNIQUE(experiment_item_id, attempt_number)` and
   `UNIQUE(analysis_run_id)` constraints are defense-in-depth backstops.

**Why this prevents double-start.** Two concurrent transactions that both read
the Item as `queued` can both stage a Run (no writes) but only one can win the
CAS: the first acquires the SQLite write lock and updates the row; the second
blocks on the write lock and, on resume, matches zero rows (`status` is now
`running`), so it raises `DATASET_EXPERIMENT_INVARIANT_VIOLATION` and rolls back.
Because the winner holds the Item write claim for the rest of Transaction A, no
second claimer can derive an `attempt_number` for the same Item concurrently, so
`max+1` cannot collide in normal operation; the unique constraint would reject a
collision if one somehow occurred.

**Deliberate reordering note.** The spec's Transaction A logical order is
`prepare_run -> add Attempt -> Item.status=running -> COMMIT`. G3 expresses the
Item transition as the conditional CAS and issues it after staging the Attempt,
inside the same single transaction. The committed content is identical
(Run + Attempt + Item running); the CAS is only the concurrency guard.

**Lock duration.** Availability probes (local interpreter check / remote SSH
probe) run inside `prepare_run` **before** the CAS, so no SQLite write lock is
held during a probe. The write lock is held only from the CAS through commit
(Attempt + Run flush). The single-coordinator V1 model (spec §9) means contention
is limited to crash/restart overlap, which G4 fences with `coordinator_token`.

**Busy behavior.** If a competing writer holds the lock longer than the SQLite
`timeout` (default 5s), the CAS raises `OperationalError`; the method's
`except: rollback; raise` fails closed with no durable ownership. This is
acceptable V1 behavior and is documented, not retried.

**Session identity.** `AnalysisService.session is DatasetExperimentService.session`
must hold; otherwise atomicity is impossible and `start_item_attempt` fails closed
with `DATASET_EXPERIMENT_INVARIANT_VIOLATION`.

---

## Crash / Failure Semantics

| Failure point | Durable result | Item state | Launch |
|---|---|---|---|
| `revalidate_frozen_identity` drift | nothing staged (read-only) | `queued` | none |
| Item not `queued` / not found / wrong experiment | nothing staged | unchanged | none |
| `prepare_run` validation/availability/certificate failure | rollback discards any staged hash/Run | `queued` | none |
| CAS `rowcount != 1` (lost race) | rollback discards staged hash/Run/Attempt | `queued` (winner sets `running`) | none |
| `commit()` failure (incl. unique-constraint backstop) | rollback; nothing durable | `queued` | none |
| process crash before commit | SQLite rolls back the open transaction | `queued` | none |
| process crash after commit | Run `pending`, Attempt present, `launch_requested_at NULL`, Item `running` | `running` | none (G4 recovery owns first launch) |

Invariant: a committed `AnalysisRun` created by G3 always has a committed
`DatasetExperimentAttempt` owner; a committed Attempt always binds an Item that is
`running`; `launch_requested_at` is always `NULL` after G3.

---

## G3 vs G4 Boundary

- **G3 (this plan):** `prepare_run` + Attempt + Item `running` + source-hash cache,
  one commit; `launch_requested_at` stays `NULL`.
- **G4 (deferred):** Transaction B (`Attempt.launch_requested_at = now()` commit),
  then `AnalysisService.launch_prepared_run(run_id)` physical launch, local
  launch-ambiguity fail-closed, coordinator token fencing, restart recovery, Retry
  Failed, duplicate-active-attempt recovery.
- **G5 (deferred):** DatasetEvaluation creation + evaluation lifecycle + REST.
- G3 never calls `launch_prepared_run`, `provider.launch`, `job_manager.start`,
  or the coordinator launcher.

---

## Interfaces

Add to `DatasetExperimentService` (no constructor change):

```python
def start_item_attempt(
    self,
    *,
    experiment_id: str,
    item_id: str,
    analysis_service: "AnalysisService",
) -> DatasetExperimentAttemptModel:
    """Transaction A: durably bind one queued Item to a newly prepared Run.

    Revalidates frozen identity, calls the G2 AnalysisService.prepare_run seam on
    the SAME Session, derives the next attempt number from Attempt history,
    claims the Item queued->running, persists the Attempt, and commits once.
    Never writes launch_requested_at and never launches.
    """

def _claim_queued_item(self, *, item_id: str, experiment_id: str) -> int:
    """Conditional compare-and-swap queued->running; returns UPDATE rowcount."""

def _next_attempt_number(self, item_id: str) -> int:
    """max(existing attempt_number) + 1, derived from authoritative Attempt rows."""
```

`AnalysisService` is referenced only under `TYPE_CHECKING` to keep the module
import graph unchanged; the runtime contract is duck-typed
(`analysis_service.session`, `analysis_service.prepare_run`).

---

## Task 1 — Transaction A ownership seam (local)

**Files:**
- Modify: `backend/app/dataset_experiments/service.py`
- Create: `backend/tests/test_dataset_experiment_attempt.py`

**Interfaces:**
- Consumes: `revalidate_frozen_identity`, `AnalysisService.prepare_run`, `DatasetExperimentAttemptModel`, `DatasetExperimentItemModel`, `UNIQUE(experiment_item_id, attempt_number)`.
- Produces: `start_item_attempt`, `_claim_queued_item`, `_next_attempt_number`.

### Exact production code (add; import `update` and `TYPE_CHECKING`)

Module top of `backend/app/dataset_experiments/service.py` (change the existing
`from sqlalchemy import func, select` import and add the guarded type import):

```python
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

if TYPE_CHECKING:
    from app.analysis.service import AnalysisService
```

Then, inside the `DatasetExperimentService` class body, add:

```python
    def start_item_attempt(
        self,
        *,
        experiment_id,
        item_id,
        analysis_service,
    ):
        """Transaction A: durably bind one queued Item to a newly prepared Run.

        Revalidates frozen identity, stages a pending AnalysisRun through the G2
        AnalysisService.prepare_run seam on the SAME Session, derives the next
        attempt number from authoritative Attempt history, claims the Item
        queued->running with a conditional UPDATE, persists the Attempt, and
        commits exactly once. Never writes launch_requested_at and never launches.
        """
        if analysis_service is None or getattr(analysis_service, "session", None) is not self.session:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisService must share the DatasetExperimentService Session.",
                409,
            )

        experiment = self.revalidate_frozen_identity(experiment_id)

        if experiment.status not in {"pending", "running"}:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Experiment is not in a schedulable state.",
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
                "Item does not belong to the frozen experiment.",
                409,
            )
        if item.status != "queued":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Only a queued item can start a new attempt.",
                409,
            )
        recording_id = item.recording_id

        try:
            run = analysis_service.prepare_run(
                recording_id=recording_id,
                pipeline_id=experiment.plugin_id,
                executor=experiment.executor,
                parameters=dict(experiment.parameters_json or {}),
                model_release_id=experiment.model_release_id,
            )

            with self.session.no_autoflush:
                attempt_number = self._next_attempt_number(item_id)
                attempt = DatasetExperimentAttemptModel(
                    id=f"expattempt_{uuid4().hex}",
                    experiment_item_id=item_id,
                    attempt_number=attempt_number,
                    analysis_run_id=run.id,
                )
                self.session.add(attempt)

                claimed = self._claim_queued_item(item_id=item_id, experiment_id=experiment.id)
                if claimed != 1:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Item is no longer queued; another start won or it is not eligible.",
                        409,
                    )
                self.session.expire(item)

            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        self.session.refresh(attempt)
        return attempt

    def _claim_queued_item(self, *, item_id, experiment_id):
        result = self.session.execute(
            update(DatasetExperimentItemModel)
            .where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "queued",
            )
            .values(status="running")
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    def _next_attempt_number(self, item_id):
        current = self.session.scalar(
            select(func.max(DatasetExperimentAttemptModel.attempt_number)).where(
                DatasetExperimentAttemptModel.experiment_item_id == item_id
            )
        )
        return int(current or 0) + 1
```

No explicit `flush()`: `run.id`, `attempt.id`, `item_id`, and `experiment.id` are
Python strings already available, and SQLAlchemy's unit of work orders the
`analysis_runs` insert before the `dataset_experiment_attempts` insert using the
declared FK.

### Exact tests — `backend/tests/test_dataset_experiment_attempt.py`

```python
from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel

from executor_fixtures import FakeProvider, FakeRegistry


class G3LocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_local", name="G3 Local", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification", executors_supported=("local_cpu",),
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


def _seed_dataset(client, count=2):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _services(client, *, provider=None):
    session = client.app.state.database.session_factory()
    provider = provider or FakeProvider("local_cpu")
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager, executor_registry=executor_registry,
    )
    return session, ds, analysis, provider


def _experiment(ds):
    return ds.create_experiment(
        name="g3", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_local", plugin_version="1.0",
        executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )


def _item(session, experiment_id, order=0):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=order
    ).one()


def test_start_item_attempt_binds_run_attempt_and_running_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )

    assert attempt.attempt_number == 1
    assert attempt.launch_requested_at is None
    assert provider.launches == []  # no physical launch
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_item = fresh.get(DatasetExperimentItemModel, item.id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        assert run.status == "pending"
        assert run.executor == "local_cpu"
        assert stored_item.status == "running"
        assert stored_attempt is not None


def test_start_item_attempt_uses_frozen_parameters_and_identity(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    captured = {}
    real_prepare = analysis.prepare_run

    def spy_prepare(**kwargs):
        captured.update(kwargs)
        return real_prepare(**kwargs)

    monkeypatch.setattr(analysis, "prepare_run", spy_prepare)
    ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)

    assert captured["recording_id"] == item.recording_id
    assert captured["pipeline_id"] == "g3_local"
    assert captured["executor"] == "local_cpu"
    assert captured["parameters"] == {}
    assert captured["model_release_id"] is None


def test_start_item_attempt_requires_same_session(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    other_session = client.app.state.database.session_factory()
    other_analysis = AnalysisService(
        other_session, PipelineRegistry([G3LocalPipeline()]), client.app.state.job_manager,
        executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=other_analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_rejects_non_queued_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    item.status = "completed"
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_rejects_terminal_experiment(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    experiment.status = "completed"
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_start_item_attempt_missing_item_fails_closed(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id="missing", analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_ITEM_NOT_FOUND"


def test_start_item_attempt_prepare_failure_leaves_item_queued(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    def boom(**kwargs):
        raise PlatformError("EXECUTION_CAPABILITY_UNAVAILABLE", "injected")

    monkeypatch.setattr(analysis, "prepare_run", boom)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_commit_failure_rolls_back(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_start_item_attempt_revalidates_frozen_identity(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    experiment.parameters_json = {"unexpected": 1}  # drift vs empty schema
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_attempt.py` exactly as above.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_attempt.py -v
```
Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'start_item_attempt'`.
- [ ] **Implement minimal code:** add `start_item_attempt`, `_claim_queued_item`, `_next_attempt_number` and the `update`/`TYPE_CHECKING` imports exactly as above.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_models.py \
  backend/tests/test_dataset_experiment_read_model.py \
  backend/tests/test_dataset_experiment_creation.py \
  backend/tests/test_dataset_experiment_revalidation.py -q
```
- [ ] **Commit checkpoint:** `feat: add dataset experiment transaction a ownership seam`

---

## Task 2 — Remote source-hash atomicity in Transaction A (verification; production expected unchanged)

**Files:**
- Create: `backend/tests/test_dataset_experiment_attempt_remote.py`.
- Production: none expected. If the real-identity tests expose a defect in the G2
  transaction-neutral source hash or the G1 seam, fix it in
  `backend/app/dataset_experiments/service.py` or report a blocker — do not
  redesign.

**Interfaces:** consumes Task 1 with the real
`resolve_remote_recording_identity`, a real `ExecutorRegistry` +
`ExecutionCertificateStore`, and a `FakeProvider("remote_gpu", ...)`.

### Exact tests (test-only)

```python
from pathlib import Path

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.identity import resolve_remote_recording_identity
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
)

from executor_fixtures import FakeProvider

RUN = "a" * 40
MANIFEST = "b" * 64
RUNTIME_REF = "remote:test:cuda:1"


class G3RemotePipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_remote", name="G3 Remote", version="1.0", label_space="spacenet_14",
            recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
            task_capability="detection_classification", executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu", model_release_required=True,
            technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class _Probe:
    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        return ExecutorAvailabilityRead(
            executor="remote_gpu", available=True, reason_code=None, reason_message=None,
            remote_profile="autodl_primary", recommended=True,
        )


class _Store:
    def resolve(self, plugin_id, plugin_version, requested):
        release = type("R", (), {"model_release_id": requested or "golden",
                                 "asset_manifest_sha256": MANIFEST})()
        manifest = type("M", (), {"asset_manifest_sha256": MANIFEST})()
        return ResolvedModelRelease(release=release, manifest=manifest)


def _cert():
    return ExecutionCertificate(
        plugin_id="g3_remote", plugin_version="1.0", model_release_id="golden",
        executor="remote_gpu", device_type="cuda", precision="float16",
        runtime_ref=RUNTIME_REF, evidence_ref="g3-test",
    )


def _seed_remote_dataset(client, data_root, recording_id="rec_real"):
    target = data_root / "recordings" / recording_id / "raw.iq"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x01\x02\x03" * 256)
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name=recording_id)
        add_ground_truth(session, gt_id=f"gt_{recording_id}", recording_id=recording_id,
                         class_id=9, class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _services_remote(client, data_root):
    session = client.app.state.database.session_factory()
    provider = FakeProvider("remote_gpu", runtime_ref=RUNTIME_REF, probe=_Probe())
    executor_registry = ExecutorRegistry(
        {"remote_gpu": provider}, ExecutionCertificateStore([_cert()])
    )
    registry = PipelineRegistry([G3RemotePipeline()])
    ds = DatasetExperimentService(session, registry, _Store(), executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager,
        remote_executor_probe=_Probe(), identity_resolver=resolve_remote_recording_identity,
        orchestrator_commit_resolver=lambda project_root: RUN,
        model_release_store=_Store(), runtime_commit_config=RUN,
        project_root=Path("/tmp"), data_root=data_root, executor_registry=executor_registry,
    )
    return session, ds, analysis, provider


def _remote_experiment(ds):
    return ds.create_experiment(
        name="g3r", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_remote", plugin_version="1.0",
        executor="remote_gpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
        model_release_id="golden",
    )


def _queued_item(session, experiment_id):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=0
    ).one()


def test_transaction_a_commits_source_hash_run_attempt_item_together(client, tmp_path):
    data_root = tmp_path / "data"
    _seed_remote_dataset(client, data_root)
    session, ds, analysis, provider = _services_remote(client, data_root)
    experiment = _remote_experiment(ds)
    item = _queued_item(session, experiment.id)

    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )
    assert attempt.launch_requested_at is None
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(RecordingModel, item.recording_id).source_data_sha256 is not None
        assert fresh.get(AnalysisRunModel, attempt.analysis_run_id).status == "pending"
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "running"
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id) is not None


def test_transaction_a_rolls_back_source_hash_run_attempt_item_together(client, tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _seed_remote_dataset(client, data_root)
    session, ds, analysis, provider = _services_remote(client, data_root)
    experiment = _remote_experiment(ds)
    item = _queued_item(session, experiment.id)

    real_prepare = analysis.prepare_run

    def prepare_then_fail(**kwargs):
        real_prepare(**kwargs)  # stages source hash + pending Run
        raise RuntimeError("injected after staging")

    monkeypatch.setattr(analysis, "prepare_run", prepare_then_fail)
    with pytest.raises(RuntimeError):
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)

    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(RecordingModel, item.recording_id).source_data_sha256 is None
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_attempt_remote.py` exactly as above.
- [ ] **Run RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_attempt_remote.py -v
```
Expected: PASS immediately after Task 1 if the G2 source-hash/identity transaction neutrality holds. This is a **verification task, not a TDD RED**; a failure means a real defect — STOP and report it rather than weakening the test.
- [ ] **Implement minimal code:** none expected.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_remote_source_hash.py \
  backend/tests/test_analysis_prepare_remote.py \
  backend/tests/test_dataset_experiment_attempt.py -q
```
- [ ] **Commit checkpoint:** `test: verify remote source hash transaction a atomicity`

---

## Task 3 — Attempt-number authority + concurrency claim (verification; production expected unchanged)

**Files:**
- Create: `backend/tests/test_dataset_experiment_attempt_concurrency.py`.
- Production: none expected.

### Exact tests (test-only)

This file is self-contained. Re-declare the small helpers (`G3LocalPipeline`,
`_seed_dataset`, `_services`, `_experiment`, `_item`) verbatim from Task 1; never
import another test module. Then add:

```python
def test_next_attempt_number_is_max_plus_one(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    session.add(AnalysisRunModel(
        id="run_history_1", recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="failed", parameters_json={},
    ))
    session.add(AnalysisRunModel(
        id="run_history_2", recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="failed", parameters_json={},
    ))
    session.add_all([
        DatasetExperimentAttemptModel(id="a1", experiment_item_id=item.id,
                                      attempt_number=1, analysis_run_id="run_history_1"),
        DatasetExperimentAttemptModel(id="a2", experiment_item_id=item.id,
                                      attempt_number=2, analysis_run_id="run_history_2"),
    ])
    session.commit()

    assert ds._next_attempt_number(item.id) == 3


def test_claim_queued_item_is_single_use(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 1
    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 0


def test_claim_rejects_externally_started_item(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    with client.app.state.database.session_factory() as other:
        other.get(DatasetExperimentItemModel, item.id).status = "running"
        other.commit()
    assert ds._claim_queued_item(item_id=item.id, experiment_id=experiment.id) == 0


def test_start_item_attempt_lost_claim_race_fails_closed(client, monkeypatch):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)

    monkeypatch.setattr(ds, "_claim_queued_item", lambda **kwargs: 0)
    with pytest.raises(PlatformError) as exc:
        ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id, analysis_service=analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentItemModel, item.id).status == "queued"
        assert fresh.query(AnalysisRunModel).count() == 0
        assert fresh.query(DatasetExperimentAttemptModel).count() == 0


def test_attempt_numbers_are_independent_per_item(client):
    _seed_dataset(client, count=2)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    first = _item(session, experiment.id, order=0)
    second = _item(session, experiment.id, order=1)

    a1 = ds.start_item_attempt(experiment_id=experiment.id, item_id=first.id, analysis_service=analysis)
    a2 = ds.start_item_attempt(experiment_id=experiment.id, item_id=second.id, analysis_service=analysis)
    assert a1.attempt_number == 1
    assert a2.attempt_number == 1
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_attempt_concurrency.py` (self-contained helpers re-declared).
- [ ] **Run RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_attempt_concurrency.py -v
```
Expected: PASS immediately after Task 1. This is a **verification task, not a TDD RED**; failure is a real defect — STOP and report.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_models.py -q
```
- [ ] **Commit checkpoint:** `test: verify attempt number authority and item claim`

---

## Task 4 — Regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_dataset_experiment_g3_regression.py`.
- No production change.

### Exact regression guards

```python
from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
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


def test_analysis_run_schema_unchanged_by_g3():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_attempt_schema_unchanged_by_g3():
    assert set(DatasetExperimentAttemptModel.__table__.columns.keys()) == _ATTEMPT_COLUMNS
    assert "attempt_count" not in DatasetExperimentAttemptModel.__table__.columns.keys()
    assert "attempt_count" not in DatasetExperimentItemModel.__table__.columns.keys()
```

### Steps

- [ ] **Add regression guards** `backend/tests/test_dataset_experiment_g3_regression.py`.
- [ ] **Run guards (expected PASS):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_g3_regression.py -v
```
- [ ] **Run the complete G3 suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_attempt.py \
  backend/tests/test_dataset_experiment_attempt_remote.py \
  backend/tests/test_dataset_experiment_attempt_concurrency.py \
  backend/tests/test_dataset_experiment_g3_regression.py -v
```
- [ ] **Run G1 + G2 regression:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_models.py \
  backend/tests/test_dataset_experiment_read_model.py \
  backend/tests/test_dataset_experiment_creation.py \
  backend/tests/test_dataset_experiment_revalidation.py \
  backend/tests/test_dataset_experiment_regression.py \
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py \
  backend/tests/test_analysis_launch_prepared_run.py \
  backend/tests/test_analysis_create_run_compat.py \
  backend/tests/test_analysis_seam_regression.py \
  backend/tests/test_remote_source_hash.py \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py -q
```
- [ ] **Run full backend suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```
Require 0 failed / 0 errors (current sealed baseline: `1436 passed, 28 skipped`; G3 must not reduce passed or introduce failures). If the tool times out, rerun in a background session until pytest prints a final summary; do not infer PASS from a partial run.
- [ ] **Commit checkpoint:** `test: add phase G G3 regression matrix`

---

## Regression Matrix

| Suite | Purpose |
|---|---|
| `test_dataset_experiment_attempt.py` | Transaction A local ownership, guards, atomicity |
| `test_dataset_experiment_attempt_remote.py` | source-hash + Run + Attempt + Item joint commit/rollback |
| `test_dataset_experiment_attempt_concurrency.py` | `max+1`, single-use claim, lost-race fail-closed |
| `test_dataset_experiment_g3_regression.py` | no schema change / no persisted counter |
| G1 suites | models/migration/read/creation/revalidation unchanged |
| G2 suites | prepare/launch seam + canonical provenance unchanged |
| `test_remote_source_hash.py` | caller-owned source-hash transaction |
| full backend suite | no regressions |

---

## Self-Review

1. `start_item_attempt` never writes `launch_requested_at`, never calls
   `launch_prepared_run`, `provider.launch`, `job_manager.start`, or a coordinator.
2. Run + Attempt + Item `running` (+ source-hash cache) commit in one transaction;
   any failure rolls back all of them.
3. `AnalysisService` must share the exact Session; mismatch fails closed.
4. `attempt_number = max(existing) + 1`; no persisted counter added.
5. The Item precondition is `queued`; completed/running items are rejected; no
   implicit `failed -> queued`.
6. The concurrency claim is a conditional `queued -> running` UPDATE (rowcount must
   be 1) issued under `no_autoflush`; the unique constraints are backstops.
7. No explicit `flush()`; all referenced ids are Python strings and SQLAlchemy
   orders FK inserts.
8. G1/G2 seams are reused; no duplicated provenance or manifest logic.
9. No DatasetExperiment schema/migration change; `AnalysisRun` schema unchanged.
10. No G4/G5/G6 behavior enters G3.
11. Every task has exact files, interfaces, tests, commands, and checkpoints.
12. No TODO/TBD/XXX/hand-wavy placeholders.

---

## Out-of-Scope Guardrails During Implementation

- If a change is needed to `dataset_experiments/model.py`, migrations,
  `analysis/service.py`, canonical hashing, fingerprinting, AssetManifest V1,
  ModelRelease authority, certificate semantics, or any scientific code, STOP and
  report the blocker.
- If `start_item_attempt` appears to need `launch_requested_at`,
  `launch_prepared_run`, `provider.launch`, a worker, a coordinator, or an
  automatic retry, STOP — that is G4+.
- If atomicity appears impossible under SQLite with the existing schema/session
  architecture, STOP and report rather than redesigning.
