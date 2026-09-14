# Backend V1 Plan A2 Auto Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, explainable, portable Auto execution-selection policy to Backend V1 — for both single `AnalysisRun` and `DatasetExperiment` — without persisting `executor="auto"`, without fallback, without plugin-id branches, and without any real GPU/CUDA/AutoDL dependency.

**Architecture:** A new torch-free `app/execution_selection/` package owns the policy. A pure module (`policy.py`) owns workload classification, deterministic ranking, and reason-code generation. A thin resolver (`resolver.py`) collects candidate facts (`technical` / `configured` / `certified` / `available`) by reusing `ExecutorRegistry` and existing availability seams, then calls the pure policy. Services (`AnalysisService`, `DatasetExperimentService`) supply facts and freeze the resolved exact executor; they never duplicate ranking logic. Auto resolves exactly once before persistence; after resolution the persisted executor is an exact executor (`local_cpu` / `local_gpu` / `remote_gpu`) and behaves identically to a manual selection (no runtime fallback, A1 recovery unchanged). Provenance is persisted metadata-only into existing JSON columns (no DB migration).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite, pytest (control-plane venv is ML-free).

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

## Global Constraints

- **GPU REQUIRED: NO** for every task. No CUDA, no AutoDL, no real inference. All Auto behavior must be testable with deterministic provider/certificate/availability doubles and SQLite.
- **Auto is a pre-persistence selection policy, never an executor.** Valid persisted executors are only `local_cpu`, `local_gpu`, `remote_gpu`. A persisted `executor="auto"` is forbidden anywhere.
- **No fallback.** Once resolved, the run/experiment stays bound to the resolved executor; provider loss follows the existing lifecycle (A1 Window A/B/C). No runtime substitution.
- **No plugin-id branches.** Production selection logic must never special-case an individual plugin (no per-plugin conditional anywhere). No learned scheduler, no cost optimizer, no opaque weighted score.
- **No Linux/cgroup/proc/nvidia-smi dependency in production selection.** Core Auto imports must not reference `os`, `subprocess`, cgroup v2 paths, `/proc`, PSI, `nvidia-smi`, `scripts/bhq3_memory_gate.py`, `torch`, or `ultralytics`.
- **No DB migration.** Provenance is metadata-only, persisted into existing JSON columns. Do not add schema columns; do not touch `parameters_json` (scientific).
- **Live availability always required.** `runnable = technical ∧ configured ∧ certified ∧ live-available`. `configured` is NEVER an alias for `available`, for single-run AND DatasetExperiment Auto. A registered + certified `local_gpu` whose live CUDA probe fails MUST NOT be Auto-runnable.
- **Public reason text is safe by construction.** The new Auto API never echoes raw provider/exception detail. Candidate reason messages come from a platform-owned projection keyed by `reason_code` (fixed safe text); unknown codes map to a generic bounded message. Selection `reason` is derived from the closed Auto reason-code set.
- **A1 preserved.** `RUNTIME_DESCRIPTOR_INVALID` stays experiment-level; `FROZEN_AUTHORITY_ITEM_CODES` is unchanged; recovery/retry never re-resolve Auto.
- **Do not modify** `feature/backend-v1-final-qualification`, `main`, `feature/v1-core`, `fix/a1-1-runtime-drift`.
- Every production task is RED → GREEN. Every test command uses the existing control-plane venv on Windows.
- **Windows full-suite differential gate (Task 8).** Because Windows has known pre-existing POSIX-only failures, A2 acceptance compares the candidate full-suite failure set against a FRESH baseline captured from exact `e372d21a9711333f906b68634f8bd20b35a9affd` in a separate read-only worktree. Gate: `candidate_new_failures = candidate_failure_set - base_failure_set` must be empty. Windows-only POSIX failures are never "fixed" by changing production code.

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
- **`DatasetExperiment`**: additive namespaced sub-object `execution_selection` inside the existing `dataset_experiments.runtime_descriptor_json` JSON column. The namespace is `execution_selection` (the spec's earlier draft name is superseded) because BOTH manual and auto requests carry execution-selection provenance.
  - The descriptor's own keys (`executor`, `device_type`, `device_index`, `precision`, `environment_ref`, `environment_label`) remain the authoritative frozen execution identity.
  - Internal shape (auto example):
    ```json
    {
      "executor": "local_cpu", "device_type": "cpu", "device_index": null,
      "precision": "float32", "environment_ref": "...", "environment_label": "...",
      "execution_selection": {
        "requested_execution_mode": "auto", "resolved_executor": "local_cpu",
        "auto_reason_code": "AUTO_LOCAL_CPU_PREFERRED", "auto_reason": "...",
        "workload_class": "SMALL"
      }
    }
    ```
  - `RuntimeDescriptor.from_metadata(...)` reads only the known descriptor keys, so the extra `execution_selection` key is inert for A1 frozen-authority comparison (`to_metadata()` equality is unaffected).
  - `parameters_json` is never touched.
  - **Public readback must not parse the raw namespace.** `DatasetExperimentRead` is extended additively with safe projected fields `requested_execution_mode`, `auto_reason_code`, `auto_reason`, `workload_class` (all optional), derived by `DatasetExperimentService._to_read()` from the internal namespace. Historical experiments with no execution-selection metadata read back as `None` (still readable). All NEW Auto UI/readback uses this safe projection.
  - The existing raw `runtime_descriptor_json` field on `DatasetExperimentRead` is PRE-EXISTING API debt (it exposes `environment_ref`); A2 does not redesign or remove it (removal would be breaking). It is recorded here for later Backend V1 API-freeze cleanup.
- **Deferred alternative (documented, not implemented):** if a later API-contract freeze requires a dedicated column, add a nullable additive JSON column via the existing `run_additive_migrations` seam. A2 does not do this.

### Public information boundary

The new Auto API returns only booleans (`technical`, `configured`, `certified`, `available`) and bounded reason fields (`reason_code`, `reason`, `reason_message`), where `reason_message` values are platform-owned sanitized projections keyed by `reason_code` (never raw provider text). It MUST NOT return the raw provider `reason_message`, `environment_ref`, interpreter/asset paths, SSH material, certificate internals/evidence, cgroup/PSI/`/proc` diagnostics, or host-private runtime paths.

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
def public_reason_message(reason_code: str | None) -> str: ...
```

`public_reason_message` (internal helper name) is the single platform-owned projection mapping a `reason_code` (closed set, plus the availability codes `EXECUTION_CAPABILITY_UNAVAILABLE`/`EXECUTION_NOT_CERTIFIED`/`INPUT_INCOMPATIBLE`/`REMOTE_EXECUTOR_UNAVAILABLE`/etc.) to a fixed, bounded, path/secret-free human string. It is invoked ONLY for candidates whose `available is False`; an available candidate's message is `None` by resolver contract (see below). For any unrecognized code it returns the generic `"Executor is currently unavailable."` (defensive fallback; it never echoes raw provider text).

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
    safe_reason_message: str | None   # platform-owned sanitized projection; None when available

@dataclass(frozen=True)
class ExecutionSelection:
    requested_mode: str            # "manual" | "auto"
    resolved_executor: str | None  # exact executor; None only when auto fails closed
    reason_code: str
    reason: str
    workload_class: str
    candidates: tuple[ExecutionCandidate, ...]

def collect_candidates(*, definition, model_release, probe_recording, executor_registry
                       ) -> tuple[ExecutionCandidate, ...]: ...
def resolve_auto_execution(*, definition, model_release, probe_recording, executor_registry,
                           dataset_item_count: int | None = None) -> ExecutionSelection: ...
```

Facts are derived by reusing existing seams only: `definition.technical_execution_capabilities`, `executor_registry.providers()`, `executor_registry.certified_capability(definition, model_release_id, executor)`, and `executor_registry.availability_for(definition, model_release, probe_recording, executor)`. No new availability engine.

`probe_recording` is always a real `RecordingModel` and `available` ALWAYS means real live availability:
- **Single-run scope:** `probe_recording` is the requested Recording.
- **Dataset scope:** `probe_recording` is the real Recording referenced by the smallest `manifest_order` manifest entry of the frozen dataset (deterministic). It permits normal input-compatibility and provider live-health evaluation. `dataset_item_count` (the frozen manifest `expected_recordings`) remains the dataset workload-size input. Auto still resolves ONCE; there is no per-item probing, no per-item Auto, and no executor change between items.

The raw provider `reason_message` is NEVER carried. Each candidate's internal `safe_reason_message` is `None` when `available is True`, else `policy.public_reason_message(reason_code)`; the selection `reason` is `policy.public_reason_message(decision.reason_code)`. The public API exposes the candidate value under the field name `reason_message` (platform sanitized). All are bounded, path/secret-free, platform-owned.

---

## Task 1: Pure Auto policy core (constant + classification + ranking + reason codes)

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/execution_selection/__init__.py` (empty package marker)
- Create: `backend/app/execution_selection/policy.py`
- Test: `backend/tests/test_execution_selection_policy.py`

**Interfaces:**
- `WorkloadClass`, `AutoDecision`, `AUTO_POLICY` constants, reason-code constants, `rank_for`, `classify_workload`, `select_executor`, `public_reason_message` (exact signatures in the Module / type reference above).

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
    public_reason_message,
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


def test_public_reason_message_is_platform_owned_and_generic_for_unknown():
    assert public_reason_message(AUTO_LOCAL_GPU_PREFERRED)  # non-empty fixed safe text
    assert public_reason_message("EXECUTION_NOT_CERTIFIED")  # non-empty fixed safe text
    assert public_reason_message("SOME_UNKNOWN_CODE") == "Executor is currently unavailable."
    # Defensive fallback only: the resolver NEVER calls this for an available
    # candidate (an available candidate's message is None by resolver contract).
    assert public_reason_message(None) == "Executor is currently unavailable."


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

Create `backend/app/execution_selection/__init__.py` (empty) and `backend/app/execution_selection/policy.py` with exactly the constants, `WorkloadClass`, `AutoDecision`, `rank_for`, `classify_workload`, `select_executor`, and `public_reason_message` specified in the Module / type reference. Import only `dataclasses`, `enum`, `typing`. `select_executor` must return `AutoDecision(None, AUTO_NO_RUNNABLE_EXECUTOR, workload_class)` for an empty runnable set, `AUTO_ONLY_RUNNABLE_EXECUTOR` for exactly one, the class ranking for `SMALL`/`GPU_BENEFICIAL`, and the `UNKNOWN` branch (`recommended_execution` if runnable else `UNKNOWN` fallback rank). `public_reason_message(reason_code)` returns a fixed platform-owned safe string for known codes and the generic `"Executor is currently unavailable."` for any unknown code; it is only invoked for unavailable candidates (the resolver returns `None` for an available candidate).

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
- `ExecutionCandidate` (with `safe_reason_message`), `ExecutionSelection`, `collect_candidates(*, definition, model_release, probe_recording, executor_registry)`, `resolve_auto_execution(*, definition, model_release, probe_recording, executor_registry, dataset_item_count=None)` (exact signatures in the Module / type reference).
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
# 8. DATASET live-availability exclusion (Correction 1):
#    probe_recording=<representative recording>, dataset_item_count=8,
#    local_gpu configured + exact certificate, local_gpu provider live availability = False,
#    local_cpu configured + certificate + live-available
#    -> candidate local_gpu available=False and excluded from runnable
#    -> workload_class == GPU_BENEFICIAL
#    -> Auto selects the next valid executor per GPU_BENEFICIAL ranking (local_cpu when local_gpu is the only GPU)
# 9. public reason projection (Correction 2):
#    a malicious FakeProvider returns availability.reason_message containing BOTH
#    "C:\\secret\\runtime\\python.exe" and "/root/private/model.pt";
#    candidate.safe_reason_message must equal policy.public_reason_message(reason_code)
#    and contain neither path.
# 10. available candidate message contract (Correction 1):
#    local_cpu configured + certified + live-available with reason_code=None
#    -> candidate.available is True
#    -> candidate.reason_code is None
#    -> candidate.safe_reason_message is None (NOT an "unavailable" message)
```

Each test constructs a `PipelineDefinition` (with `technical_execution_capabilities` and `recommended_execution`), a real `RecordingModel` (use `benchmark_fixture.add_recording` + `session`), a `FakeProvider`/`FakeRegistry` or real `ExecutorRegistry`, then calls `collect_candidates(...)` / `resolve_auto_execution(...)` and asserts the `ExecutionCandidate` booleans and the `ExecutionSelection`.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_execution_selection_resolver.py -q
```

Expected RED reason: `ModuleNotFoundError: No module named 'app.execution_selection.resolver'`.

- [ ] **Step 3: Minimal implementation**

Create `backend/app/execution_selection/resolver.py`:
- Candidate universe = sorted union of `{cap.executor for cap in definition.technical_execution_capabilities}` and `set(executor_registry.providers())`.
- Per candidate compute `technical`, `configured`, `certified = executor_registry.certified_capability(definition, model_release_id, executor) is not None`, and `availability = executor_registry.availability_for(definition, model_release, probe_recording, executor)` (REAL live availability for BOTH scopes; `configured` is never used as an `available` alias). Set `available = availability.available`; set `reason_code = None if availability.available else availability.reason_code`; set `safe_reason_message = None if availability.available else policy.public_reason_message(availability.reason_code)` — never the raw provider message, and never an "unavailable" message for an available executor.
- `resolve_auto_execution` builds `runnable = tuple(c.executor for c in candidates if technical and configured and certified and available)`, classifies workload from `probe_recording.num_samples`/`duration_s` (single-run) or `dataset_item_count` (dataset), calls `policy.select_executor(...)`, and returns `ExecutionSelection(requested_mode="auto", reason=policy.public_reason_message(decision.reason_code), ...)`.
- Do not call `availability_for` for executors that are not configured (a missing provider yields `configured=False` and is excluded without a probe).
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
9. **Exact ModelRelease freeze (Correction 4):** a release-required plugin whose default release resolves to `golden`; an Auto request (no explicit `model_release_id`) persists the run with `execution_metadata_json["model_release_id"] == "golden"` and the run executes that exact resolved release — the same release identity used for the Auto certificate decision. A release-less plugin persists `model_release_id` absent/`None`.

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
    - auto with `executor is None`:
      - load recording + definition; `resolved_release = self._resolve_release(definition, model_release_id)`;
      - call `resolve_auto_execution(definition=definition, model_release=resolved_release, probe_recording=recording, executor_registry=self.executor_registry)`;
      - on `resolved_executor is None` raise `PlatformError(selection.reason_code, selection.reason)`;
      - **freeze the exact release used for selection (Correction 4):** `frozen_model_release_id = resolved_release.release.model_release_id if resolved_release is not None else None`; use that value for `prepare_run(model_release_id=frozen_model_release_id)` so the Auto certificate decision and the persisted/executed release are the same identity (release-less plugin → `None`).
    - manual path keeps the caller-supplied `model_release_id` behaviour unchanged.
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
7. **Dataset live-availability exclusion (Correction 1):** manifest item count ≥ 8; `local_gpu` configured + exact certificate but its live provider availability returns `False`; `local_cpu` configured + certified + live-available → frozen `executor != "local_gpu"`; the frozen executor is the next valid executor per the `GPU_BENEFICIAL` ranking (`local_cpu` here, unless a `remote_gpu` is also runnable); `local_gpu` is excluded from the runnable set.
8. **Representative probe Recording (Correction 1):** the resolver is called with the real `RecordingModel` whose `manifest_order == 0` (the deterministic representative); assert the resolved experiment `executor` equals the Auto decision computed against that recording. Auto is invoked exactly once per experiment creation (patch `resolve_auto_execution` with a counting wrapper → call count == 1).
9. `runtime_descriptor_json["execution_selection"]` persisted with `requested_execution_mode`, `resolved_executor`, `auto_reason_code`, `auto_reason`, `workload_class`; `RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)` still parses and `to_metadata()` equals the frozen descriptor (the extra key is inert; A1 authority unaffected).
10. `parameters_json` equals the request `parameters` (scientific parameters untouched).
11. `retry_failed`/recovery keep the frozen executor (assert the frozen `executor` is unchanged after a retry-failed transition, and that `resolve_auto_execution` is not invoked again).
12. **Safe readback (Correction 3):** a new Auto experiment exposes `requested_execution_mode`/`auto_reason_code`/`auto_reason`/`workload_class` via `DatasetExperimentRead` (`GET /api/dataset-experiments/{id}`); the values match the internal `execution_selection` namespace.
13. **Historical compatibility (Correction 3):** an experiment seeded with `runtime_descriptor_json` lacking `execution_selection` still reads back (the four projected fields are `None`, no error).
14. Public boundary: the projected `auto_reason` never contains a raw provider path or `environment_ref` value.

Use the shared `dataset_experiment_fixtures` (`seed_dataset`, `create_experiment`) and `executor_fixtures`.

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_dataset_experiment_auto_creation.py -q
```

