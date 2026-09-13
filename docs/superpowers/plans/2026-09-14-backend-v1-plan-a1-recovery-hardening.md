# Backend V1 Plan A1 — Recovery Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the P1/P2/P3 local-executor recovery defects without changing
scientific behavior, executor identity semantics, or remote_gpu semantics.

**Architecture:** The platform already separates four concerns: (a) durable
Transaction A (Run + Attempt + Item binding), (b) durable Transaction B (launch
intent), (c) generation-token fencing, and (d) the physical process launch. Plan
A1 (1) generalizes the startup stale-run interrupter and the DatasetExperiment
pending-run repair from `local_cpu` to all local executors, and (2) extracts a
cheap, deterministic execution-authority validation seam and calls it before
Transaction B claims the launch intent, with correct item-level vs
experiment-level failure classification. No new state machine, no new DB column,
no schema/migration change.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, pytest, SQLite.

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`
(approved design commit `513dce3012ea42dd284e7cd5d452e5a51faf799c`).

---

## Global Constraints

1. Preserve Transaction A / Transaction B durability. Never collapse them into one
   transaction and never launch a worker before the launch intent is durably
   committed.
2. No executor substitution. A frozen `local_gpu` run is never relaunched as
   `local_cpu` or `remote_gpu`; the platform fails closed.
3. `remote_gpu` semantics are unchanged: remote `pending`/`running` runs are never
   interrupted by the local helper; remote re-coordination/rotation is untouched.
4. No new DB schema or migration. No new AnalysisRun/Item/Attempt statuses.
5. No Auto selection, no runtime doctor, no certificate installer, no Dataset
   qualification, no frontend, no scientific-pipeline changes.
6. No GPU model execution is required for Plan A1; all tasks are CPU/DB-level.
7. Control-plane `.venv` stays ML-free
   (`find_spec("torch") is None`, `find_spec("ultralytics") is None`).
8. Strict TDD per task: RED → minimal change → GREEN → focused regression → commit.
9. Small semantic commits; no giant implementation commit.
10. Every production change is confined to recovery/launch-authority seams.

## Current Recovery Architecture (audited at `513dce3`)

```text
Startup (app/main.py:137-159, order):
  mark_stale_local_cpu_runs_interrupted(session)              # recovery.py:22-37
  mark_stale_running_evaluations_interrupted(session)
  [remote config] coordinate_orphaned_remote_runs(...)        # recovery.py:77-108
  recover_dataset_experiments(...)                            # dataset_experiments/recovery.py:313-402

mark_stale_local_cpu_runs_interrupted (remote_execution/recovery.py:22-37):
  UPDATE analysis_runs SET status=interrupted, error_type=ANALYSIS_INTERRUPTED
  WHERE status='running' AND executor='local_cpu'             # <-- local_cpu only
  Compatibility delegate: analysis/service.py:19-27 mark_stale_running_runs_interrupted

recover_dataset_experiments (dataset_experiments/recovery.py:313-402):
  claim generation (recovery.py:45-82) -> reconcile_items -> repair_local_pending_runs
  repair_local_pending_runs (recovery.py:155-214):
    if run.executor != "local_cpu" or run.status != "pending": continue   # <-- local_cpu only

launch_item_attempt (dataset_experiments/service.py:572-695):
  revalidate_frozen_identity(experiment_id)                   # service.py:287-417 (section 8:
                                                              #  provider + certified_capability +
                                                              #  descriptor match -> raises
                                                              #  DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED)
  _claim_launch_intent CAS + commit (Transaction B)            # service.py:673-691, 697-718
  analysis_service.launch_prepared_run(run.id)                 # service.py:693 -> analysis/service.py:249-312

Coordinator scheduling (dataset_experiments/coordinator.py:118-139):
  start_item_attempt + launch_item_attempt in try/except PlatformError
  FENCE_LOST -> raise; is_experiment_level(exc) -> raise (fails experiment);
  else -> ds._mark_item_failed(item_id, code, message)         # item-level

is_experiment_level (coordinator.py:56-63): EXPERIMENT_LEVEL_CODES includes
  EXECUTION_CAPABILITY_UNAVAILABLE and EXECUTION_NOT_CERTIFIED
