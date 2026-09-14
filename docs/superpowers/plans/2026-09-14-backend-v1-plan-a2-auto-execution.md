# Backend V1 Plan A2 Auto Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, explainable, portable Auto execution-selection policy to Backend V1 — for both single `AnalysisRun` and `DatasetExperiment` — without persisting `executor="auto"`, without fallback, without plugin-id branches, and without any real GPU/CUDA/AutoDL dependency.

**Architecture:** A new torch-free `app/execution_selection/` package owns the policy. A pure module (`policy.py`) owns workload classification, deterministic ranking, and reason-code generation. A thin resolver (`resolver.py`) collects candidate facts (`technical` / `configured` / `certified` / `available`) by reusing `ExecutorRegistry` and existing availability seams, then calls the pure policy. Services (`AnalysisService`, `DatasetExperimentService`) supply facts and freeze the resolved exact executor; they never duplicate ranking logic. Auto resolves exactly once before persistence; after resolution the persisted executor is an exact executor (`local_cpu` / `local_gpu` / `remote_gpu`) and behaves identically to a manual selection (no runtime fallback, A1 recovery unchanged). Provenance is persisted metadata-only into existing JSON columns (no DB migration).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, pytest (control-plane venv is ML-free).

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

## Global Constraints

- **GPU REQUIRED: NO** for every task. No CUDA, no AutoDL, no real inference. All Auto behavior must be testable with deterministic provider/certificate/availability doubles and SQLite.
- **Auto is a pre-persistence selection policy, never an executor.** Valid persisted executors are only `local_cpu`, `local_gpu`, `remote_gpu`. A persisted `executor="auto"` is forbidden anywhere.
- **No fallback.** Once resolved, the run/experiment stays bound to the resolved executor; provider loss follows the existing lifecycle (A1 Window A/B/C). No runtime substitution.
- **No plugin-id branches.** Production selection logic must never special-case an individual plugin (no per-plugin conditional anywhere). No learned scheduler, no cost optimizer, no opaque weighted score.
- **No Linux/cgroup/proc/nvidia-smi dependency in production selection.** Core Auto imports must not reference `os`, `subprocess`, cgroup v2 paths, `/proc`, PSI, `nvidia-smi`, `scripts/bhq3_memory_gate.py`, `torch`, or `ultralytics`.
- **No DB migration.** Provenance is metadata-only, persisted into existing JSON columns. Do not add schema columns; do not touch `parameters_json` (scientific).
- **A1 preserved.** `RUNTIME_DESCRIPTOR_INVALID` stays experiment-level; `FROZEN_AUTHORITY_ITEM_CODES` is unchanged; recovery/retry never re-resolve Auto.
- **Do not modify** `feature/backend-v1-final-qualification`, `main`, `feature/v1-core`, `fix/a1-1-runtime-drift`.
- Every production task is RED → GREEN. Every test command uses the existing control-plane venv on Windows.

### Closed sets (authoritative)

Workload classes: `SMALL`, `GPU_BENEFICIAL`, `UNKNOWN`.

Reason codes (V1 closed set — no others may be invented):

```text
AUTO_NO_RUNNABLE_EXECUTOR
AUTO_ONLY_RUNNABLE_EXECUTOR
AUTO_LOCAL_CPU_PREFERRED
AUTO_LOCAL_GPU_PREFERRED
AUTO_REMOTE_GPU_PREFERRED
AUTO_UNKNOWN_RECOMMENDED_EXECUTOR
AUTO_UNKNOWN_DETERMINISTIC_RANK
```

Policy constants (one place only — `app/execution_selection/policy.py`):

```text
GPU_PREFER_DURATION_S = 0.05
GPU_PREFER_SAMPLES    = 3_000_000
GPU_BATCH_ITEMS       = 8
```

Rankings:

```text
SMALL:          local_cpu > local_gpu > remote_gpu
GPU_BENEFICIAL: local_gpu > remote_gpu > local_cpu
UNKNOWN:        recommended_execution if runnable, else local_gpu > local_cpu > remote_gpu
```

### Request-mode contract (explicit, backward compatible)

Single run — `POST /api/analysis-runs` (`AnalysisRunCreate`):

| mode | executor supplied | outcome |
|---|---|---|
| `manual` (default) | omitted | resolve to historical default `local_cpu` (exact behavior preserved) |
| `manual` | present | exact requested executor via `availability_for` (unchanged) |
| `auto` | omitted | Auto resolver selects exactly one executor before persistence |
| `auto` | present | fail closed `EXECUTION_REQUEST_INVALID` (conflicting intent) |

Dataset — `POST /api/dataset-experiments` (`DatasetExperimentCreate`):