Expected RED reason: `DatasetExperimentCreate` requires `executor` / rejects `execution_mode`; auto path absent.

- [ ] **Step 3: Minimal implementation**

- `dataset_experiments/schema.py`: add `execution_mode` + make `executor` optional (keep `extra="forbid"`); extend `DatasetExperimentRead` additively with `requested_execution_mode: str | None`, `auto_reason_code: str | None`, `auto_reason: str | None`, `workload_class: str | None` (all default `None`).
- `dataset_experiments/router.py`: pass `execution_mode=payload.execution_mode` and `executor=payload.executor`.
- `dataset_experiments/service.py::create_experiment`:
  - build `manifest` (already) → `dataset_item_count = manifest.expected_recordings`;
  - **representative probe Recording (Correction 1):** load the real `RecordingModel` referenced by the smallest `manifest_order` entry (`manifest.entries[0].recording_id`, entries are manifest-ordered) as `probe_recording`;
  - resolve release (already) → `frozen_release_id` / `resolved_release`;
  - mode handling: manual + `executor is None` → `EXECUTION_REQUEST_INVALID`; auto + `executor is not None` → `EXECUTION_REQUEST_INVALID`; auto + `None` → `resolve_auto_execution(definition=definition, model_release=resolved_release, probe_recording=probe_recording, executor_registry=self.executor_registry, dataset_item_count=dataset_item_count)` (called exactly ONCE); on `resolved_executor is None` raise `PlatformError(selection.reason_code, selection.reason)`; else use the resolved executor;
  - `_validate_execution(definition, frozen_release_id, executor)` unchanged (validates provider + certificate);
  - persist `runtime_descriptor_json = {**descriptor.to_metadata(), "execution_selection": {...}}` (namespace `execution_selection`; provenance for both modes; auto fields present only for auto);
  - `parameters_json = dict(parameters)` unchanged.
  - No per-item Auto anywhere; `start_item_attempt`/`launch_item_attempt`/`retry_failed`/recovery are unchanged and consume the frozen `executor`.