```

**Audit finding (honest correction to the design's G7 framing).** The durable
ordering is *already* `revalidate_frozen_identity` → Transaction B CAS/commit →
physical launch (`service.py:595, 673-693`). Because `revalidate_frozen_identity`
section 8 already checks provider registration + `certified_capability` + frozen
descriptor match **before** the CAS, the "intent recorded although no worker was
launched" ambiguity is already prevented on the DatasetExperiment path. The real
residual defects are:
- **P3-a:** those checks raise the experiment-level code
  `DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED`, so a *recoverable* deployment
  condition (provider/certificate disappeared) fails the whole experiment instead
  of failing the item and reaching `completed_with_failures` (contradicts the
  approved design A2-9 / Case D).
- **P3-b:** the checks are inline in `revalidate_frozen_identity`, not a reusable
  seam, and cannot be reused by the recovery repair path.
- **P3-c:** the recovery repair path (`repair_local_pending_runs`) does not consult
  execution authority before relaunching, so on a no-GPU cold boot it would either
  raise an experiment-level error or stall.

Plan A1's P3 therefore extracts the seam, wires it before the intent CAS, and
corrects the classification.

---

## Task 1 — P1: Generic local stale-run startup recovery

**Commit:** `fix: recover stale local gpu analysis runs`

**Files**
- Modify: `backend/app/remote_execution/recovery.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/analysis/service.py` (docstring/delegate only)
- Modify tests: `backend/tests/test_remote_startup_recovery.py`,
  `backend/tests/test_remote_stale_helper_regression.py`,
  `backend/tests/test_dataset_experiment_startup_order.py`

**Interface produced:** `mark_stale_local_runs_interrupted(session: Session) -> int`
plus a backward-compatible alias `mark_stale_local_cpu_runs_interrupted`.

### Step 1.1 — RED: write failing tests

- [ ] In `backend/tests/test_remote_startup_recovery.py`, rename
  `test_mark_stale_interrupts_local_cpu_running_only` to
  `test_mark_stale_interrupts_local_running_only` and assert:
  ```python
  _add_run(client, "run_cpu", "local_cpu", "running")
  _add_run(client, "run_gpu", "local_gpu", "running")
  _add_run(client, "run_gpu_pending", "local_gpu", "pending")
  _add_run(client, "run_remote", "remote_gpu", "running")
  with client.app.state.database.session_factory() as session:
      n = mark_stale_local_runs_interrupted(session)
  assert n == 2                       # only the two running local runs
  # local_cpu + local_gpu running -> interrupted / ANALYSIS_INTERRUPTED
  # local_gpu pending -> untouched (still pending)
  # remote_gpu running -> untouched
  ```
- [ ] In `backend/tests/test_remote_stale_helper_regression.py`, update
  `test_stale_helper_interrupts_local_cpu_only` →
  `test_stale_helper_interrupts_local_running_only` (same assertions) and update
  the import to `mark_stale_local_runs_interrupted`. Keep a test
  `test_legacy_stale_helper_alias_delegates` asserting
  `mark_stale_local_cpu_runs_interrupted` still returns the local count.
- [ ] In `backend/tests/test_dataset_experiment_startup_order.py`, update the two
  monkeypatch targets from `"mark_stale_local_cpu_runs_interrupted"` to
  `"mark_stale_local_runs_interrupted"` (the name `main.py` will call).
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_remote_startup_recovery.py \
    backend/tests/test_remote_stale_helper_regression.py \
    backend/tests/test_dataset_experiment_startup_order.py -q
  ```
  Expected RED reason: `ImportError`/`AttributeError` for
  `mark_stale_local_runs_interrupted` (function does not exist).

### Step 1.2 — GREEN: minimal implementation

