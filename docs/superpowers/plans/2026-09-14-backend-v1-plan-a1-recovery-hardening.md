# Backend V1 Plan A1 — Recovery Hardening Implementation Plan (Corrected)

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the P1/P2/P3 local-executor recovery defects without changing
scientific behavior, executor identity semantics, or remote_gpu semantics.

**Architecture:** The platform separates (a) durable Transaction A (Run + Attempt
+ Item binding), (b) durable Transaction B (launch intent), (c) generation-token
fencing, and (d) the physical launch. Plan A1 (1) generalizes the startup
stale-run interrupter and the DatasetExperiment pending-run repair from `local_cpu`
to all local executors; (2) extracts a cheap, deterministic frozen-execution-
authority seam and calls it before Transaction B claims the launch intent; (3)
terminalizes the already-persisted `pending` AnalysisRun when authority is lost,
under generation fencing, so Item/Run invariants always agree; and (4) closes the
standalone single-recording `pending`-run race. No new state machine, no new DB
column, no schema/migration change.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, pytest, SQLite.

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`
(approved design commit `513dce3012ea42dd284e7cd5d452e5a51faf799c`).

**This revision supersedes the prior Plan A1** and applies Corrections 1–7:
frozen runtime identity is not weakened; the persisted pending Run is terminalized
on authority loss; the single-recording pending race is closed; error
classification and the test matrix are updated.

---

## Global Constraints

1. Preserve Transaction A / Transaction B durability. Never collapse them and
   never launch a worker before the launch intent is durably committed.
2. No executor substitution. A frozen `local_gpu` run is never relaunched as
   `local_cpu`/`remote_gpu`; fail closed.
3. `remote_gpu` semantics unchanged: remote `pending`/`running` runs are never
   interrupted by the local helper; remote re-coordination/rotation untouched.
   The single-recording correction is scoped to **local** executors only.
4. No new DB schema/migration; no new AnalysisRun/Item/Attempt statuses; reuse
   existing statuses (`pending`, `running`, `failed`, `interrupted`, `completed`).
5. **Frozen runtime identity is never weakened.** The authority seam compares the
   **full internal** `RuntimeDescriptor.to_metadata()` (including the persisted
   environment identity fields) exactly as `revalidate_frozen_identity` does today.
6. No Auto selection, no runtime doctor, no certificate installer, no Dataset
   qualification, no frontend, no scientific-pipeline changes.
7. No GPU model execution required; all tasks are CPU/DB-level.
8. Control-plane `.venv` stays ML-free.
9. Strict TDD per task: RED → minimal change → GREEN → focused regression → commit.
10. Small semantic commits; no giant implementation commit.
11. Only recovery / launch-authority seams are modified.

## Current Recovery Architecture (audited at `f314e29`, code at `513dce3`)

```text
main.py:137-159 startup order:
  mark_stale_local_cpu_runs_interrupted      recovery.py:22-37   (local_cpu only)
  mark_stale_running_evaluations_interrupted
  [remote config] coordinate_orphaned_remote_runs   recovery.py:77-108
  recover_dataset_experiments                 dataset_experiments/recovery.py:313-402

repair_local_pending_runs (dataset_experiments/recovery.py:155-214):
  skip if run.executor != "local_cpu" or run.status != "pending"      (:176)
  safe first-launch -> ds.launch_item_attempt(...)
  marker present    -> fail_closed_pending_local_run(...)   (:85-152, generation-fenced pending->interrupted)

launch_item_attempt (dataset_experiments/service.py:572-695):
  revalidate_frozen_identity(experiment_id)                  (:595; section 8 :391-412 compares
       provider registration + certified_capability + provider.runtime_descriptor().to_metadata()
       == experiment.runtime_descriptor_json, raising DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED)
  ownership-chain checks
  _claim_launch_intent CAS + COMMIT (Transaction B)          (:673-691, :697-718)
  analysis_service.launch_prepared_run(run.id)               (:693)

launch_prepared_run (analysis/service.py:249-312):
  provider = self.executor_registry.provider(run.executor)   (:285, OUTSIDE the try)
  ... try: provider.launch(...) except: run.status="failed"/ANALYSIS_FAILED

_mark_item_failed (dataset_experiments/service.py:1299-1353): updates ONLY the Item.

coordinator scheduling (coordinator.py:118-139):
  FENCE_LOST -> raise; is_experiment_level(exc) -> raise (fail experiment); else ds._mark_item_failed