- `dataset_experiments/service.py::_to_read()`: derive the four probe-free projected fields from `runtime_descriptor_json.get("execution_selection", {})`, returning `None` when absent (historical experiments stay readable). The raw `runtime_descriptor_json` field remains on the read model unchanged (pre-existing API debt, out of A2 scope).

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
- `ExecutorSelectionRead`, `ExecutionCandidateRead` (Pydantic response models; booleans + bounded reasons only). `ExecutionCandidateRead` exposes the public field name `reason_message`, populated from the internal sanitized projection (`safe_reason_message`); the raw provider message is never exposed. An available candidate's `reason_message` is `None`.
- `GET /api/executor-selection` query contract:
  - `pipeline_id: str` (required)
  - `model_release_id: str | None`
  - single-run scope: `recording_id: str | None`
  - dataset scope: `dataset_name: str | None`, `dataset_split: str | None`, `dataset_label_space: str | None`
  - exactly one scope required (fail closed otherwise).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_executor_selection_api.py` covering:
1. single-run scope returns `requested_mode == "auto"`, `resolved_executor` in the closed exact-executor set, `reason_code` in the closed set, `workload_class` in the closed set, and a `candidates` list where each entry has the four booleans and a `reason_message` field (public field name).
2. dataset scope (item count ≥ 8) returns `workload_class == "GPU_BENEFICIAL"` and selects per the GPU ranking (evaluated against the representative `manifest_order == 0` Recording).
3. neither scope (no `recording_id`, no dataset trio) → `PlatformError` `EXECUTION_SELECTION_REQUEST_INVALID`.
4. both scopes supplied → `EXECUTION_SELECTION_REQUEST_INVALID`.
5. zero runnable → `resolved_executor is None`, `reason_code == "AUTO_NO_RUNNABLE_EXECUTOR"`.
6. public boundary: the JSON response contains no `environment_ref`, no `C:\...`/`/root/...` path substring, no `certificate`, no `cgroup`/`psi`, and no SSH material.
7. `technical but no provider` candidate reports `technical=true, configured=false, certified=false, available=false`.
8. `provider but no certificate` candidate reports `configured=true, certified=false, available=false`.
9. **Dataset live-availability exclusion (Correction 1):** dataset scope with item count ≥ 8, `local_gpu` configured + certified but live availability `False`, `local_cpu` runnable → `resolved_executor != "local_gpu"`.
10. **Malicious reason safety (Correction 2):** a `FakeProvider` whose `availability(...).reason_message` contains BOTH `C:\secret\runtime\python.exe` and `/root/private/model.pt`; the full `GET /api/executor-selection` JSON body must contain neither path substring (the projection replaces it with the platform-owned bounded message).
11. **Available candidate message is None (Correction 1):** a candidate that is technically supported + configured + certified + live-available reports `available=true`, `reason_code=None`, and `reason_message=None` (never an "unavailable" message).
12. **Unavailable candidate message is sanitized (Correction 2):** an unavailable candidate reports a non-null `reason_code` with a non-null platform-owned `reason_message` that contains no raw provider detail.
13. `reason_message` is the public API field name for every candidate (the internal sanitized projection is never exposed under a different name).

- [ ] **Step 2: Run and confirm RED**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_executor_selection_api.py -q
```