| mode | executor supplied | outcome |
|---|---|---|
| `manual` (default) | present | exact requested executor (unchanged) |
| `manual` | omitted | fail closed `EXECUTION_REQUEST_INVALID` (there is no historical default) |
| `auto` | omitted | Auto resolver runs once, freezes one exact executor for the whole experiment |
| `auto` | present | fail closed `EXECUTION_REQUEST_INVALID` (conflicting intent) |

### Provenance design (metadata-only, no migration)

Required concepts: `requested_execution_mode`, `resolved_executor`, `auto_reason_code`, `auto_reason`, `workload_class`.

- **Single `AnalysisRun`**: additive keys in the existing `analysis_runs.execution_metadata_json` JSON column.
  - `manual`: `{"requested_execution_mode": "manual", "resolved_executor": "<executor>"}`.
  - `auto`: adds `"auto_reason_code"`, `"auto_reason"`, `"workload_class"`.
  - Public read model `analysis/schema.py::RemoteExecutionMetadataRead` (the public execution-metadata allowlist) is extended additively with `requested_execution_mode`, `auto_reason_code`, `auto_reason`, `workload_class` (all optional). `resolved_executor` is already the top-level `AnalysisRunRead.executor`.
- **`DatasetExperiment`**: additive namespaced sub-object `auto_selection` inside the existing `dataset_experiments.runtime_descriptor_json` JSON column.
  - The descriptor's own keys (`executor`, `device_type`, `device_index`, `precision`, `environment_ref`, `environment_label`) remain the authoritative frozen execution identity.
  - `RuntimeDescriptor.from_metadata(...)` reads only the known descriptor keys, so the extra `auto_selection` key is inert for A1 frozen-authority comparison (`to_metadata()` equality is unaffected).
  - `parameters_json` is never touched.
- **Deferred alternative (documented, not implemented):** if a later API-contract freeze requires a dedicated column, add a nullable additive JSON column via the existing `run_additive_migrations` seam. A2 does not do this.

### Public information boundary

The new Auto API returns only booleans (`technical`, `configured`, `certified`, `available`) and bounded reason fields (`reason_code`, `reason`, `reason_message`). It MUST NOT return `environment_ref`, interpreter/asset paths, SSH material, certificate internals/evidence, cgroup/PSI/`/proc` diagnostics, or host-private runtime paths.

---

## Module / type reference (single source of truth)

New package `backend/app/execution_selection/`:

```text
backend/app/execution_selection/__init__.py
backend/app/execution_selection/policy.py      # pure: constants, classification, ranking, reason codes
backend/app/execution_selection/resolver.py    # facts: candidate collection + selection orchestration
backend/app/execution_selection/schema.py      # response read models (public allowlist)
backend/app/execution_selection/router.py      # GET /api/executor-selection
```

`policy.py` public surface:

```python
class WorkloadClass(str, Enum):
    SMALL = "SMALL"
    GPU_BENEFICIAL = "GPU_BENEFICIAL"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class AutoDecision:
    resolved_executor: str | None
    reason_code: str
    workload_class: WorkloadClass

def classify_workload(*, duration_s: float | None, num_samples: int | None,
                      dataset_item_count: int | None) -> WorkloadClass: ...
def rank_for(workload_class: WorkloadClass) -> tuple[str, ...]: ...
def select_executor(*, runnable: tuple[str, ...], workload_class: WorkloadClass,
                    recommended_execution: str | None) -> AutoDecision: ...
```

`resolver.py` public surface:

```python
@dataclass(frozen=True)
class ExecutionCandidate:
    executor: str
    technical: bool
    configured: bool
    certified: bool
    available: bool
    reason_code: str | None
    reason_message: str | None

@dataclass(frozen=True)
class ExecutionSelection:
    requested_mode: str            # "manual" | "auto"
    resolved_executor: str | None  # exact executor; None only when auto fails closed
    reason_code: str
    reason: str
    workload_class: str
    candidates: tuple[ExecutionCandidate, ...]

def collect_candidates(*, definition, model_release, recording, executor_registry
                       ) -> tuple[ExecutionCandidate, ...]: ...
def resolve_auto_execution(*, definition, model_release, recording, executor_registry,
                           dataset_item_count: int | None = None) -> ExecutionSelection: ...
```

Facts are derived by reusing existing seams only: `definition.technical_execution_capabilities`, `executor_registry.providers()`, `executor_registry.certified_capability(definition, model_release_id, executor)`, and (single-run scope only) `executor_registry.availability_for(definition, model_release, recording, executor)`. No new availability engine.

Scope semantics for `available`:
- **Single-run scope (`recording` not None):** `available` = live `availability_for(...).available` (input + provider probe).
- **Dataset scope (`recording` is None):** there is no single input; consistent with existing `DatasetExperimentService._validate_execution` (deployment identity only, never probes a recording), `available` is reported as `configured` (deployment-level). Runnable = `technical ∧ configured ∧ certified`. This mirrors the existing experiment-creation contract and is documented in the API response.