- [ ] In `backend/app/remote_execution/recovery.py`:
  ```python
  _LOCAL_EXECUTORS = ("local_cpu", "local_gpu")

  def mark_stale_local_runs_interrupted(session: Session) -> int:
      """Interrupt stale running LOCAL runs (local_cpu + local_gpu). Remote untouched."""
      statement = (
          update(AnalysisRunModel)
          .where(AnalysisRunModel.status == "running")
          .where(AnalysisRunModel.executor.in_(_LOCAL_EXECUTORS))
          .values(
              status="interrupted",
              error_type="ANALYSIS_INTERRUPTED",
              error_message="Previous local analysis process ended before platform restart.",
              finished_at=datetime.now(timezone.utc),
          )
      )
      result = session.execute(statement)
      session.commit()
      return int(result.rowcount or 0)

  # Backward-compatible alias (legacy name).
  mark_stale_local_cpu_runs_interrupted = mark_stale_local_runs_interrupted
  ```
  Update the module docstring to state local_cpu + local_gpu.
- [ ] In `backend/app/main.py:140,143`, import and call
  `mark_stale_local_runs_interrupted`.
- [ ] In `backend/app/analysis/service.py:19-27`, keep
  `mark_stale_running_runs_interrupted` delegating to
  `mark_stale_local_runs_interrupted`; update its docstring.
- [ ] Run GREEN:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_remote_startup_recovery.py \
    backend/tests/test_remote_stale_helper_regression.py \
    backend/tests/test_dataset_experiment_startup_order.py -q
  ```

### Step 1.3 — Focused regression + commit

- [ ] `git diff --check && git status --short`
- [ ] Commit `fix: recover stale local gpu analysis runs`

---

## Task 2 — P2: Generic DatasetExperiment local pending-run repair

**Commit:** `fix: generalize local dataset experiment recovery`

**Files**
- Modify: `backend/app/dataset_experiments/recovery.py`
- Modify tests: `backend/tests/test_dataset_experiment_local_launch_recovery.py`

### Step 2.1 — RED: parameterize the local recovery test over executors

- [ ] Refactor the fixture `_experiment_with_running_item` (currently seeds
  `executor="local_cpu"`) to accept `executor: str = "local_cpu"`, and
  parameterize the recovery tests over `["local_cpu", "local_gpu"]`:
  `test_safe_first_launch_reuses_same_attempt_and_run`,
  `test_safe_first_launch_creates_no_new_attempt_or_run`,
  `test_ambiguous_pending_local_run_fails_closed_zero_launch`,
  `test_ambiguous_fail_close_idempotent_under_current_generation`,
  `test_ambiguous_run_projects_item_failed`.
- [ ] Add `test_local_gpu_safe_first_launch_relaunches` asserting: running item +
  authoritative pending `local_gpu` run + `launch_requested_at IS NULL` + no worker
  → the same attempt and run are relaunched once (no new Attempt/AnalysisRun).
- [ ] Add `test_foreign_executor_pending_run_not_repaired` asserting a `local_cpu`
  helper on a `remote_gpu` item is delegated (no local marker logic) — reuse
  `test_remote_pending_run_delegated_no_local_marker_logic`.
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_local_launch_recovery.py -q
  ```
  Expected RED: `local_gpu` parameters fail because `repair_local_pending_runs`
  skips non-`local_cpu` runs (no relaunch → assertion on launch count).

### Step 2.2 — GREEN: minimal implementation

- [ ] In `backend/app/dataset_experiments/recovery.py`, define
  `_LOCAL_EXECUTORS = ("local_cpu", "local_gpu")` and change
  `repair_local_pending_runs` (line ~176) from
  `if run.executor != "local_cpu" or run.status != "pending":`
  to
  `if run.executor not in _LOCAL_EXECUTORS or run.status != "pending":`
  Update the docstring ("Repair local pending runs").