Expected RED reason: route absent → `404` for `GET /api/executor-selection` (or `ModuleNotFoundError` for the new router/schema).

- [ ] **Step 3: Minimal implementation**

- `execution_selection/schema.py`: `ExecutionCandidateRead` (`executor`, `technical`, `configured`, `certified`, `available`, `reason_code`, `reason_message`) and `ExecutorSelectionRead` (`requested_mode`, `resolved_executor`, `reason_code`, `reason`, `workload_class`, `candidates`). The public field is `reason_message`; it is populated from the internal `safe_reason_message`.
- `execution_selection/router.py`: `GET /api/executor-selection`; validate the exactly-one-scope rule (raise `PlatformError("EXECUTION_SELECTION_REQUEST_INVALID", ...)`); resolve `definition` via `request.app.state.pipeline_registry`; resolve the release via `request.app.state.model_release_store` (`_resolve_release` semantics); single scope loads the requested `RecordingModel` (404 `RECORDING_NOT_FOUND` if missing) and passes it as `probe_recording`; dataset scope computes `dataset_item_count` via `DatasetBenchmarkService(session).prepare_manifest(dataset_name, dataset_split, dataset_label_space).expected_recordings`, loads the real `RecordingModel` referenced by the smallest `manifest_order` entry as `probe_recording`, and passes both to `resolve_auto_execution`.
- `main.py`: `include_router(execution_selection_router)`.
- Response maps only the booleans + bounded `reason_code`/`reason`/`reason_message`. Each candidate's public `reason_message` is the internal `safe_reason_message` (platform-owned projection; `None` when the candidate is available); raw provider `reason_message` is discarded. Never serialize `environment_ref` or any `RuntimeDescriptor.to_metadata()` payload.

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
15. dataset scope: `local_gpu` configured + certified + live-unavailable is excluded from the runnable set and never frozen (Correction 1).
16. no public Auto reason text (`reason`, candidate `reason_message`) echoes raw provider detail even when a provider returns a path-bearing message (Correction 2).
19. an available candidate reports `reason_code=None` and `reason_message=None`; an unavailable candidate reports a non-null sanitized `reason_message` (Correction 1).
17. DatasetExperiment safe readback: a new Auto experiment exposes the projected `requested_execution_mode`/`auto_reason_code`/`auto_reason`/`workload_class`; a historical experiment without the namespace reads back with `None` (Correction 3).
18. Auto freezes the exact resolved ModelRelease identity used for the certificate decision (Correction 4).

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