Reason text (`reason`) is a bounded, human-readable, path/secret-free string derived from the selected `reason_code`.

---

## Task 1: Pure Auto policy core (constant + classification + ranking + reason codes)

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/execution_selection/__init__.py` (empty package marker)
- Create: `backend/app/execution_selection/policy.py`
- Test: `backend/tests/test_execution_selection_policy.py`

**Interfaces:**
- `WorkloadClass`, `AutoDecision`, `AUTO_POLICY` constants, reason-code constants, `rank_for`, `classify_workload`, `select_executor` (exact signatures in the Module / type reference above).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_execution_selection_policy.py`:

```python
import importlib
import sys

import pytest

from app.execution_selection import policy
from app.execution_selection.policy import (
    AUTO_LOCAL_CPU_PREFERRED,
    AUTO_LOCAL_GPU_PREFERRED,
    AUTO_NO_RUNNABLE_EXECUTOR,
    AUTO_ONLY_RUNNABLE_EXECUTOR,
    AUTO_REMOTE_GPU_PREFERRED,
    AUTO_UNKNOWN_DETERMINISTIC_RANK,
    AUTO_UNKNOWN_RECOMMENDED_EXECUTOR,
    GPU_BATCH_ITEMS,
    GPU_PREFER_DURATION_S,
    GPU_PREFER_SAMPLES,
    WorkloadClass,
    classify_workload,
    select_executor,
)


def test_policy_constants_are_single_sourced():
    assert GPU_PREFER_DURATION_S == 0.05
    assert GPU_PREFER_SAMPLES == 3_000_000
    assert GPU_BATCH_ITEMS == 8


def test_classify_small_when_within_thresholds():
    assert classify_workload(duration_s=0.05, num_samples=3_000_000, dataset_item_count=7) is WorkloadClass.SMALL


def test_classify_gpu_beneficial_on_duration():
    assert classify_workload(duration_s=0.0500001, num_samples=None, dataset_item_count=None) is WorkloadClass.GPU_BENEFICIAL


def test_classify_gpu_beneficial_on_samples():
    assert classify_workload(duration_s=None, num_samples=3_000_001, dataset_item_count=None) is WorkloadClass.GPU_BENEFICIAL


def test_classify_gpu_beneficial_on_dataset_count():
    assert classify_workload(duration_s=None, num_samples=None, dataset_item_count=8) is WorkloadClass.GPU_BENEFICIAL


def test_classify_unknown_when_no_size_available():
    assert classify_workload(duration_s=None, num_samples=None, dataset_item_count=None) is WorkloadClass.UNKNOWN


def test_no_runnable_is_fail_closed():
    decision = select_executor(runnable=(), workload_class=WorkloadClass.SMALL, recommended_execution=None)
    assert decision.resolved_executor is None
    assert decision.reason_code == AUTO_NO_RUNNABLE_EXECUTOR


def test_only_runnable_is_selected():
    decision = select_executor(runnable=("remote_gpu",), workload_class=WorkloadClass.SMALL, recommended_execution=None)
    assert decision.resolved_executor == "remote_gpu"
    assert decision.reason_code == AUTO_ONLY_RUNNABLE_EXECUTOR


def test_small_ranking_prefers_local_cpu():
    decision = select_executor(runnable=("local_cpu", "local_gpu", "remote_gpu"),
                               workload_class=WorkloadClass.SMALL, recommended_execution="local_gpu")
    assert decision.resolved_executor == "local_cpu"
    assert decision.reason_code == AUTO_LOCAL_CPU_PREFERRED


def test_gpu_beneficial_ranking_prefers_local_gpu():
    decision = select_executor(runnable=("local_cpu", "local_gpu", "remote_gpu"),
                               workload_class=WorkloadClass.GPU_BENEFICIAL, recommended_execution="local_cpu")
    assert decision.resolved_executor == "local_gpu"
    assert decision.reason_code == AUTO_LOCAL_GPU_PREFERRED


def test_gpu_beneficial_ranking_remote_over_cpu():
    decision = select_executor(runnable=("local_cpu", "remote_gpu"),
                               workload_class=WorkloadClass.GPU_BENEFICIAL, recommended_execution=None)
    assert decision.resolved_executor == "remote_gpu"
    assert decision.reason_code == AUTO_REMOTE_GPU_PREFERRED


def test_unknown_uses_recommended_when_runnable():
    decision = select_executor(runnable=("local_cpu", "local_gpu"),
                               workload_class=WorkloadClass.UNKNOWN, recommended_execution="local_gpu")
    assert decision.resolved_executor == "local_gpu"
    assert decision.reason_code == AUTO_UNKNOWN_RECOMMENDED_EXECUTOR


def test_unknown_ignores_recommended_when_not_runnable():
    decision = select_executor(runnable=("local_cpu",),
                               workload_class=WorkloadClass.UNKNOWN, recommended_execution="remote_gpu")
    assert decision.resolved_executor == "local_cpu"
    assert decision.reason_code == AUTO_UNKNOWN_DETERMINISTIC_RANK


def test_policy_module_has_no_linux_or_ml_dependency():
    importlib.import_module("app.execution_selection.policy")
    source = __import__("inspect").getsource(policy)
    for forbidden in ("cgroup", "/proc", "nvidia-smi", "bhq3_memory_gate", "torch", "ultralytics", "subprocess", "import os"):
        assert forbidden not in source
    assert "torch" not in sys.modules or sys.modules["torch"] is None  # no accidental torch import
```