is_experiment_level (coordinator.py:56-63): EXECUTION_CAPABILITY_UNAVAILABLE / EXECUTION_NOT_CERTIFIED
  are currently EXPERIMENT-level.
```

**Audit findings (three residual defects, all real):**
- **F1:** the only authority check (`revalidate_frozen_identity` section 8) raises
  an experiment-level code for a recoverable provider/certificate loss, and is not
  reusable.
- **F2:** if authority is lost between Transaction A and Transaction B,
  `_mark_item_failed` leaves the persisted `AnalysisRun` at `pending`, violating
  `reconcile_items` (`service.py:879-891` requires a failed item's authoritative run
  to be `failed`/`interrupted`).
- **F3:** `launch_prepared_run` resolves the provider **outside** the launch
  `try/except` (`analysis/service.py:285`), so a provider that disappears after
  `prepare_run` commits leaves a standalone `pending` run forever; P1 only handles
  `running` runs.

---

## Task 1 — P1: Generic local stale-run startup recovery

**Commit:** `fix: recover stale local gpu analysis runs`

**Files**
- Modify: `backend/app/remote_execution/recovery.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/analysis/service.py` (delegate docstring only)
- Modify tests: `backend/tests/test_remote_startup_recovery.py`,
  `backend/tests/test_remote_stale_helper_regression.py`,
  `backend/tests/test_dataset_experiment_startup_order.py`

**Interface produced:** `mark_stale_local_runs_interrupted(session) -> int` plus
alias `mark_stale_local_cpu_runs_interrupted`.

### Step 1.1 — RED

- [ ] In `test_remote_startup_recovery.py`, replace
  `test_mark_stale_interrupts_local_cpu_running_only` with
  `test_mark_stale_interrupts_local_running_only`:
  ```python
  _add_run(client, "run_cpu", "local_cpu", "running")
  _add_run(client, "run_gpu", "local_gpu", "running")
  _add_run(client, "run_gpu_pending", "local_gpu", "pending")
  _add_run(client, "run_remote", "remote_gpu", "running")
  with client.app.state.database.session_factory() as session:
      n = mark_stale_local_runs_interrupted(session)
  assert n == 2                                  # two running local runs only
  # run_cpu, run_gpu -> interrupted / ANALYSIS_INTERRUPTED
  # run_gpu_pending untouched ; run_remote untouched
  ```
- [ ] In `test_remote_stale_helper_regression.py`, update the import and rename
  `test_stale_helper_interrupts_local_cpu_only` →
  `test_stale_helper_interrupts_local_running_only`; add
  `test_legacy_stale_helper_alias_delegates` (alias returns the same local count).
- [ ] In `test_dataset_experiment_startup_order.py`, update the two monkeypatch
  targets to `"mark_stale_local_runs_interrupted"`.
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_remote_startup_recovery.py \
    backend/tests/test_remote_stale_helper_regression.py \
    backend/tests/test_dataset_experiment_startup_order.py -q
  ```
  Expected RED: `ImportError`/`AttributeError` for `mark_stale_local_runs_interrupted`.

### Step 1.2 — GREEN

- [ ] `backend/app/remote_execution/recovery.py`:
  ```python
  _LOCAL_EXECUTORS = ("local_cpu", "local_gpu")

  def mark_stale_local_runs_interrupted(session: Session) -> int:
      statement = (update(AnalysisRunModel)
          .where(AnalysisRunModel.status == "running")
          .where(AnalysisRunModel.executor.in_(_LOCAL_EXECUTORS))
          .values(status="interrupted", error_type="ANALYSIS_INTERRUPTED",
                  error_message="Previous local analysis process ended before platform restart.",
                  finished_at=datetime.now(timezone.utc)))
      result = session.execute(statement); session.commit()
      return int(result.rowcount or 0)

  mark_stale_local_cpu_runs_interrupted = mark_stale_local_runs_interrupted  # legacy alias
  ```
  Update the module docstring.
- [ ] `backend/app/main.py:140,143` → `mark_stale_local_runs_interrupted`.
- [ ] `backend/app/analysis/service.py:19-27` delegate → `mark_stale_local_runs_interrupted`.
- [ ] Run GREEN on the three files.

### Step 1.3 — Commit
- [ ] `git diff --check && git status --short` → `fix: recover stale local gpu analysis runs`