## Task 8: Windows full-suite differential regression gate

**GPU REQUIRED: NO**

**Verification task — introduces NO production code.** Windows has known pre-existing POSIX-only failures, so the full backend suite is not required to be globally green. Instead establish a FRESH baseline from exact `e372d21a9711333f906b68634f8bd20b35a9affd` and require ZERO new failures from the A2 candidate.

**Files:** none (verification only). A temporary detached Git worktree is used so the A2 working branch is never rewritten.

- [ ] **Step 1: Create a read-only exact-base worktree**

```bash
git worktree add --detach "D:\LGFiles\Wideband Signal Analysis Platform\wt-a2-base" e372d21a9711333f906b68634f8bd20b35a9affd
```

Confirm the baseline HEAD: `git -C "D:\...\wt-a2-base" rev-parse HEAD` equals `e372d21…`.

- [ ] **Step 2: Capture the baseline PROBLEM set (FAILED + ERROR)**

```bash
& "...\.venv\Scripts\python.exe" -m pytest backend/tests -q --tb=no -rfE
```

Run this with the working directory set to the baseline worktree. Record every `FAILED <node_id>` AND every `ERROR <node_id>` line into `base_problems.txt` (sorted unique). Also record the summary counts: `passed`, `skipped`, `failed`, `errors`, `warnings`, `runtime`.