- [ ] **Step 2: Run and confirm RED**

```bash
& "D:\LGFiles\Wideband Signal Analysis Platform\Wideband-Intelligent-Signal-Analysis-Platform\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_policy.py -q
```

Expected RED reason: `ModuleNotFoundError: No module named 'app.execution_selection'`.

- [ ] **Step 3: Minimal implementation**

Create `backend/app/execution_selection/__init__.py` (empty) and `backend/app/execution_selection/policy.py` with exactly the constants, `WorkloadClass`, `AutoDecision`, `rank_for`, `classify_workload`, `select_executor` specified in the Module / type reference. Import only `dataclasses`, `enum`, `typing`. `select_executor` must return `AutoDecision(None, AUTO_NO_RUNNABLE_EXECUTOR, workload_class)` for an empty runnable set, `AUTO_ONLY_RUNNABLE_EXECUTOR` for exactly one, the class ranking for `SMALL`/`GPU_BENEFICIAL`, and the `UNKNOWN` branch (`recommended_execution` if runnable else `UNKNOWN` fallback rank).

- [ ] **Step 4: Run GREEN**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_policy.py -q
```

Expected: all tests pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/execution_selection/__init__.py backend/app/execution_selection/policy.py backend/tests/test_execution_selection_policy.py
git commit -m "feat: add portable auto execution selection policy"
```

---

## Task 2: Candidate fact collection + Auto resolver

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/execution_selection/resolver.py`
- Test: `backend/tests/test_execution_selection_resolver.py`

**Interfaces:**
- `ExecutionCandidate`, `ExecutionSelection`, `collect_candidates`, `resolve_auto_execution` (exact signatures in the Module / type reference).
- Reuses `ExecutorRegistry` from `app/remote_execution/runtime.py` and the shared `FakeProvider`/`FakeRegistry` from `backend/tests/executor_fixtures.py` plus the real `ExecutorRegistry` + `ExecutionCertificateStore` for certification coverage.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_execution_selection_resolver.py` covering:

```python
# 1. technical but no provider -> configured False, certified False, available False, runnable excluded
# 2. provider but no exact certificate -> certified False (real ExecutorRegistry + empty store)
# 3. certified but live-unavailable (local provider probe returns unavailable) -> available False
# 4. input incompatible -> availability_for reason_code "INPUT_INCOMPATIBLE", available False
# 5. remote provider unavailable -> available False, reason_code "REMOTE_EXECUTOR_UNAVAILABLE"
# 6. two/three runnable -> policy ranking applied, reason code from the selected executor
# 7. zero runnable -> resolved_executor None, AUTO_NO_RUNNABLE_EXECUTOR
# 8. dataset scope (recording None, dataset_item_count=8) -> GPU_BENEFICIAL, available == configured
```

Each test constructs a `PipelineDefinition` (with `technical_execution_capabilities` and `recommended_execution`), a `RecordingModel`-like stub, a `FakeProvider`/`FakeRegistry` or real `ExecutorRegistry`, then calls `collect_candidates(...)` / `resolve_auto_execution(...)` and asserts the `ExecutionCandidate` booleans and the `ExecutionSelection`.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_resolver.py -q
```

Expected RED reason: `ModuleNotFoundError: No module named 'app.execution_selection.resolver'`.

- [ ] **Step 3: Minimal implementation**

Create `backend/app/execution_selection/resolver.py`:
- Candidate universe = sorted union of `{cap.executor for cap in definition.technical_execution_capabilities}` and `set(executor_registry.providers())`.
- Per candidate compute `technical`, `configured`, `certified = executor_registry.certified_capability(definition, model_release_id, executor) is not None`, and `available`:
  - single-run scope (`recording is not None`): `executor_registry.availability_for(definition, model_release, recording, executor)` (reason fields taken from the result);
  - dataset scope (`recording is None`): `available = configured` with a bounded deployment reason.
- `resolve_auto_execution` builds `runnable = tuple(c.executor for c in candidates if technical and configured and certified and available)`, classifies workload from `duration_s`/`num_samples` (recording scope) or `dataset_item_count` (dataset scope), calls `policy.select_executor(...)`, and returns `ExecutionSelection(requested_mode="auto", ...)`.
- Import only `dataclasses`, `typing`, `app.core.errors`, `app.execution_selection.policy`, and the `RuntimeDescriptor`/`ExecutorRegistry` types (`from app.remote_execution.runtime import ...`). No `os`/`subprocess`/Linux/ML imports.

- [ ] **Step 4: Run GREEN**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_resolver.py -q
```