---

## Task 2 — P2: Generic DatasetExperiment local pending-run repair

**Commit:** `fix: generalize local dataset experiment recovery`

**Files**
- Modify: `backend/app/dataset_experiments/recovery.py`
- Modify tests: `backend/tests/test_dataset_experiment_local_launch_recovery.py`

### Step 2.1 — RED
- [ ] Refactor the recovery-test fixture to accept `executor` and parameterize the
  safe-first-launch and ambiguous tests over `["local_cpu", "local_gpu"]`.
- [ ] Add `test_local_gpu_safe_first_launch_relaunches` (running item + pending
  `local_gpu` run + `launch_requested_at IS NULL` + no worker, provider present →
  same attempt/run relaunched once, no new Attempt/AnalysisRun).
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_local_launch_recovery.py -q
  ```
  Expected RED: `local_gpu` cases not repaired (`:176` skips them).

### Step 2.2 — GREEN
- [ ] `_LOCAL_EXECUTORS = ("local_cpu", "local_gpu")`; change `:176` to
  `if run.executor not in _LOCAL_EXECUTORS or run.status != "pending": continue`;
  update docstring.
- [ ] Run GREEN.

### Step 2.3 — Commit
- [ ] Regression on `test_dataset_experiment_start.py`, `..._attempt.py`, `..._launch.py`
  → `fix: generalize local dataset experiment recovery`

---

## Task 3 — P3 seam: frozen execution-authority validation (full identity)

**Commit:** `feat: add frozen execution authority validation seam`

**Files**
- Modify: `backend/app/remote_execution/runtime.py`
- Create test: `backend/tests/test_execution_authority.py`

**Owner:** `ExecutorRegistry` (owns providers, descriptors, certificates).

**Interface produced:**
```python
FROZEN_AUTHORITY_ITEM_CODES = ("EXECUTION_CAPABILITY_UNAVAILABLE", "EXECUTION_NOT_CERTIFIED")

def validate_frozen_execution_authority(
    self,
    definition: PipelineDefinition,
    model_release_id: str | None,
    executor: str,
    frozen_descriptor: RuntimeDescriptor,
) -> ExecutorProvider:
    """Cheap, deterministic; NEVER calls provider.probe().

    Order (fail closed):
      1. provider = self._providers.get(executor)          -> EXECUTION_CAPABILITY_UNAVAILABLE
      2. provider.runtime_descriptor().to_metadata()
           == frozen_descriptor.to_metadata()              -> RUNTIME_DESCRIPTOR_INVALID
      3. self.certified_capability(definition, model_release_id, executor)
           is not None                                     -> EXECUTION_NOT_CERTIFIED
    Returns provider on success.
    """
```
**Correction 1/5 rationale:** the comparison is the **full internal**
`to_metadata()` (includes `environment_ref` + `environment_label`), exactly
matching current `revalidate_frozen_identity` section 8 and current
`recovery.py` behavior. This prevents a different runtime generation that happens
to share executor/device/precision from being accepted, and prevents a valid
certificate for another generation from hijacking an old frozen experiment.
Verification order matters: the descriptor equality test precedes certificate
lookup, so a generation change fails as `RUNTIME_DESCRIPTOR_INVALID` before any
current-generation certificate can be considered.

### Step 3.1 — RED
- [ ] Create `backend/tests/test_execution_authority.py`:
  ```text
  test_authority_ok_returns_provider
  test_authority_missing_provider_raises_EXECUTION_CAPABILITY_UNAVAILABLE
  test_authority_full_descriptor_mismatch_raises_RUNTIME_DESCRIPTOR_INVALID
  test_authority_environment_identity_mismatch_raises_RUNTIME_DESCRIPTOR_INVALID
      # same executor/device/precision; frozen environment_label A vs provider B
  test_authority_other_generation_certificate_cannot_hijack
      # provider generation B HAS a valid cert, but frozen is A -> RUNTIME_DESCRIPTOR_INVALID
  test_authority_missing_certificate_raises_EXECUTION_NOT_CERTIFIED
  test_authority_wrong_runtime_ref_raises_EXECUTION_NOT_CERTIFIED
  test_authority_does_not_call_provider_probe   # spy asserts probe() not invoked
  ```
- [ ] Run RED → `AttributeError`.

### Step 3.2 — GREEN
- [ ] Implement the method and `FROZEN_AUTHORITY_ITEM_CODES` in `runtime.py` as above.

### Step 3.3 — Commit
- [ ] Regression `test_executor_registry.py`, `test_executor_cutover.py`,
  `test_execution_certificate.py` → `feat: add frozen execution authority validation seam`

---

## Task 4 — P3 wiring + pending-Run terminalization + classification

**Commit:** `fix: revalidate execution authority before launch intent`

**Files**
- Modify: `backend/app/dataset_experiments/service.py`
- Modify: `backend/app/dataset_experiments/recovery.py`
- Modify: `backend/app/dataset_experiments/coordinator.py`
- Modify tests: `backend/tests/test_dataset_experiment_launch.py`,
  `test_dataset_experiment_coordinator_scheduling.py`,
  `test_dataset_experiment_local_launch_recovery.py`

### 4.0 — New fail-close seam (Correction 2)

**Owner:** `DatasetExperimentService` (`service.py`). Generalize the existing
recovery-side `fail_closed_pending_local_run` CAS into one reusable method:
```python
def fail_closed_unlaunched_run(
    self, *, experiment_id: str, coordinator_token: str,
    item_id: str, attempt_id: str, run_id: str,
    error_type: str, error_message: str,
) -> str:
    """Generation-fenced pending -> interrupted for an unlaunched run.
    Fence: EXISTS(experiment running + coordinator_token) AND
           EXISTS(item running for experiment) AND
           EXISTS(attempt binds item + run).
    Returns 'interrupted' | 'already_terminal'.
    Raises DATASET_EXPERIMENT_FENCE_LOST (stale generation, run not terminal)
       or DATASET_EXPERIMENT_INVARIANT_VIOLATION (409)."""