- [ ] **Step 3: Capture the candidate PROBLEM set (FAILED + ERROR)**

Run the identical command on the A2 candidate branch (its worktree); record `FAILED` and `ERROR` node IDs into `candidate_problems.txt` (sorted unique) plus the same summary counts. Do not rely on `-rf` alone — the reporter flag must surface BOTH `FAILED` and `ERROR` short-summary lines (`-rfE`).

- [ ] **Step 4: Compute the differential gate**

```text
problem node = FAILED node id OR ERROR node id

candidate_new_problems = candidate_problem_set - base_problem_set
```

Require `candidate_new_problems == empty` (candidate new FAILED nodes = none AND candidate new ERROR nodes = none). The candidate MAY have fewer pre-existing Windows/POSIX problems than the baseline. Record for BOTH exact base and candidate: `passed`, `skipped`, `failed`, `errors`, `warnings`, `runtime`, and the full problem node-ID lists.

- [ ] **Step 5: Remove the temporary worktree**

```bash
git worktree remove "D:\LGFiles\Wideband Signal Analysis Platform\wt-a2-base"
```

The A2 working branch must be unchanged by this task (read-only baseline).

- [ ] **Step 6: ML-free control-plane verification**

```bash
& "...\.venv\Scripts\python.exe" -c "import importlib.util as u; print('torch:', u.find_spec('torch')); print('ultralytics:', u.find_spec('ultralytics'))"
```