Expected: all tests pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/execution_selection/resolver.py backend/tests/test_execution_selection_resolver.py
git commit -m "feat: add auto execution candidate resolver"
```

---

## Task 3: Single AnalysisRun manual/Auto request + pre-persistence resolution + provenance

**GPU REQUIRED: NO**

**Files:**
- Modify: `backend/app/analysis/schema.py`
- Modify: `backend/app/analysis/router.py`
- Modify: `backend/app/analysis/service.py`
- Test: `backend/tests/test_analysis_auto_execution.py`

**Interfaces:**
- `AnalysisRunCreate` gains `execution_mode: Literal["manual", "auto"] = "manual"` and `executor: str | None = None`.
- `AnalysisService.create_run(*, recording_id, pipeline_id, executor, parameters, model_release_id=None, execution_mode="manual")`.
- `RemoteExecutionMetadataRead` gains optional `requested_execution_mode`, `auto_reason_code`, `auto_reason`, `workload_class`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_analysis_auto_execution.py` covering:
1. `execution_mode` omitted → stored run `executor == "local_cpu"` (historical default preserved).
2. `execution_mode="manual"`, `executor="local_gpu"` → exact `local_gpu` via `availability_for` (unchanged manual path; no substitution, no availability call for other executors).
3. `execution_mode="auto"`, `executor=None` → run persisted with the resolver's exact executor; `run.executor != "auto"`.
4. `execution_mode="auto"`, `executor="local_gpu"` → `PlatformError` code `EXECUTION_REQUEST_INVALID`; no `AnalysisRun` row created.
5. Auto with zero runnable executors → `PlatformError` `AUTO_NO_RUNNABLE_EXECUTOR`; no `AnalysisRun` row created.
6. Auto provenance persisted in `execution_metadata_json`: `requested_execution_mode == "auto"`, `auto_reason_code` in the closed set, `workload_class` in `{SMALL,GPU_BENEFICIAL,UNKNOWN}`, and no `environment_ref`/path/secret keys present.
7. Manual provenance persisted: `requested_execution_mode == "manual"`.
8. Public read model: `GET /api/analysis-runs/{id}` includes `execution_metadata_json.requested_execution_mode` and does not expose `environment_ref`.

Use the existing `executor_fixtures.FakeProvider`/`FakeRegistry` and the `client`/`session` fixtures.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_analysis_auto_execution.py -q
```

Expected RED reason: `TypeError: create_run() got an unexpected keyword argument 'execution_mode'` / `AnalysisRunCreate` rejects `execution_mode`.

- [ ] **Step 3: Minimal implementation**

- `analysis/schema.py`: add `execution_mode` + make `executor` optional; add the four optional provenance fields to `RemoteExecutionMetadataRead`.
- `analysis/router.py`: pass `execution_mode=payload.execution_mode`; apply the single-run default `executor = payload.executor or "local_cpu"` for manual before calling the service (or inside the service).
- `analysis/service.py`:
  - `create_run(..., execution_mode="manual")`:
    - manual with `executor=None` → `executor = "local_cpu"`;
    - auto with `executor is not None` → `PlatformError("EXECUTION_REQUEST_INVALID", ...)`;
    - auto with `executor is None` → load recording + definition + resolved release and call `resolve_auto_execution(...)`; on `resolved_executor is None` raise `PlatformError(selection.reason_code, selection.reason)`; else use the resolved executor.
    - then call `prepare_run(executor=...)` unchanged.
    - After `prepare_run`, merge the provenance dict (`requested_execution_mode`, `resolved_executor`, and for auto `auto_reason_code`/`auto_reason`/`workload_class`) into `run.execution_metadata_json` (new dict assignment), then `commit` + `launch_prepared_run` exactly as today.
  - Do not modify `prepare_run` semantics for the DatasetExperiment path.

- [ ] **Step 4: Run GREEN**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_analysis_auto_execution.py backend/tests/test_analysis_create_run_compat.py backend/tests/test_analysis_prepare_local.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/analysis/schema.py backend/app/analysis/router.py backend/app/analysis/service.py backend/tests/test_analysis_auto_execution.py
git commit -m "feat: add single-run auto execution selection"
```