```
- Same SQL as `fail_closed_pending_local_run` (`recovery.py:85-152`) but with
  caller-supplied `error_type`/`error_message`; terminal state is
  `status="interrupted"`, `finished_at=now`.
- `recovery.py::fail_closed_pending_local_run` delegates to this method
  (preserving its existing signature/return contract and its
  `ANALYSIS_LAUNCH_AMBIGUOUS` error type) so there is exactly one implementation.

### 4.1 — RED
- [ ] `test_dataset_experiment_launch.py`:
  `test_authority_missing_before_intent_terminalizes_run`:
  seed running experiment/item/attempt/`pending` run with the frozen provider
  absent from the registry; call `launch_item_attempt`; assert:
  - raises `EXECUTION_CAPABILITY_UNAVAILABLE`;
  - `attempt.launch_requested_at is None` (no intent);
  - `run.status == "interrupted"`, `run.error_type == "EXECUTION_CAPABILITY_UNAVAILABLE"`,
    `run.worker_pid is None`;
  - `item` remains `running` (the coordinator projects it to `failed` on the next
    `reconcile_items`, which is now consistent because the run is terminal).
- [ ] `test_dataset_experiment_launch.py`:
  `test_authority_fail_close_requires_generation` (stale token →
  `fail_closed_unlaunched_run` raises `DATASET_EXPERIMENT_FENCE_LOST`; run not
  terminalized by the stale actor).
- [ ] `test_dataset_experiment_coordinator_scheduling.py`:
  `test_provider_disappears_scheduling_fails_item_not_experiment`:
  `coordinator.step` → item `failed`, run `interrupted`, experiment not `failed`;
  continuing steps reach `completed_with_failures`; assert
  `is_experiment_level` is False for `EXECUTION_CAPABILITY_UNAVAILABLE` /
  `EXECUTION_NOT_CERTIFIED` and True for `RUNTIME_DESCRIPTOR_INVALID` / `DATASET_EXPERIMENT_*`.
- [ ] `test_dataset_experiment_local_launch_recovery.py`:
  `test_recovery_provider_absent_fails_closed_run_and_item` and
  `test_recovery_authority_ok_relaunches_local_gpu`.
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_launch.py \
    backend/tests/test_dataset_experiment_coordinator_scheduling.py \
    backend/tests/test_dataset_experiment_local_launch_recovery.py -q
  ```
  Expected RED: run stays `pending` (no terminalization) and/or experiment fails.