- [ ] Run GREEN:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_local_launch_recovery.py -q
  ```

### Step 2.3 — Focused regression + commit

- [ ] Run `test_dataset_experiment_start.py`, `test_dataset_experiment_attempt.py`,
  `test_dataset_experiment_launch.py`.
- [ ] Commit `fix: generalize local dataset experiment recovery`

---

## Task 3 — P3 seam: reusable frozen execution-authority validation

**Commit:** `feat: add frozen execution authority validation seam`

**Files**
- Modify: `backend/app/remote_execution/runtime.py`
- Create test: `backend/tests/test_execution_authority.py`

**Owner:** `ExecutorRegistry` (narrowest reusable owner: it already owns
providers, descriptors, and the certificate store).

**Interface produced:**
```python
def validate_frozen_execution_authority(
    self,
    definition: PipelineDefinition,
    model_release_id: str | None,
    executor: str,
    runtime_descriptor: RuntimeDescriptor,
) -> ExecutorProvider:
    """Cheap, deterministic, no live hardware probe.

    Raises:
      EXECUTION_CAPABILITY_UNAVAILABLE  provider not registered (item-level, recoverable)
      RUNTIME_DESCRIPTOR_INVALID        provider descriptor != frozen descriptor (frozen identity)
      EXECUTION_NOT_CERTIFIED           no exact certificate for the frozen tuple (item-level)
    Returns the provider on success.
    """
```
Descriptor equality is on the four execution fields
`(executor, device_type, device_index, precision)`; `environment_ref`/`_label`
are deployment-private and ignored.

### Step 3.1 — RED: write the seam test

- [ ] Create `backend/tests/test_execution_authority.py` with a
  `_registry(provider_descriptor, certificates)` builder and tests:
  ```text
  test_authority_ok_returns_provider
  test_authority_missing_provider_raises_EXECUTION_CAPABILITY_UNAVAILABLE
  test_authority_descriptor_mismatch_raises_RUNTIME_DESCRIPTOR_INVALID
  test_authority_missing_certificate_raises_EXECUTION_NOT_CERTIFIED
  test_authority_wrong_runtime_ref_raises_EXECUTION_NOT_CERTIFIED
  test_authority_does_not_call_provider_probe   # assert probe() not invoked (spy)
  ```
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_execution_authority.py -q
  ```
  Expected RED: `AttributeError` (method does not exist).

### Step 3.2 — GREEN: implement the seam

- [ ] Add the method to `ExecutorRegistry` in
  `backend/app/remote_execution/runtime.py`:
  - provider lookup via `self._providers.get(executor)`;
  - descriptor comparison against `provider.runtime_descriptor()`;
  - `self.certified_capability(definition, model_release_id, executor) is None`
    → `EXECUTION_NOT_CERTIFIED`;
  - never call `provider.probe()` (no live hardware probe).
- [ ] Run GREEN on `test_execution_authority.py`.

### Step 3.3 — Focused regression + commit

- [ ] Run `backend/tests/test_executor_registry.py`,
  `test_executor_cutover.py`, `test_execution_certificate.py`.
- [ ] Commit `feat: add frozen execution authority validation seam`

---

## Task 4 — P3 wiring: authority before intent + correct classification

**Commit:** `fix: revalidate execution authority before launch intent`

**Files**
- Modify: `backend/app/dataset_experiments/service.py`
- Modify: `backend/app/dataset_experiments/recovery.py`
- Modify: `backend/app/dataset_experiments/coordinator.py`
- Modify tests: `backend/tests/test_dataset_experiment_launch.py`,
  `backend/tests/test_dataset_experiment_coordinator_scheduling.py`,
  `backend/tests/test_dataset_experiment_local_launch_recovery.py`

### Step 4.1 — RED: authority-before-intent and classification tests

- [ ] In `test_dataset_experiment_launch.py`, add
  `test_authority_missing_before_intent_claims_nothing`:
  seed a running experiment/item/attempt/pending run whose executor provider is
  absent from the registry; call `launch_item_attempt`; assert:
  - raises `EXECUTION_CAPABILITY_UNAVAILABLE`;
  - `attempt.launch_requested_at is None` (no intent claimed);
  - `run.status == "pending" and run.worker_pid is None` (no launch, no
    ambiguity marker).
- [ ] In `test_dataset_experiment_coordinator_scheduling.py`, add
  `test_provider_disappears_scheduling_fails_item_not_experiment`:
  a `running` experiment with queued items and a registry lacking the frozen
  provider; one `coordinator.step` → item(s) `failed`, experiment NOT `failed`;
  after all items terminal the experiment reaches `completed_with_failures`.
  Assert `is_experiment_level` classification: `EXECUTION_CAPABILITY_UNAVAILABLE`
  and `EXECUTION_NOT_CERTIFIED` are item-level; `RUNTIME_DESCRIPTOR_INVALID` and
  `DATASET_EXPERIMENT_*` remain experiment-level.