Expected: `torch: None`, `ultralytics: None`.

- [ ] **Step 7: Commit**

No commit is created by this verification task (no files changed). Record the differential result in the A2 acceptance evidence.

---

## Plan self-review record

Corrective self-review (post-correction):

1. **Dataset Auto `available` is true live availability.** Both scopes compute `available` via `executor_registry.availability_for(definition, model_release, probe_recording, executor)`. `configured` is never used as an `available` alias (Module / type reference; Task 2; Task 4).
2. **No-GPU Case B excludes `local_gpu` from Auto.** A registered + certified `local_gpu` whose live CUDA probe fails is excluded from the runnable set for single-run and dataset Auto (Task 2 test 8; Task 4 test 7, 8; Task 5 test 9; Task 6 item 15).
3. **No per-item Auto resolution.** Dataset Auto resolves exactly once at creation against the deterministic representative Recording (`manifest_order == 0`); all Items use the frozen executor (Task 4 steps; Task 4 test 8 counting the resolver call).
4. **Public Auto reason text never echoes raw provider detail, and an available candidate's message is `None`.** Candidate public `reason_message` is the internal `safe_reason_message` = `None` when available, else `policy.public_reason_message(reason_code)`; selection `reason` = `policy.public_reason_message(decision.reason_code)`; unknown codes → generic bounded text (Module / type reference; Task 2 tests 9–10; Task 5 tests 11–13; Task 6 items 16, 19).
5. **Dataset Auto provenance has a safe public read projection.** `DatasetExperimentRead` gains `requested_execution_mode`/`auto_reason_code`/`auto_reason`/`workload_class` derived by `_to_read()`; consumers do not parse the raw namespace (Correction 3; Task 4 tests 12–14).
6. **`runtime_descriptor_json` extra metadata is inert for A1 authority.** `RuntimeDescriptor.from_metadata` reads only descriptor keys; `to_metadata()` equality is unaffected (Correction 3; Task 4 test 9; Task 6 item 17).
7. **Auto freezes the exact ModelRelease used for certificate selection.** Single-run Auto passes `frozen_model_release_id = resolved_release.release.model_release_id` (or `None`) to `prepare_run` (Correction 4; Task 3 test 9; Task 6 item 18). DatasetExperiment already resolves/fixes its release once.
8. **Fresh Windows full-suite differential gate.** Task 8 captures a read-only baseline at exact `e372d21…` in a detached worktree and compares the PROBLEM set (FAILED **and** ERROR node IDs); requires `candidate_problem_set - base_problem_set == empty` and records exact base/candidate counts (`passed`, `skipped`, `failed`, `errors`, `warnings`, `runtime`); Windows-only POSIX failures are never "fixed" in production code.
9. **No GPU work exists anywhere in A2.** Every task is labeled `GPU REQUIRED: NO`; all Auto branches are exercised with deterministic provider/certificate/availability doubles and SQLite.
10. **No A3 implementation leaked into A2.** No runtime doctor, runtime identity generation, qualification evidence directories, or certificate install CLI; A2 only consumes existing `ExecutorRegistry` / `ExecutionCertificateStore` / `availability_for`.
11. **No unfinished-marker text.** No unfinished markers remain; no "similar to Task X"; each task names its files, interfaces, RED test, exact command, expected RED reason, minimal implementation, GREEN command, and commit.
12. **Only the plan document changed** (this corrective commit); no production code, tests, schema, or migration touched.
13. **Public API field is exactly `reason_message`.** `ExecutionCandidateRead` / `GET /api/executor-selection` expose `reason_message` (platform-sanitized); the internal projection name is `safe_reason_message`; the raw provider `reason_message` is discarded.

Additional invariants held:

- Runtime is documented as Python 3.12.
- Manual + executor omitted: `AnalysisRun` → historical `local_cpu`; `DatasetExperiment` → fail closed (approved asymmetry preserved).
- `auto` + explicit executor → `EXECUTION_REQUEST_INVALID` (approved).
- Auto reason-code set is the seven approved codes; no plugin-id special-casing; no Linux/cgroup dependency; A2/A3 boundary unchanged.
- Persisted `executor` is never `"auto"`; no fallback after resolution; A1 Windows A/B/C unchanged; recovery/retry never re-resolve Auto.
- Provenance is metadata-only in existing JSON columns (`analysis_runs.execution_metadata_json`, `dataset_experiments.runtime_descriptor_json["execution_selection"]`); no DB migration; `parameters_json` untouched.
- The pre-existing raw `runtime_descriptor_json` exposure on `DatasetExperimentRead` is recorded as Backend V1 API-freeze debt (not reworked in A2).