### 4.2 — GREEN
- [ ] `service.py::launch_item_attempt`: after `revalidate_frozen_identity` and
  the ownership-chain checks, and **before** `_claim_launch_intent`:
  ```python
  definition = self.registry.get(experiment.plugin_id).definition
  frozen_descriptor = RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)
  try:
      self.executor_registry.validate_frozen_execution_authority(
          definition, experiment.model_release_id, experiment.executor, frozen_descriptor)
  except PlatformError as exc:
      if exc.code in FROZEN_AUTHORITY_ITEM_CODES and coordinator_token is not None:
          self.fail_closed_unlaunched_run(
              experiment_id=experiment.id, coordinator_token=coordinator_token,
              item_id=item.id, attempt_id=attempt.id, run_id=run.id,
              error_type=exc.code, error_message=exc.message)
      raise
  ```
- [ ] `service.py::revalidate_frozen_identity`: remove section 8 (provider +
  certificate + descriptor authority block, current `:391-412`). Keep
  dataset-membership, release, asset-manifest, and frozen-parameter checks
  (experiment-level).
- [ ] `coordinator.py`: move `"EXECUTION_CAPABILITY_UNAVAILABLE"` and
  `"EXECUTION_NOT_CERTIFIED"` from `EXPERIMENT_LEVEL_CODES` to `ITEM_LEVEL_CODES`.
  `RUNTIME_DESCRIPTOR_INVALID` stays unlisted (default experiment-level).
- [ ] `recovery.py::repair_local_pending_runs`: when `launch_item_attempt` raises
  a `FROZEN_AUTHORITY_ITEM_CODES` code, `continue` (the run was terminalized by
  `launch_item_attempt`; do not re-raise and fail the experiment);
  `RUNTIME_DESCRIPTOR_INVALID`/`DATASET_EXPERIMENT_*` still propagate.
- [ ] Run GREEN on the three files.

### 4.3 — Commit
- [ ] Regression `test_dataset_experiment_coordinator_regression.py`,
  `test_dataset_experiment_generation_fence.py`,
  `test_dataset_experiment_attempt.py`, `test_analysis_launch_prepared_run.py`
  → `fix: revalidate execution authority before launch intent`

---

## Task 5 — Single-recording pre-launch authority fail-close (Correction 3)

**Commit:** `fix: fail closed single recording launch on authority loss`

**Files**
- Modify: `backend/app/analysis/service.py`
- Modify tests: `backend/tests/test_analysis_launch_prepared_run.py`

**Owner:** `AnalysisService.launch_prepared_run` (`analysis/service.py:249-312`).

**Required semantics:** a persisted prepared **local** run whose execution
authority disappears before physical launch must become terminal, not stay
`pending`. Scope is local executors only; remote behavior is unchanged.

### Step 5.1 — RED
- [ ] `test_launch_prepared_run_missing_provider_fails_closed` (existing): extend
  to assert `run.status == "failed"` and `run.error_type == "EXECUTION_CAPABILITY_UNAVAILABLE"`.
- [ ] Add `test_launch_prepared_local_run_provider_disappeared_terminalizes_run`
  (registry whose `provider()` raises for a persisted local run → raises
  `EXECUTION_CAPABILITY_UNAVAILABLE`, run terminal `failed`, no worker).
- [ ] Add `test_create_run_provider_disappears_after_prepare_does_not_leave_pending`
  (prepare→commit with provider present, then registry loses the provider before
  launch → no `pending` row remains).
- [ ] Add `test_launch_prepared_remote_provider_missing_unchanged` (remote run with
  no remote provider keeps existing behavior: raises, run not locally
  terminalized — remote recovery owns pending remote runs).