- [ ] In `test_dataset_experiment_local_launch_recovery.py`, add
  `test_recovery_provider_absent_fails_closed_run_and_item`:
  running item + pending `local_gpu` run + `launch_requested_at IS NULL` + provider
  absent at recovery → no intent claimed, no substitution, run `interrupted`
  (bounded error), item `failed` after reconcile, experiment reaches
  `completed_with_failures`; and
  `test_recovery_authority_ok_relaunches_local_gpu` (provider present → relaunch).
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_launch.py \
    backend/tests/test_dataset_experiment_coordinator_scheduling.py \
    backend/tests/test_dataset_experiment_local_launch_recovery.py -q
  ```
  Expected RED: `launch_item_attempt` still raises the experiment-level
  `DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED` (with the intent not claimed but
  the wrong code), and the coordinator still fails the experiment.

### Step 4.2 — GREEN: implement

- [ ] In `backend/app/dataset_experiments/service.py`:
  - In `launch_item_attempt`, after `revalidate_frozen_identity` and before
    `_claim_launch_intent`, call the seam:
    ```python
    definition = self.registry.get(experiment.plugin_id).definition
    frozen_descriptor = RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)
    self.executor_registry.validate_frozen_execution_authority(
        definition, experiment.model_release_id, experiment.executor, frozen_descriptor
    )
    ```
    (resolve `RuntimeDescriptor` via `app.remote_execution.runtime`).
  - In `revalidate_frozen_identity`, remove section 8 (provider registration +
    `certified_capability` + provider-descriptor match, current lines ~391-412)
    so the experiment-wide identity check no longer fails the whole experiment for
    a recoverable provider/certificate disappearance; keep dataset-membership,
    release, asset-manifest, and frozen-parameter checks experiment-level.
- [ ] In `backend/app/dataset_experiments/coordinator.py`:
  - Move `"EXECUTION_CAPABILITY_UNAVAILABLE"` and `"EXECUTION_NOT_CERTIFIED"` from
    `EXPERIMENT_LEVEL_CODES` to `ITEM_LEVEL_CODES`. Leave
    `RUNTIME_DESCRIPTOR_INVALID` unlisted (default experiment-level = frozen
    identity change).
- [ ] In `backend/app/dataset_experiments/recovery.py`:
  - Parameterize `fail_closed_pending_local_run(..., error_type=None,
    error_message=None)` defaulting to the current `ANALYSIS_LAUNCH_AMBIGUOUS`
    values (preserves existing tests).
  - In `repair_local_pending_runs`, before the safe first-launch call, validate
    authority; if it raises `EXECUTION_CAPABILITY_UNAVAILABLE` or
    `EXECUTION_NOT_CERTIFIED`, fail-close the run via
    `fail_closed_pending_local_run(..., error_type=exc.code, error_message=exc.message)`
    and `continue` (no relaunch, no substitution); let
    `RUNTIME_DESCRIPTOR_INVALID` and `DATASET_EXPERIMENT_*` propagate
    (experiment-level).
- [ ] Run GREEN on the three files above.

### Step 4.3 — Focused regression + commit

- [ ] Run `test_dataset_experiment_launch.py`,
  `test_dataset_experiment_coordinator_regression.py`,
  `test_dataset_experiment_generation_fence.py`,
  `test_dataset_experiment_attempt.py`,
  `test_analysis_launch_prepared_run.py`.
- [ ] Commit `fix: revalidate execution authority before launch intent`

---

## Task 5 — No-GPU / profile-change recovery boundary matrix

**Commit:** `test: cover no-gpu local executor recovery boundary`

**Files**
- Create: `backend/tests/test_local_executor_recovery_boundary.py`
- (Production code unchanged in this task unless a RED case exposes a defect.)

Cases (all CPU/DB-only; a settings fixture with no `WSP_LOCAL_GPU_*` and a
registry lacking `local_gpu`):

- [ ] **Case A (P1):** historical `AnalysisRun(executor="local_gpu",
  status="running")`, boot recovery → `interrupted` / `ANALYSIS_INTERRUPTED`;
  `create_app` boots; `/api/health` ok.
- [ ] **Case B (P2/P3):** DatasetExperiment with `item=running`, run
  `executor="local_gpu"`, `status="pending"`, `launch_requested_at IS NULL`,
  provider absent → no intent claimed, no `local_cpu`/`remote_gpu` launch, run
  `interrupted` (bounded code), item `failed`, experiment → `completed_with_failures`.
- [ ] **Case C (existing):** run `pending`, `launch_requested_at SET`,
  `worker_pid NULL`, `local_gpu`, provider present → existing ambiguous
  fail-closed (no relaunch, no substitution).
- [ ] **Case D (coordinator):** queued items with frozen `local_gpu` and provider
  unavailable after cold boot → each item fails deterministically, experiment
  reaches `completed_with_failures` (no infinite `running`).
- [ ] Assert in every case: no permanent `pending`/`running` local state remains.
- [ ] Run RED then GREEN:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_local_executor_recovery_boundary.py -q
  ```