---

## Task 4: DatasetExperiment Auto resolve-once + exact executor freeze + provenance

**GPU REQUIRED: NO**

**Files:**
- Modify: `backend/app/dataset_experiments/schema.py`
- Modify: `backend/app/dataset_experiments/router.py`
- Modify: `backend/app/dataset_experiments/service.py`
- Test: `backend/tests/test_dataset_experiment_auto_creation.py`

**Interfaces:**
- `DatasetExperimentCreate` gains `execution_mode: Literal["manual", "auto"] = "manual"` and `executor: str | None = None`.
- `DatasetExperimentService.create_experiment(..., executor=None, execution_mode="manual")`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_dataset_experiment_auto_creation.py` covering:
1. manual + `executor` omitted → `PlatformError` `EXECUTION_REQUEST_INVALID`; no experiment row.
2. manual + `executor="local_cpu"` → unchanged (frozen `executor == "local_cpu"`).
3. auto + `executor="local_cpu"` → `PlatformError` `EXECUTION_REQUEST_INVALID`; no experiment row.
4. auto + omitted, small manifest (e.g. 3 items), `local_cpu` runnable → frozen `executor == "local_cpu"`, `reason_code == "AUTO_LOCAL_CPU_PREFERRED"`.
5. auto + omitted, manifest item count ≥ 8 → `workload_class == "GPU_BENEFICIAL"`.
6. auto + omitted, zero runnable → `PlatformError` `AUTO_NO_RUNNABLE_EXECUTOR`; no experiment row.
7. `runtime_descriptor_json["auto_selection"]` persisted with `requested_execution_mode`, `resolved_executor`, `auto_reason_code`, `auto_reason`, `workload_class`; `RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)` still parses and `to_metadata()` equals the frozen descriptor (the extra key is inert).
8. `parameters_json` equals the request `parameters` (scientific parameters untouched).
9. `retry_failed`/recovery keep the frozen executor (assert the frozen `executor` is unchanged after a retry-failed transition).

Use the shared `dataset_experiment_fixtures` (`seed_dataset`, `create_experiment`) and `executor_fixtures`.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_dataset_experiment_auto_creation.py -q
```

Expected RED reason: `DatasetExperimentCreate` requires `executor` / rejects `execution_mode`; auto path absent.

- [ ] **Step 3: Minimal implementation**

- `dataset_experiments/schema.py`: add `execution_mode` + make `executor` optional (keep `extra="forbid"`).
- `dataset_experiments/router.py`: pass `execution_mode=payload.execution_mode` and `executor=payload.executor`.
- `dataset_experiments/service.py::create_experiment`:
  - build `manifest` (already) → `dataset_item_count = manifest.expected_recordings`;
  - resolve release (already) → `frozen_release_id`;
  - mode handling: manual + `executor is None` → `EXECUTION_REQUEST_INVALID`; auto + `executor is not None` → `EXECUTION_REQUEST_INVALID`; auto + `None` → `resolve_auto_execution(definition=definition, model_release=resolved_release, recording=None, executor_registry=self.executor_registry, dataset_item_count=dataset_item_count)`; on `resolved_executor is None` raise `PlatformError(selection.reason_code, selection.reason)`; else use the resolved executor;
  - `_validate_execution(definition, frozen_release_id, executor)` unchanged (validates provider + certificate);
  - persist `runtime_descriptor_json = {**descriptor.to_metadata(), "auto_selection": {...}}` (provenance for both modes; auto fields present only for auto);
  - `parameters_json = dict(parameters)` unchanged.
  - No per-item Auto anywhere; `start_item_attempt`/`launch_item_attempt`/`retry_failed`/recovery are unchanged and consume the frozen `executor`.

- [ ] **Step 4: Run GREEN**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_dataset_experiment_auto_creation.py backend/tests/test_dataset_experiment_creation.py backend/tests/test_dataset_experiment_launch.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/dataset_experiments/schema.py backend/app/dataset_experiments/router.py backend/app/dataset_experiments/service.py backend/tests/test_dataset_experiment_auto_creation.py
git commit -m "feat: add dataset experiment auto execution freeze"
```

---

## Task 5: `GET /api/executor-selection` explanation read model

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/execution_selection/schema.py`
- Create: `backend/app/execution_selection/router.py`
- Modify: `backend/app/main.py` (include the router)
- Test: `backend/tests/test_executor_selection_api.py`