- [ ] Run RED:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_analysis_launch_prepared_run.py \
    backend/tests/test_analysis_create_run_compat.py -q
  ```

### Step 5.2 — GREEN
- [ ] `analysis/service.py`: move provider resolution into the protected path for
  local executors and terminalize on authority loss:
  ```python
  try:
      provider = self.executor_registry.provider(run.executor)
  except PlatformError as exc:
      if run.executor in ("local_cpu", "local_gpu"):
          self._terminalize_unlaunched_local_run(run, exc.code, exc.message)
      raise
  ```
  plus a private `_terminalize_unlaunched_local_run(run, code, message)` setting
  `status="failed"`, `error_type=code`, `error_message` bounded, `finished_at=now`,
  committed only when `run.status == "pending"` and `run.worker_pid is None`.
- [ ] For local executors, also call `validate_frozen_execution_authority` before
  `provider.launch` (using `run.execution_metadata_json["runtime_descriptor"]`,
  `model_release_id`, `pipeline_id`/`version`), terminalizing on
  `FROZEN_AUTHORITY_ITEM_CODES` / `RUNTIME_DESCRIPTOR_INVALID`. Remote path
  unchanged (no descriptor authority call / no local terminalization).
- [ ] Run GREEN.

### Step 5.3 — Commit
- [ ] Regression `test_analysis_create_run_compat.py`,
  `test_analysis_prepare_local.py`, `test_analysis_runs.py`
  → `fix: fail closed single recording launch on authority loss`

---

## Task 6 — No-GPU / profile-change recovery boundary matrix

**Commit:** `test: cover no-gpu local executor recovery boundary`

**Files**
- Create: `backend/tests/test_local_executor_recovery_boundary.py`
- Production code unchanged unless a RED case exposes a defect.

Cases (CPU/DB-only; settings fixture with no `WSP_LOCAL_GPU_*` and a registry
lacking `local_gpu`):
- [ ] **A (P1):** historical `AnalysisRun(executor="local_gpu", status="running")`
  → startup recovery `interrupted`/`ANALYSIS_INTERRUPTED`; `create_app` boots;
  `/api/health` ok.
- [ ] **B (P2/P3):** experiment `item=running`, run `local_gpu`/`pending`,
  `launch_requested_at IS NULL`, provider absent → no intent, no substitution, run
  `interrupted`, item `failed`, experiment → `completed_with_failures`.
- [ ] **C:** run `pending`, `launch_requested_at SET`, `worker_pid NULL`,
  `local_gpu` → existing ambiguous fail-closed (no relaunch/substitution).
- [ ] **D:** queued items with frozen `local_gpu` and provider unavailable after
  cold boot → deterministic item failures → `completed_with_failures` (no infinite
  `running`).
- [ ] Assert: no permanent `pending`/`running` local state; `Item=failed` always
  agrees with a terminal authoritative run.
- [ ] Run RED→GREEN and commit.

---

## Task 7 — Idempotency, fencing, and final regression

**Commit:** `test: harden recovery idempotency and fencing`

### Step 7.1 — RED/GREEN assertions
- [ ] Add:
  ```text
  test_stale_interrupt_idempotent
  test_double_recovery_no_duplicate_attempt_run
  test_double_recovery_no_duplicate_launch_intent
  test_double_recovery_no_duplicate_coordinator
  test_fail_close_unlaunched_run_idempotent        # second call -> already_terminal
  test_fail_close_unlaunched_run_requires_generation
  test_recovery_preserves_generation_fence
  test_authority_check_does_not_bypass_fence
  ```
- [ ] Run:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_dataset_experiment_generation_fence.py \
    backend/tests/test_dataset_experiment_local_launch_recovery.py \
    backend/tests/test_remote_startup_recovery.py \
    backend/tests/test_analysis_launch_prepared_run.py -q
  ```