- [ ] Commit `test: cover no-gpu local executor recovery boundary`

---

## Task 6 — Idempotency, fencing, and final regression/evidence

**Commit:** `test: harden recovery idempotency and fencing`

**Files**
- Modify tests: `backend/tests/test_dataset_experiment_generation_fence.py`,
  `backend/tests/test_dataset_experiment_local_launch_recovery.py`,
  `backend/tests/test_remote_startup_recovery.py`

### Step 6.1 — RED/GREEN: idempotency + fencing assertions

- [ ] Add:
  ```text
  test_stale_interrupt_idempotent                 # P1 run twice: second pass rowcount 0; already-interrupted untouched
  test_double_recovery_no_duplicate_attempt_run   # recover_dataset_experiments twice: no new Attempt/AnalysisRun
  test_double_recovery_no_duplicate_launch_intent # launch_requested_at single-use preserved
  test_double_recovery_no_duplicate_coordinator   # heartbeat cutoff prevents re-claim; no second spawn
  test_recovery_preserves_generation_fence        # old token cannot write after claim (existing seams: claim_experiment_generation, _require_experiment_generation)
  test_authority_check_does_not_bypass_fence      # launch_item_attempt with stale token -> DATASET_EXPERIMENT_FENCE_LOST, no intent claimed
  ```
- [ ] Run:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_generation_fence.py \
    backend/tests/test_dataset_experiment_local_launch_recovery.py \
    backend/tests/test_remote_startup_recovery.py -q
  ```

### Step 6.2 — Full backend regression (mandatory)

- [ ] ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
  ```
  Require `0 failed, 0 errors`. Skips are hardware/env-gated and are not passes.
- [ ] Control-plane boundary:
  ```bash
  "$PWD/.venv/bin/python" -c "import importlib.util as u; assert u.find_spec('torch') is None and u.find_spec('ultralytics') is None; print('VENV_NO_ML_OK')"
  ```
- [ ] `git diff --check && git status --short`
- [ ] Commit `test: harden recovery idempotency and fencing`

---

## Test Matrix (requirement → task → test)

| Requirement | Task | Test |
|---|---|---|
| P1 local_cpu running → interrupted | 1 | `test_mark_stale_interrupts_local_running_only` |
| P1 local_gpu running → interrupted | 1 | `test_mark_stale_interrupts_local_running_only` |
| P1 remote_gpu untouched | 1 | same + `test_startup_preserves_remote_pending_and_running` |
| P1 idempotent | 6 | `test_stale_interrupt_idempotent` |
| P2 local_gpu safe first-launch | 2 | `test_local_gpu_safe_first_launch_relaunches` |
| P2 ambiguous local_gpu fail-closed | 2 | `test_ambiguous_pending_local_run_fails_closed_zero_launch[local_gpu]` |
| P2 remote delegated | 2 | `test_remote_pending_run_delegated_no_local_marker_logic` |
| P3 seam ok/provider/cert/descriptor | 3 | `test_execution_authority.py` (6 tests) |
| P3 authority before intent, no claim | 4 | `test_authority_missing_before_intent_claims_nothing` |
| P3 item vs experiment classification | 4 | `test_provider_disappears_scheduling_fails_item_not_experiment` |
| P3 recovery provider-absent fail-closed | 4 | `test_recovery_provider_absent_fails_closed_run_and_item` |
| P3 no live probe | 3 | `test_authority_does_not_call_provider_probe` |
| No-GPU Case A/B/C/D | 5 | `test_local_executor_recovery_boundary.py` |
| No substitution | 2/4/5 | cases B/C/D + ambiguous tests |
| Fencing preserved | 6 | `test_authority_check_does_not_bypass_fence`, `test_recovery_preserves_generation_fence` |
| Full regression | 6 | `pytest backend/tests -q` |
| ML-free control plane | 6 | `.venv find_spec` check |