**Interfaces:**
- `ExecutorSelectionRead`, `ExecutionCandidateRead` (Pydantic response models; booleans + bounded reasons only).
- `GET /api/executor-selection` query contract:
  - `pipeline_id: str` (required)
  - `model_release_id: str | None`
  - single-run scope: `recording_id: str | None`
  - dataset scope: `dataset_name: str | None`, `dataset_split: str | None`, `dataset_label_space: str | None`
  - exactly one scope required (fail closed otherwise).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_executor_selection_api.py` covering:
1. single-run scope returns `requested_mode == "auto"`, `resolved_executor` in the closed exact-executor set, `reason_code` in the closed set, `workload_class` in the closed set, and a `candidates` list where each entry has the four booleans.
2. dataset scope (item count ≥ 8) returns `workload_class == "GPU_BENEFICIAL"` and selects per the GPU ranking.
3. neither scope (no `recording_id`, no dataset trio) → `PlatformError` `EXECUTION_SELECTION_REQUEST_INVALID`.
4. both scopes supplied → `EXECUTION_SELECTION_REQUEST_INVALID`.
5. zero runnable → `resolved_executor is None`, `reason_code == "AUTO_NO_RUNNABLE_EXECUTOR"`.
6. public boundary: the JSON response contains no `environment_ref`, no path-like substring, no `certificate`, no `cgroup`/`psi`, and no SSH material.
7. `technical but no provider` candidate reports `technical=true, configured=false, certified=false, available=false`.
8. `provider but no certificate` candidate reports `configured=true, certified=false, available=false`.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_executor_selection_api.py -q
```

Expected RED reason: route absent → `404` for `GET /api/executor-selection` (or `ModuleNotFoundError` for the new router/schema).

- [ ] **Step 3: Minimal implementation**

- `execution_selection/schema.py`: `ExecutionCandidateRead` (`executor`, `technical`, `configured`, `certified`, `available`, `reason_code`, `reason_message`) and `ExecutorSelectionRead` (`requested_mode`, `resolved_executor`, `reason_code`, `reason`, `workload_class`, `candidates`).
- `execution_selection/router.py`: `GET /api/executor-selection`; validate the exactly-one-scope rule (raise `PlatformError("EXECUTION_SELECTION_REQUEST_INVALID", ...)`); resolve `definition` via `request.app.state.pipeline_registry`; resolve the release via `request.app.state.model_release_store` (`_resolve_release` semantics); single scope loads the `RecordingModel` (404 `RECORDING_NOT_FOUND` if missing) and passes it to `resolve_auto_execution`; dataset scope computes `dataset_item_count` via `DatasetBenchmarkService(session).prepare_manifest(dataset_name, dataset_split, dataset_label_space).expected_recordings` and passes `recording=None`.
- `main.py`: `include_router(execution_selection_router)`.
- Response maps only the booleans + bounded `reason_code`/`reason`/`reason_message`; never serialize `environment_ref` or any `RuntimeDescriptor.to_metadata()` payload.

- [ ] **Step 4: Run GREEN**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_executor_selection_api.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/execution_selection/schema.py backend/app/execution_selection/router.py backend/app/main.py backend/tests/test_executor_selection_api.py
git commit -m "feat: add executor selection explanation endpoint"
```

---

## Task 6: Manual compatibility + fail-closed matrix + portability sweep (verification)

**GPU REQUIRED: NO**

**Verification task — introduces NO production code.** The fail-closed behavior it exercises was implemented (with its own RED evidence) in Tasks 3–5; this task authors the dedicated matrix/portability test file and runs it. Its gate is GREEN, not a RED→GREEN cycle, because no production code changes here.

**Files:**
- Test: `backend/tests/test_execution_selection_failclosed_matrix.py`

- [ ] **Step 1: Write the matrix tests**

Create `backend/tests/test_execution_selection_failclosed_matrix.py` covering, against the implemented behavior:
1. zero runnable executors → fail closed, no persisted Run/Experiment.
2. exactly one runnable → selected with `AUTO_ONLY_RUNNABLE_EXECUTOR`.
3. two runnable → ranking applied.
4. three runnable → ranking applied.
5. technical but no provider → excluded.
6. provider but no exact certificate → excluded.
7. certified but live-unavailable → excluded.
8. input incompatible → excluded.
9. remote provider unavailable → excluded.
10. unknown workload size → `UNKNOWN` branch (recommended-if-runnable else deterministic rank).
11. recommended executor unavailable → not selected.
12. recommended executor uncertified → not selected.
13. manual exact executors (`local_cpu`, `local_gpu`, `remote_gpu`) each still pass through `availability_for` unchanged.
14. no persisted `AnalysisRun.executor == "auto"` and no `DatasetExperiment.executor == "auto"` in any scenario.

- [ ] **Step 2: Run (gate GREEN)**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_failclosed_matrix.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 3: Portability sweep**

Assert (in the same file or a small dedicated test) that `app.execution_selection.policy` and `app.execution_selection.resolver` source contain none of `cgroup`, `nvidia-smi`, `bhq3_memory_gate`, `/proc`, `torch`, `ultralytics`, `subprocess`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_execution_selection_failclosed_matrix.py
git commit -m "test: cover auto execution fail-closed matrix and portability"
```