### Step 7.2 — Full regression (mandatory)
- [ ] ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
  ```
  Require `0 failed, 0 errors`; skips are env-gated, not passes.
- [ ] ML-free boundary:
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
| P1 local_gpu running → interrupted | 1 | same |
| P1 remote_gpu untouched | 1 | same + `test_startup_preserves_remote_pending_and_running` |
| P1 idempotent | 7 | `test_stale_interrupt_idempotent` |
| P2 local_gpu safe first-launch | 2 | `test_local_gpu_safe_first_launch_relaunches` |
| P2 ambiguous local_gpu fail-closed | 2 | `test_ambiguous_pending_local_run_fails_closed_zero_launch[local_gpu]` |
| P2 remote delegated | 2 | `test_remote_pending_run_delegated_no_local_marker_logic` |
| P3 seam ok/provider/cert | 3 | `test_execution_authority.py` |
| P3 full frozen identity (env mismatch) | 3 | `test_authority_environment_identity_mismatch_raises_RUNTIME_DESCRIPTOR_INVALID` |
| P3 other-generation cert cannot hijack | 3 | `test_authority_other_generation_certificate_cannot_hijack` |
| P3 no live probe | 3 | `test_authority_does_not_call_provider_probe` |
| P3 authority before intent; no claim | 4 | `test_authority_missing_before_intent_terminalizes_run` |
| P3 pending Run terminalized on authority loss | 4 | same (`run.status == "interrupted"`) |
| P3 item/experiment classification | 4 | `test_provider_disappears_scheduling_fails_item_not_experiment` |
| P3 recovery provider-absent fail-closed | 4 | `test_recovery_provider_absent_fails_closed_run_and_item` |
| Single recording provider lost → terminal, no pending | 5 | `test_launch_prepared_local_run_provider_disappeared_terminalizes_run`, `test_create_run_provider_disappears_after_prepare_does_not_leave_pending` |
| Single recording remote unchanged | 5 | `test_launch_prepared_remote_provider_missing_unchanged` |
| No-GPU Case A/B/C/D | 6 | `test_local_executor_recovery_boundary.py` |
| No substitution | 2/4/5/6 | cases B/C/D + ambiguous + single-recording |
| Fencing protects fail-close | 4/7 | `test_fail_close_unlaunched_run_requires_generation`, `test_authority_check_does_not_bypass_fence` |
| Full regression | 7 | `pytest backend/tests -q` |
| ML-free control plane | 7 | `.venv find_spec` check |

## Expected Production Files

```text
backend/app/remote_execution/recovery.py          (P1)
backend/app/analysis/service.py                   (P1 delegate + single-recording fail-close)
backend/app/main.py                               (P1 call site)
backend/app/dataset_experiments/recovery.py       (P2 + authority fail-close delegation)
backend/app/dataset_experiments/service.py        (P3 authority call, fail_closed_unlaunched_run, section 8 removal)
backend/app/dataset_experiments/coordinator.py    (P3 classification)
backend/app/remote_execution/runtime.py           (P3 seam + FROZEN_AUTHORITY_ITEM_CODES)
```

## Forbidden Scope

```text
Auto resolver / Auto API fields        frontend
runtime doctor / certificate installer
scientific pipelines (CPN/ZoomSpec preprocessing/detector/AHLP/FRN/postprocess)
model assets / label spaces
DB schema / migrations                 remote SSH architecture
execution certificate content          Dataset qualification
runtime portability semantics (belongs to the later runtime/certificate plan)
```

## Plan Self-Review

- [ ] Frozen `RuntimeDescriptor` comparison is the full `to_metadata()` equality —
  not weaker than current `revalidate_frozen_identity`.
- [ ] A new valid certificate for another runtime generation cannot hijack an old
  frozen Experiment (descriptor equality is checked before certificate lookup).
- [ ] Dataset authority loss after Transaction A cannot leave a `pending` Run
  (terminalized via generation-fenced `fail_closed_unlaunched_run`).
- [ ] `Item=failed` always agrees with a terminal authoritative Run.
- [ ] Single-recording launch authority loss cannot leave a `pending` Run;
  remote behavior explicitly regression-tested/unchanged.
- [ ] `launch_requested_at` remains `NULL` when authority fails before Transaction B.
- [ ] No worker launches on authority loss.
- [ ] No executor substitution.
- [ ] Fencing protects every fail-close write.
- [ ] remote_gpu semantics unchanged (local-only predicates/corrections; explicit
  remote test).
- [ ] Full regression mandatory; ML-free boundary asserted.
- [ ] No TODO/TBD/placeholders; every step names exact files/functions/tests.
- [ ] Every named function/path exists at `513dce3` (verified:
  `mark_stale_local_cpu_runs_interrupted` recovery.py:22; `repair_local_pending_runs`
  recovery.py:155; `fail_closed_pending_local_run` recovery.py:85;
  `launch_item_attempt` service.py:572; `_claim_launch_intent` service.py:697;
  `revalidate_frozen_identity` service.py:287 (section 8 :391-412);
  `_mark_item_failed` service.py:1299; `is_experiment_level` coordinator.py:56;
  `certified_capability` runtime.py:265; `launch_prepared_run` analysis/service.py:249,
  provider lookup :285).

## Known Interactions (called out)

- Moving `EXECUTION_CAPABILITY_UNAVAILABLE` / `EXECUTION_NOT_CERTIFIED` to
  item-level changes only the coordinator's scheduling classification
  (`coordinator.py:133,144`); `create_experiment` raises synchronously to the API;
  `recover_dataset_experiments` handles the repair path explicitly.
- `RUNTIME_DESCRIPTOR_INVALID` (frozen generation mismatch) remains
  experiment-level; it is a frozen-identity violation, not a recoverable
  deployment condition.
- The single-recording fail-close is scoped to local executors; remote `pending`
  runs remain owned by remote recovery/coordination.