## Expected Production Files

```text
backend/app/remote_execution/recovery.py          (P1)
backend/app/analysis/service.py                   (P1 delegate docstring)
backend/app/main.py                               (P1 call site)
backend/app/dataset_experiments/recovery.py       (P2 + P3 recovery fail-close)
backend/app/dataset_experiments/service.py        (P3 authority call + remove inline section 8)
backend/app/dataset_experiments/coordinator.py    (P3 classification)
backend/app/remote_execution/runtime.py           (P3 seam)
```

## Forbidden Scope (do not touch)

```text
Auto resolver / Auto API fields        frontend
runtime doctor / certificate installer
scientific pipelines (CPN/ZoomSpec preprocessing/detector/AHLP/FRN/postprocess)
model assets / label spaces
DB schema / migrations                 remote SSH architecture
execution certificate content          Dataset qualification
```

## Plan Self-Review

- [ ] Every P1/P2/P3 requirement maps to a task and test (see Test Matrix).
- [ ] Provider-disappearance-before-intent is covered (Task 4 RED test asserts
  `launch_requested_at is None` after the authority failure).
- [ ] Hardware-failure-after-authority is NOT conflated with P3: it remains the
  existing worker lifecycle (`running → failed/interrupted`); P3 only revalidates
  authority before the intent CAS and performs no live probe.
- [ ] No-GPU cold-profile cases A–D covered (Task 5).
- [ ] Idempotency covered (Task 6).
- [ ] Stale coordinator fencing preserved and tested (Task 6; existing CAS seams
  `claim_experiment_generation` / `_require_experiment_generation`).
- [ ] `remote_gpu` semantics unchanged (P1 predicate is local-only; remote
  re-coordination untouched; no test changes to remote behavior).
- [ ] No executor substitution anywhere.
- [ ] Exact error classification documented: provider → `EXECUTION_CAPABILITY_UNAVAILABLE`
  (item-level), certificate → `EXECUTION_NOT_CERTIFIED` (item-level), descriptor
  mismatch → `RUNTIME_DESCRIPTOR_INVALID` (experiment-level default),
  `DATASET_EXPERIMENT_*` → experiment-level; the classification correction is
  called out explicitly in Task 4.
- [ ] No Auto/runtime-doctor/frontend scope entered.
- [ ] No TODO/TBD/vague steps; every step names exact files/functions/tests.
- [ ] Every named function/path exists at `513dce3` (verified during audit:
  `mark_stale_local_cpu_runs_interrupted` recovery.py:22; `repair_local_pending_runs`
  recovery.py:155; `launch_item_attempt` service.py:572; `_claim_launch_intent`
  service.py:697; `revalidate_frozen_identity` service.py:287;
  `fail_closed_pending_local_run` recovery.py:85; `is_experiment_level`
  coordinator.py:56; `certified_capability` runtime.py:265).

## Known Interaction Called Out

Moving `EXECUTION_CAPABILITY_UNAVAILABLE` and `EXECUTION_NOT_CERTIFIED` from
experiment-level to item-level changes only the coordinator's scheduling
classification (used at `coordinator.py:133,144`). It does not affect
`create_experiment` (which raises synchronously to the API) or
`recover_dataset_experiments` (which handles the recovery repair path explicitly).
The change is required to satisfy the approved design (Case D →
`completed_with_failures`) instead of failing the whole experiment for a
recoverable deployment condition.