---

## Task 7: A1 recovery/fencing regression + focused regression + ML-free verification

**GPU REQUIRED: NO**

**Verification task — introduces NO production code.** Confirms A1 is not weakened and the control plane stays ML-free. Gate is GREEN.

**Files:**
- Test: `backend/tests/test_execution_selection_a1_regression.py`

- [ ] **Step 1: Write the A1 regression guards**

Create `backend/tests/test_execution_selection_a1_regression.py`:
1. After a DatasetExperiment Auto freeze, `start_item_attempt` uses the frozen executor and `revalidate_frozen_identity` never calls the Auto resolver (assert the resolver is not imported/invoked by `dataset_experiments.service` / `analysis.service` at runtime — patch `app.execution_selection.resolver.resolve_auto_execution` to raise and prove recovery/retry/launch still succeed).
2. A1 Window A/B/C tests in `test_dataset_experiment_launch.py` remain unchanged (referenced, not rewritten).
3. `FROZEN_AUTHORITY_ITEM_CODES` still equals `("EXECUTION_CAPABILITY_UNAVAILABLE", "EXECUTION_NOT_CERTIFIED")` and `RUNTIME_DESCRIPTOR_INVALID` is not item-level.

- [ ] **Step 2: Run (gate GREEN)**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_a1_regression.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 3: Focused regression + ML-free verification**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_policy.py backend/tests/test_execution_selection_resolver.py backend/tests/test_analysis_auto_execution.py backend/tests/test_dataset_experiment_auto_creation.py backend/tests/test_executor_selection_api.py backend/tests/test_execution_selection_failclosed_matrix.py backend/tests/test_execution_selection_a1_regression.py backend/tests/test_analysis_create_run_compat.py backend/tests/test_dataset_experiment_creation.py backend/tests/test_dataset_experiment_launch.py backend/tests/test_dataset_experiment_coordinator_scheduling.py backend/tests/test_pipeline_read_model.py backend/tests/test_executor_registry.py backend/tests/test_execution_certificate.py -q
```

Expected: `0 failed`, `0 errors`.

```bash
& "...\.venv\Scripts\python.exe" -c "import importlib.util as u; print('torch:', u.find_spec('torch')); print('ultralytics:', u.find_spec('ultralytics'))"
```

Expected: `torch: None`, `ultralytics: None`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_execution_selection_a1_regression.py
git commit -m "test: guard A1 recovery and ML-free boundary for auto execution"
```

---

## Plan self-review record

- Every Auto design requirement maps to a task: pure policy (Task 1), candidate facts (Task 2), single-run Auto + provenance (Task 3), dataset Auto freeze + provenance (Task 4), explanation endpoint (Task 5), fail-closed matrix + portability (Task 6), A1/ML-free regression (Task 7).
- No unfinished-marker text; no "similar to Task X" references; each task names its files, interfaces, RED test, exact command, expected RED reason, minimal implementation, GREEN command, and commit.
- Function/type names are internally consistent (`resolve_auto_execution`, `select_executor`, `classify_workload`, `ExecutionCandidate`, `ExecutionSelection`, `AutoDecision`).
- No plugin-id branches anywhere in the designed production code.
- No Linux/cgroup/proc/nvidia-smi dependency; portability guard test asserts absence in the policy/resolver sources.
- No A3 scope: no runtime doctor, no runtime identity generation, no certificate install CLI, no qualification evidence directories; A2 only consumes existing `ExecutorRegistry` / `ExecutionCertificateStore` / `availability_for`.
- No GPU gate: every task is labeled `GPU REQUIRED: NO`; all tests use deterministic providers/certificates/availability doubles and SQLite.
- DatasetExperiment Auto resolves exactly once at creation; all Items use the frozen executor; `retry_failed`/recovery never re-resolve.
- Persisted `executor` is never `"auto"` (single-run and dataset).
- Manual behavior remains exact and fail-closed; no substitution; manual+omitted contracts are explicit per endpoint.
- Reason-code set matches the approved design exactly (7 codes).
- A1 recovery never re-resolves Auto; `RUNTIME_DESCRIPTOR_INVALID` stays experiment-level; `FROZEN_AUTHORITY_ITEM_CODES` unchanged.
- Provenance is metadata-only in existing JSON columns (`analysis_runs.execution_metadata_json`, `dataset_experiments.runtime_descriptor_json["auto_selection"]`); no DB migration; `parameters_json` untouched.
- Public boundary: the Auto API returns only booleans + bounded reasons; no `environment_ref`, paths, SSH material, or certificate internals.