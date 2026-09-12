# Phase G G2 Analysis Prepare / Launch Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each behavior task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal:** Split the existing `AnalysisService` single-Recording lifecycle into a
caller-owned `prepare_run(...)` seam that validates, resolves, freezes, and
stages a `pending` `AnalysisRun` without committing or launching, and a
`launch_prepared_run(...)` seam that physically launches an already-persisted
prepared `AnalysisRun`. Legacy `create_run(...)` keeps its exact
Single-Recording behavior by composing `prepare_run -> commit -> launch`.
G3 then composes `prepare_run` into DatasetExperiment Transaction A.

**Architecture:** A pure in-place refactor of a single production module
(`backend/app/analysis/service.py`). No new tables, no schema change, no new
provenance scheme. The existing validation / release resolution / availability
probe / provenance freeze / run construction are moved behind `prepare_run`
(no transaction ownership) and the existing provider dispatch is moved behind
`launch_prepared_run` (owns only its post-launch result transaction).
`create_run` becomes a thin compatibility wrapper. G2 does not add any
DatasetExperiment code.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
FastAPI, SQLAlchemy 2.x, Pydantic v2, pytest 9. No GPU, no SSH, no torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`
(§7 AnalysisService Prepare / Launch Seam, §8 Launch Ambiguity, §15 invariants).

**Sealed G1:** `docs/superpowers/plans/2026-09-12-phase-g-g1-persistence-frozen-experiment.md`.

**Base:** `feature/m9-2-implementation @ f6978e2521419fc9b7461e4b2336bfd98c2ab03b`.

---

## Global Constraints

Every task must preserve all of the following. A violation is a stop-and-
escalate condition.

1. **`prepare_run` owns no transaction.** It validates/resolves/freezes,
   constructs the `AnalysisRun`, calls `self.session.add(run)`, and returns.
   It MUST NOT call `session.commit()`, `session.rollback()`,
   `session.flush()`, or `provider.launch()`.
2. **`prepare_run` never makes a Run durable by itself.** A crash before the
   caller's commit must leave zero committed `AnalysisRun` rows. This is what
   makes G3 Transaction A (`prepare_run` + Attempt + Item running + commit)
   possible.
3. **Durable launch ordering is a caller precondition.** The safety ordering is
   `G3 Transaction A COMMIT -> G4 Transaction B COMMIT -> launch_prepared_run`.
   `launch_prepared_run` defensively rejects a Run still staged/unflushed in
   the current `Session`, but it does NOT claim to prove transaction durability
   from ORM object state.
4. **`launch_prepared_run` launches only a persisted, pending, not-yet-launched
   Run.** It never constructs a new `AnalysisRun`, never rebuilds remote
   provenance, and never regenerates a remote coordinator token.
5. **Legacy `create_run` behavior is byte-for-byte semantically preserved**:
   same observable status/worker_pid, same launch ordering, same error codes,
   same canonical remote provenance, same result-row set.
6. **Canonical remote request identity is unchanged.** `freeze_request_provenance`,
   `build_coordinator_metadata`, `build_batch`, `request_sha256`, and the
   `LEGACY_REQUEST_SHA256 = a96504b07998779d9053cc0ca472da3e5746c6740a765aaf7d1dde29b04ecc6c`
   contract are untouched.
7. **No `AnalysisRun` schema change.** No new/removed/renamed columns in
   `backend/app/analysis/model.py`; no migration.
8. **No DatasetExperiment coupling.** `backend/app/dataset_experiments/*` is
   not imported or modified by G2.
9. **No G3/G4 behavior:** no `DatasetExperimentAttempt` creation, no Item
   status transition, no coordinator/scheduler, no `launch_requested_at`, no
   `launch_requested_at`-based recovery, no automatic retry, no heartbeat/token
   fencing, no DatasetEvaluation, no REST, no frontend.
10. **No science changes:** no plugin runtime, ZoomSpec, CPN, STFT Energy,
    detection payload, or benchmark metric change.
11. **One production file only:** `backend/app/analysis/service.py`.

---

## Repository Mapping (verified against HEAD f6978e2)

### Current `create_run` lifecycle (`backend/app/analysis/service.py`)

- `AnalysisService.create_run` (`:108`): shared preamble (`:117`–`:149`):
  - `:117` load `RecordingModel` or `RECORDING_NOT_FOUND` (404).
  - `:120` `self.registry.get(pipeline_id)` → `definition` (raises `PIPELINE_INCOMPATIBLE` from `PipelineRegistry.get`, `pipelines/registry.py:72`).
  - `:125` `validate_plugin_parameters(definition, parameters)` (`pipelines/plugin.py:71`, raises `PLUGIN_PARAMETERS_INVALID`).
  - `:127` executor registry guard → `EXECUTION_CAPABILITY_UNAVAILABLE`.
  - `:131` `self._resolve_release(definition, model_release_id)` (`:91`).
  - `:135` `self.executor_registry.availability_for(definition, resolved_release, recording, executor)`.
  - `:138` `if not availability.available: raise PlatformError(reason_code or "EXECUTOR_UNAVAILABLE", ...)`.
  - `:143` `provider = self.executor_registry.provider(executor)` (raises `EXECUTION_CAPABILITY_UNAVAILABLE`).
  - `:145` dispatch: `executor == "remote_gpu"` → `_create_remote_run`; else `_create_local_run`.
- `_create_local_run` (`:151`): builds run id `run_<uuid4>`, `metadata={"runtime_descriptor": provider.runtime_descriptor().to_metadata()}` plus optional `model_release_id`/`asset_manifest_sha256`; constructs `AnalysisRunModel(status="pending", executor=provider.name, parameters_json=dict(parameters), execution_metadata_json=metadata)`; `session.add`; **`session.commit()`**; `session.refresh`; `provider.launch(run.id, coordinator_token=None)`; assign `worker_pid`; `session.commit()`; `session.refresh`; on exception → `status="failed"`, `error_type="ANALYSIS_FAILED"`, `error_message=str(exc)[:1000]`, commit, raise `PlatformError("ANALYSIS_FAILED", "Unable to launch local inference worker.")`.
- `_create_remote_run` (`:184`): requires `resolved_release is not None` else `MODEL_RELEASE_MISMATCH`; requires `identity_resolver` + `orchestrator_commit_resolver` else `EXECUTOR_UNAVAILABLE`; requires `runtime_commit_config`; calls `freeze_request_provenance(...)` (`remote_execution/request_builder.py:103`), injects `runtime_descriptor`, calls `build_coordinator_metadata(...)` (`remote_execution/startup.py:13`), reads `final_metadata["coordinator_token"]`; constructs run; **commit**; refresh; `provider.launch(run.id, coordinator_token=coordinator_token)`; assign `worker_pid`; commit; refresh; on exception → failed + commit + `PlatformError("ANALYSIS_FAILED", "Unable to launch remote coordinator.")`.

### Local preparation / launch code

- `LocalInferenceWorkerProvider.launch` (`analysis/local_executor.py:127`) accepts `coordinator_token` but ignores it; spawns `app.analysis.local_inference_worker <run_id>` with `WSP_LOCAL_INFERENCE_RUNTIME_REF` etc.; returns `process.pid`.
- `LocalInferenceWorkerProvider.runtime_descriptor()` (`:63`) is the frozen local descriptor.
- Worker reads `run.execution_metadata_json["runtime_descriptor"]` (`analysis/local_inference_worker.py:123`) and `run.parameters_json` (`:250`); it sets `status="running"` and commits (`:221`); terminal guard `_TERMINAL_STATUSES = {"completed","failed","interrupted"}` (`:45`, `:215`).

### Remote preparation / launch code

- `executor_registry.provider("remote_gpu")` returns `RemoteGpuExecutorProvider` (`remote_execution/runtime.py:376`), whose `launch(run_id, coordinator_token)` delegates to `self._launcher.launch(run_id, coordinator_token)` (`:412`).
- `CoordinatorJobManager.launch(run_id, coordinator_token)` (`remote_execution/coordinator_job_manager.py:26`) spawns `python -m app.remote_execution.coordinator <run_id> --coordinator-token <token>`; returns pid.
- `freeze_request_provenance` → `FROZEN_REQUEST_KEYS` (`request_builder.py:31`); `request_sha256` is computed by `build_batch` (`:89`) and excludes itself; `coordinator_token` is excluded from the wire batch and hash (`startup.py:13`).
- `build_coordinator_metadata` preserves an existing token or creates `coord_<uuid4>`; `find_or_new_coordinator_token` (`:22`).
- `resolve_remote_recording_identity(session, recording, data_root, local_run_id)` (`remote_execution/identity.py:39`) needs only the `run_id` string, not a flushed row.
- Startup recovery (`remote_execution/recovery.py:77`) rotates tokens and calls `launcher.launch(...)` directly; G2 leaves recovery untouched.

### Transaction ownership today

- `create_run`/`_create_local_run`/`_create_remote_run` own both the preparation
  commit and the launch-result commit.
- `Database.session_factory` uses `expire_on_commit=False` (`db/session.py:13`),
  so the returned Run keeps its attributes after commit.

### Relevant test owners (must remain green, unchanged)

- `backend/tests/test_analysis_runs.py` (API dispatch, unknown pipeline, stale-run startup).
- `backend/tests/test_remote_create_run.py` (remote capability/probe/provenance/token/launch/failure; local path).
- `backend/tests/test_remote_executor_availability.py`.
- `backend/tests/test_input_output_label_space.py` (input compatibility; local `worker_pid == 4242`).
- `backend/tests/test_plugin_parameters_freeze.py` (parameter freeze, non-finite rejection, no side effects before failure).
- `backend/tests/test_release_wiring.py` (release resolve + freeze into metadata).
- `backend/tests/test_request_release_provenance.py` (`LEGACY_REQUEST_SHA256`).
- `backend/tests/test_remote_execution_canonical.py`, `backend/tests/test_remote_request_freeze.py`.
- `backend/tests/test_remote_startup_recovery.py` (recovery untouched).
- `backend/tests/test_local_worker_provider.py`, `backend/tests/test_local_cpu_acceptance.py`, `backend/tests/test_cpn_local_cpu_acceptance.py`.
- `backend/tests/executor_fixtures.py` (`FakeProvider`, `FakeRegistry`).

### Test command conventions

- Single: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`

---

## Transaction Ownership

| Layer | Owns commit/rollback? | Owns launch? |
|---|---|---|
| `prepare_run(...)` | **No** | **No** |
| Legacy `create_run(...)` | Yes, the preparation commit only | Delegates to `launch_prepared_run` |
| `launch_prepared_run(...)` | Yes, the post-launch result commit only | Yes, physical launch |
| G3 Transaction A (future) | Yes — caller commits `prepare_run` + Attempt + Item running | No |
| G4 Transaction B (future) | Yes — caller commits `Attempt.launch_requested_at` before calling `launch_prepared_run` | No |

Rationale: the spec (§7) requires DatasetExperiment ownership to be durably
committed before any physical launch. `prepare_run` therefore cannot make a Run
durable; G3 must be able to bind the Run, the Attempt, and the Item in one
caller-owned transaction. `launch_prepared_run` remains the physical-launch
primitive that G4 calls strictly after the launch-intent fence commit.

Durability is therefore an explicit caller precondition, not something G2 proves
from ORM state. `launch_prepared_run` only adds a defensive pre-query rejection
for a Run that is still staged/unflushed in the current `Session`.

---

## Backward Compatibility

- **Local:** `create_run` still returns a `pending` Run with `worker_pid` set to
  the provider pid; metadata still `{"runtime_descriptor": ...}` (+ optional
  release fields); the provider is called exactly once.
- **Remote:** `create_run` still returns a `pending` Run with `worker_pid` set;
  metadata still contains the full frozen request provenance,
  `runtime_descriptor`, and `coordinator_token`; the launcher is called exactly
  once with the frozen token; `request_sha256` is unchanged
  (`LEGACY_REQUEST_SHA256` pin).
- **Failure:** a `provider.launch` exception leaves `run.status="failed"`,
  `run.error_type="ANALYSIS_FAILED"`, `run.error_message=str(exc)[:1000]`, and
  persists the Run. The raised `PlatformError` has code `ANALYSIS_FAILED` and a
  fixed message: `"Unable to launch remote coordinator."` for `remote_gpu`,
  otherwise `"Unable to launch local inference worker."`. The raw exception text
  is stored in `error_message`; it is NOT the raised `PlatformError` message.
- **Availability/validation errors:** all pre-existing codes and ordering are
  preserved (`RECORDING_NOT_FOUND`, `PIPELINE_INCOMPATIBLE`,
  `PLUGIN_PARAMETERS_INVALID`, `MODEL_RELEASE_MISMATCH`,
  `EXECUTION_CAPABILITY_UNAVAILABLE`, `EXECUTION_NOT_CERTIFIED`,
  `INPUT_INCOMPATIBLE`, `REMOTE_EXECUTOR_UNAVAILABLE`, `...`).
- **Recovery:** `remote_execution/recovery.py` is not modified and keeps using
  the launcher directly.
- **Schema:** `AnalysisRunModel` and migrations are untouched.

---

# TASK 1 — Local + Remote Analysis Prepare Seam

This task is one coherent, independently reviewable deliverable: the complete
prepare seam for both local and remote execution. Both RED test files are
written and run before any production code, so no remote production behavior
precedes its remote RED tests.

**Files:**
- Modify: `backend/app/analysis/service.py` (add `prepare_run`, `_prepare_local_run`, `_prepare_remote_run`, `_freeze_remote_provenance`).
- Create: `backend/tests/test_analysis_prepare_local.py`.
- Create: `backend/tests/test_analysis_prepare_remote.py`.

**Interfaces:**
- Consumes: `RecordingModel`, `PipelineRegistry.get`, `validate_plugin_parameters`, `_resolve_release`, `ExecutorRegistry.availability_for` / `.provider`, `provider.runtime_descriptor()`, `provider.name`, `freeze_request_provenance`, `build_coordinator_metadata`, `identity_resolver`, `orchestrator_commit_resolver`, `runtime_commit_config`.
- Produces:
  - `AnalysisService.prepare_run(*, recording_id, pipeline_id, executor, parameters, model_release_id=None) -> AnalysisRunModel`
  - `AnalysisService._prepare_local_run(recording, definition, parameters, resolved_release, provider) -> AnalysisRunModel`
  - `AnalysisService._prepare_remote_run(recording, definition, parameters, resolved_release, provider, availability) -> AnalysisRunModel`
  - `AnalysisService._freeze_remote_provenance(*, run_id, recording, definition, resolved_release, provider, availability, parameters) -> dict`

### Exact production code (add)

```python
    # ---------- preparation (caller-owned transaction) ----------

    def prepare_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
        model_release_id: str | None = None,
    ) -> AnalysisRunModel:
        """Validate/resolve/freeze and stage a pending AnalysisRun.

        Adds the Run to the CALLER'S Session. Never commits, rolls back,
        flushes, or launches: the caller owns the transaction, which is what
        lets DatasetExperiment Transaction A bind the Run, the Attempt, and the
        Item atomically (G3).
        """
        recording = self.session.get(RecordingModel, recording_id)
        if recording is None:
            raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
        pipeline = self.registry.get(pipeline_id)
        definition = pipeline.definition

        # The plugin's own parameter schema decides validity; no plugin-specific
        # parameter branch lives in the control plane.
        validate_plugin_parameters(definition, parameters)

        if self.executor_registry is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
            )
        resolved_release = self._resolve_release(definition, model_release_id)

        # Exact requested-executor availability: capability + exact certificate +
        # provider probe. The platform never substitutes a different executor.
        availability = self.executor_registry.availability_for(
            definition, resolved_release, recording, executor
        )
        if not availability.available:
            raise PlatformError(
                availability.reason_code or "EXECUTOR_UNAVAILABLE",
                availability.reason_message or "The requested executor is unavailable.",
            )
        provider = self.executor_registry.provider(executor)

        if executor == "remote_gpu":
            return self._prepare_remote_run(
                recording, definition, parameters, resolved_release, provider, availability
            )
        return self._prepare_local_run(
            recording, definition, parameters, resolved_release, provider
        )

    def _prepare_local_run(self, recording, definition, parameters, resolved_release, provider) -> AnalysisRunModel:
        run_id = f"run_{uuid4().hex}"
        metadata = {"runtime_descriptor": provider.runtime_descriptor().to_metadata()}
        if resolved_release is not None:
            metadata["model_release_id"] = resolved_release.release.model_release_id
            metadata["asset_manifest_sha256"] = resolved_release.manifest.asset_manifest_sha256

        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor=provider.name,
            status="pending",
            parameters_json=dict(parameters),
            execution_metadata_json=metadata,
        )
        self.session.add(run)
        return run

    def _prepare_remote_run(self, recording, definition, parameters, resolved_release, provider, availability) -> AnalysisRunModel:
        run_id = f"run_{uuid4().hex}"
        final_metadata = self._freeze_remote_provenance(
            run_id=run_id,
            recording=recording,
            definition=definition,
            resolved_release=resolved_release,
            provider=provider,
            availability=availability,
            parameters=parameters,
        )
        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            executor=provider.name,
            status="pending",
            parameters_json=dict(parameters),
            execution_metadata_json=final_metadata,
        )
        self.session.add(run)
        return run

    def _freeze_remote_provenance(self, *, run_id, recording, definition, resolved_release, provider, availability, parameters) -> dict:
        from app.remote_execution.request_builder import freeze_request_provenance
        from app.remote_execution.startup import build_coordinator_metadata

        if resolved_release is None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH", "Remote execution requires a ModelRelease."
            )
        if self.identity_resolver is None or self.orchestrator_commit_resolver is None:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote provenance configuration is incomplete.")
        if not self.runtime_commit_config:
            raise PlatformError("EXECUTOR_UNAVAILABLE", "Remote runtime commit is not configured.")

        frozen_model_release_id = resolved_release.release.model_release_id
        asset_manifest_sha256 = resolved_release.manifest.asset_manifest_sha256

        identity = self.identity_resolver(self.session, recording, self.data_root, run_id)
        orchestrator_commit = self.orchestrator_commit_resolver(self.project_root)

        frozen_metadata = freeze_request_provenance(
            local_run_id=run_id,
            recording_fingerprint=identity.recording_fingerprint,
            source_data_sha256=identity.source_data_sha256,
            dataset_name=identity.dataset_name,
            dataset_split=identity.dataset_split,
            dataset_key=identity.dataset_key,
            label_space=identity.label_space,
            pipeline_id=definition.id,
            pipeline_version=definition.version,
            required_remote_runtime_commit=self.runtime_commit_config,
            orchestrator_commit=orchestrator_commit,
            asset_manifest_sha256=asset_manifest_sha256,
            remote_profile=availability.remote_profile or "remote",
            model_release_id=frozen_model_release_id,
            parameters=parameters,
        )
        # runtime_descriptor is internal execution metadata OUTSIDE the canonical
        # request payload; request_sha256 and recovery reconstruction are unchanged.
        frozen_metadata["runtime_descriptor"] = provider.runtime_descriptor().to_metadata()
        return build_coordinator_metadata(frozen_metadata)
```

`_create_local_run` / `_create_remote_run` / `create_run` remain unchanged in
this task (transient duplication is removed in Task 3). None of the new methods
contains `commit`, `rollback`, `flush`, or `launch`.

### Exact tests — `backend/tests/test_analysis_prepare_local.py`

```python
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.model_release import ResolvedModelRelease

from executor_fixtures import FakeProvider, FakeRegistry

MANIFEST_SHA = "b" * 64


class PrepareLocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="prepare_local", name="Prepare Local", version="1.0",
            label_space="spacenet_14", recommended_device="CPU", cpu_supported=True,
            stages=(), inspectable_stages=(), task_capability="classification",
            executors_supported=("local_cpu",),
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
            parameter_schema={"type": "object", "additionalProperties": False,
                              "properties": {"threshold": {"type": "number"}}},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class ReleaseBoundLocalPipeline(PrepareLocalPipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return replace(super().definition, model_release_required=True)


class FakeReleaseStore:
    def __init__(self) -> None:
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            model_release_id=requested or "golden", asset_manifest_sha256=MANIFEST_SHA
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST_SHA)
        return ResolvedModelRelease(release=release, manifest=manifest)


def _add_recording(client, recording_id="rec_x"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.commit()


def _service_local(client, *, pipeline=None, store=None, provider=None):
    session = client.app.state.database.session_factory()
    fake_provider = provider or FakeProvider("local_cpu")
    registry = FakeRegistry({"local_cpu": fake_provider})
    service = AnalysisService(
        session,
        PipelineRegistry([pipeline or PrepareLocalPipeline()]),
        client.app.state.job_manager,
        model_release_store=store,
        executor_registry=registry,
    )
    return service, fake_provider, registry


def _prepare_local(service, **overrides):
    kwargs = dict(recording_id="rec_x", pipeline_id="prepare_local",
                  executor="local_cpu", parameters={})
    kwargs.update(overrides)
    return service.prepare_run(**kwargs)


def test_prepare_run_stages_pending_local_run_without_commit_or_launch(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    assert run.status == "pending"
    assert run.worker_pid is None
    assert run.executor == "local_cpu"
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_freezes_parameters_and_runtime_descriptor(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service, parameters={"threshold": 0.5})
    assert run.parameters_json == {"threshold": 0.5}
    assert run.execution_metadata_json == {
        "runtime_descriptor": provider.runtime_descriptor().to_metadata()
    }


def test_prepare_run_release_less_fields_remain_null(client):
    _add_recording(client)
    service, _, _ = _service_local(client)
    run = _prepare_local(service)
    assert "model_release_id" not in run.execution_metadata_json
    assert "asset_manifest_sha256" not in run.execution_metadata_json


def test_prepare_run_release_bound_freezes_id_and_manifest_sha(client):
    _add_recording(client)
    store = FakeReleaseStore()
    service, _, _ = _service_local(client, pipeline=ReleaseBoundLocalPipeline(), store=store)
    run = _prepare_local(service, model_release_id="golden")
    assert run.execution_metadata_json["model_release_id"] == "golden"
    assert run.execution_metadata_json["asset_manifest_sha256"] == MANIFEST_SHA
    assert store.resolve_calls == [("prepare_local", "1.0", "golden")]


def test_prepare_run_invokes_actual_availability(client):
    _add_recording(client)
    service, _, registry = _service_local(client)
    _prepare_local(service)
    assert registry.availability_calls == [("prepare_local", "local_cpu")]


def test_prepare_run_rejects_invalid_parameters_without_side_effects(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    with pytest.raises(PlatformError) as exc:
        _prepare_local(service, parameters={"unknown": 1})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_caller_rollback_after_prepare_leaves_no_durable_run(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run_id = run.id
    service.session.rollback()
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run_id) is None


def test_caller_can_add_companion_row_in_same_transaction(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.add(DetectionResultModel(
        id="det_companion", run_id=run.id, t_start_s=0.0, t_end_s=0.1,
        f_low_hz=0.0, f_high_hz=1.0, class_id=0, class_name="x", confidence=0.5,
    ))
    service.session.commit()
    assert provider.launches == []  # commit did not launch
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is not None
        assert fresh.get(DetectionResultModel, "det_companion") is not None
```

### Exact tests — `backend/tests/test_analysis_prepare_remote.py`

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.request_builder import build_batch

from executor_fixtures import FakeProvider, FakeRegistry

RUN = "a" * 40
MANIFEST = "b" * 64


class RemoteCapablePipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="remote_test", name="Remote Test", version="1.0", label_space="spacenet_14",
            recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
            task_capability="detection_classification", executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu", model_release_required=True,
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeProbe:
    def __init__(self, *, available=True, reason_code=None):
        self.available = available
        self.reason_code = reason_code
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        self.calls.append((recording.id, pipeline.id))
        if self.available:
            return ExecutorAvailabilityRead(executor="remote_gpu", available=True,
                                            reason_code=None, reason_message=None,
                                            remote_profile="autodl_primary", recommended=True)
        return ExecutorAvailabilityRead(executor="remote_gpu", available=False,
                                        reason_code=self.reason_code or "REMOTE_EXECUTOR_UNAVAILABLE",
                                        reason_message="unavailable", remote_profile="autodl_primary",
                                        recommended=False)


class FakeModelReleaseStore:
    def __init__(self, release_id="golden"):
        self.release_id = release_id
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version,
            model_release_id=requested or self.release_id,
            asset_manifest_path=Path("/tmp/asset_manifest.json"),
            asset_manifest_sha256=MANIFEST,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=MANIFEST)
        return ResolvedModelRelease(release=release, manifest=manifest)


class FakeLauncher:
    def __init__(self):
        self.launches = []

    def launch(self, run_id, coordinator_token):
        self.launches.append((run_id, coordinator_token))
        return 12345


def _identity_resolver(session, recording, data_root, local_run_id):
    return SimpleNamespace(
        recording_fingerprint="2" * 64, source_data_sha256="1" * 64,
        dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
        label_space="spacenet_14", local_run_id=local_run_id,
    )


def _orch_commit_resolver(project_root):
    return RUN


def _add_recording(client, recording_id="rec_x", label_space="spacenet_14"):
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=recording_id, name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space=label_space, source_data_sha256="1" * 64,
        ))
        session.commit()


def _service_remote(client, *, probe=None, launcher=None, pipeline=None):
    session = client.app.state.database.session_factory()
    probe = probe or FakeProbe()
    launcher = launcher or FakeLauncher()
    service = AnalysisService(
        session,
        PipelineRegistry([pipeline or RemoteCapablePipeline()]),
        client.app.state.job_manager,
        remote_executor_probe=probe,
        remote_coordinator_launcher=launcher,
        identity_resolver=_identity_resolver,
        orchestrator_commit_resolver=_orch_commit_resolver,
        model_release_store=FakeModelReleaseStore(),
        runtime_commit_config=RUN,
        project_root=Path("/tmp"),
        data_root=Path("/tmp/data"),
        executor_registry=FakeRegistry(
            {"remote_gpu": FakeProvider("remote_gpu", probe=probe, launcher=launcher)}
        ),
    )
    return service, probe, launcher


def _prepare_remote(service, **overrides):
    kwargs = dict(recording_id="rec_x", pipeline_id="remote_test",
                  executor="remote_gpu", parameters={})
    kwargs.update(overrides)
    return service.prepare_run(**kwargs)


def test_prepare_run_remote_stages_without_launch(client):
    _add_recording(client)
    service, probe, launcher = _service_remote(client)
    run = _prepare_remote(service)
    assert run.status == "pending"
    assert run.worker_pid is None
    assert run.executor == "remote_gpu"
    assert probe.calls == [("rec_x", "remote_test")]
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_remote_freezes_full_provenance_and_token_before_commit(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client)
    run = _prepare_remote(service)
    metadata = run.execution_metadata_json
    assert metadata["local_run_id"] == run.id
    assert metadata["request_id"] and metadata["batch_id"] and metadata["item_key"]
    assert metadata["request_sha256"] == compute_request_sha256(build_batch(metadata))
    assert metadata["recording_fingerprint"] == "2" * 64
    assert metadata["source_data_sha256"] == "1" * 64
    assert metadata["model_release_id"] == "golden"
    assert metadata["asset_manifest_sha256"] == MANIFEST
    assert metadata["coordinator_token"]
    assert metadata["runtime_descriptor"]["executor"] == "remote_gpu"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id) is None


def test_prepare_run_remote_provenance_matches_legacy_request_identity(client):
    # request_sha256 is reconstructed purely from persisted metadata: launch must
    # never need to rebuild a scientifically different request.
    _add_recording(client)
    service, _, _ = _service_remote(client)
    run = _prepare_remote(service)
    metadata = dict(run.execution_metadata_json)
    assert build_batch(metadata).request_sha256 == metadata["request_sha256"]


def test_prepare_run_remote_rejects_missing_release(client):
    from dataclasses import replace
    _add_recording(client)
    class ReleaseLess(RemoteCapablePipeline):
        @property
        def definition(self):
            return replace(RemoteCapablePipeline().definition, model_release_required=False)
    service, _, launcher = _service_remote(client, pipeline=ReleaseLess())
    with pytest.raises(PlatformError) as exc:
        _prepare_remote(service)
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    assert launcher.launches == []


def test_prepare_run_remote_consults_probe_before_staging(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client, probe=FakeProbe(
        available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"))
    with pytest.raises(PlatformError) as exc:
        _prepare_remote(service)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_freeze_remote_provenance_helper_is_the_single_seam(client):
    _add_recording(client)
    service, _, _ = _service_remote(client)
    recording = service.session.get(RecordingModel, "rec_x")
    definition = RemoteCapablePipeline().definition
    resolved = FakeModelReleaseStore().resolve("remote_test", "1.0", None)
    provider = service.executor_registry.provider("remote_gpu")
    availability = FakeProbe().availability(recording, definition, "1" * 64)

    metadata = service._freeze_remote_provenance(
        run_id="run_seam",
        recording=recording,
        definition=definition,
        resolved_release=resolved,
        provider=provider,
        availability=availability,
        parameters={"threshold": 0.5},
    )
    assert metadata["local_run_id"] == "run_seam"
    assert metadata["parameters"] == {"threshold": 0.5}
    assert metadata["request_sha256"] == compute_request_sha256(build_batch(metadata))
    assert metadata["coordinator_token"]
    assert metadata["runtime_descriptor"]["executor"] == "remote_gpu"
```

### Steps

- [ ] **Write failing tests:** create BOTH `backend/tests/test_analysis_prepare_local.py` and `backend/tests/test_analysis_prepare_remote.py` exactly as above. Neither imports the other.
- [ ] **Run exact RED command (both files together, before any production code):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py -v
```
Expected RED: `AttributeError: 'AnalysisService' object has no attribute 'prepare_run'` (local + remote tests), plus `AttributeError: ... '_freeze_remote_provenance'` for the helper test.
- [ ] **Implement minimal code:** add `prepare_run`, `_prepare_local_run`, `_prepare_remote_run`, `_freeze_remote_provenance` exactly as above; do not touch `create_run` or the old `_create_*` methods.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_request_release_provenance.py \
  backend/tests/test_remote_execution_canonical.py \
  backend/tests/test_release_wiring.py \
  backend/tests/test_input_output_label_space.py \
  backend/tests/test_plugin_parameters_freeze.py -q
```
- [ ] **Commit checkpoint:** `feat: add local and remote analysis run prepare seam`

---

# TASK 2 — `launch_prepared_run` + failure/duplicate guards

**Files:**
- Modify: `backend/app/analysis/service.py` (add `launch_prepared_run`).
- Create: `backend/tests/test_analysis_launch_prepared_run.py`.

**Interfaces:**
- Consumes: `AnalysisRunModel`, `Session.get`, `Session.new`, `ExecutorRegistry.provider`, `provider.launch(run_id, coordinator_token=...)`, `run.execution_metadata_json["coordinator_token"]`.
- Produces: `AnalysisService.launch_prepared_run(run_id: str) -> AnalysisRunModel`.

### Exact production code (add)

```python
    # ---------- physical launch of an already-persisted prepared run ----------

    def launch_prepared_run(self, run_id: str) -> AnalysisRunModel:
        """Physically launch an already-persisted prepared AnalysisRun.

        Durable commit ordering is a CALLER precondition:
            G3 Transaction A COMMIT
            G4 Transaction B COMMIT
            launch_prepared_run(...)

        This method does not prove transaction durability from ORM object state.
        It only defensively rejects a Run that is still staged/unflushed in this
        Session (checked BEFORE any query, so no autoflush can occur), then loads
        the persisted Run and launches it. It never constructs a new Run, never
        rebuilds remote provenance, and never regenerates the coordinator token.
        """
        # Defensive pre-query guard: a Run still staged in this Session has no
        # durable row and must not be launched. Durable commit ordering itself is
        # guaranteed by the caller contract, not by Session.new.
        for pending in self.session.new:
            if isinstance(pending, AnalysisRunModel) and pending.id == run_id:
                raise PlatformError(
                    "ANALYSIS_RUN_NOT_LAUNCHABLE",
                    "Prepared analysis run is still staged in this session; the caller must commit before launch.",
                    409,
                )

        run = self.get(run_id)  # raises ANALYSIS_RUN_NOT_FOUND (404)
        if run.worker_pid is not None or run.status != "pending":
            raise PlatformError(
                "ANALYSIS_RUN_NOT_LAUNCHABLE",
                "Only a pending, not-yet-launched prepared analysis run can be launched.",
                409,
            )
        if self.executor_registry is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
            )
        provider = self.executor_registry.provider(run.executor)

        coordinator_token = None
        if run.executor == "remote_gpu":
            coordinator_token = (run.execution_metadata_json or {}).get("coordinator_token")
            if not coordinator_token:
                raise PlatformError(
                    "ANALYSIS_RUN_NOT_LAUNCHABLE",
                    "Prepared remote run is missing its coordinator token.",
                    409,
                )
        try:
            run.worker_pid = provider.launch(run.id, coordinator_token=coordinator_token)
            self.session.commit()
            self.session.refresh(run)
        except Exception as exc:
            run.status = "failed"
            run.error_type = "ANALYSIS_FAILED"
            run.error_message = str(exc)[:1000]
            self.session.commit()
            if run.executor == "remote_gpu":
                raise PlatformError(
                    "ANALYSIS_FAILED", "Unable to launch remote coordinator."
                ) from exc
            raise PlatformError(
                "ANALYSIS_FAILED", "Unable to launch local inference worker."
            ) from exc
        return run
```

Guard semantics (deliberate):

| Condition | Result |
|---|---|
| A matching `AnalysisRunModel` is still staged/unflushed in `session.new` (checked before any query) | `ANALYSIS_RUN_NOT_LAUNCHABLE` (409) — defensive only; durable commit is the caller's precondition |
| Run id absent from the database | `ANALYSIS_RUN_NOT_FOUND` (404), existing `get` behavior |
| `status != "pending"` (running/completed/failed/interrupted) | `ANALYSIS_RUN_NOT_LAUNCHABLE` (409) |
| `worker_pid is not None` | `ANALYSIS_RUN_NOT_LAUNCHABLE` (409) — prevents duplicate launch |
| `executor_registry is None` | `EXECUTION_CAPABILITY_UNAVAILABLE` |
| No registered provider for `run.executor` | `EXECUTION_CAPABILITY_UNAVAILABLE` |
| `remote_gpu` without frozen `coordinator_token` | `ANALYSIS_RUN_NOT_LAUNCHABLE` (409) |
| `provider.launch` raises | `run.status="failed"`, `run.error_type="ANALYSIS_FAILED"`, `run.error_message=str(exc)[:1000]`, committed; raised `PlatformError("ANALYSIS_FAILED", "Unable to launch remote coordinator." / "Unable to launch local inference worker.")` |

`ANALYSIS_RUN_NOT_LAUNCHABLE` is a new, additive error code; it centralizes
precondition failure and avoids conflating a rejected launch with a failed
launch. No automatic retry is added.

Note: a Run that a caller manually `flush()`es (leaving `session.new`) inside an
uncommitted transaction is NOT distinguishable from a committed Run via ORM
state. G2 does not attempt to detect that case; durable commit ordering remains
the explicit caller precondition (Transaction A / Transaction B).

### Exact tests (`backend/tests/test_analysis_launch_prepared_run.py`)

**Scaffolding:** copy verbatim the module-level imports/classes/fixtures and the
`_service_local` / `_prepare_local` helpers from
`backend/tests/test_analysis_prepare_local.py`, and the `_service_remote` /
`_prepare_remote` helpers from `backend/tests/test_analysis_prepare_remote.py`.
Both test modules remain self-contained; never import one test module from
another. `_service_local` returns `(service, provider, registry)`;
`_service_remote` returns `(service, probe, launcher)`.

```python
def test_launch_prepared_run_local_launches_existing_run_once(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.commit()
    launched = service.launch_prepared_run(run.id)
    assert provider.launches == [(run.id, None)]
    assert launched.worker_pid == 4242
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 1


def test_launch_prepared_run_remote_uses_frozen_coordinator_token(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client)
    run = _prepare_remote(service)
    frozen_token = run.execution_metadata_json["coordinator_token"]
    service.session.commit()
    service.launch_prepared_run(run.id)
    assert launcher.launches == [(run.id, frozen_token)]
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(AnalysisRunModel, run.id)
        assert stored.execution_metadata_json["coordinator_token"] == frozen_token
        assert stored.worker_pid == 12345


def test_launch_prepared_run_multiple_calls_do_not_relaunch(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    service.session.commit()
    service.launch_prepared_run(run.id)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == [(run.id, None)]


def test_launch_prepared_run_missing_run_fails_closed(client):
    service, provider, _ = _service_local(client)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run("missing")
    assert exc.value.code == "ANALYSIS_RUN_NOT_FOUND"
    assert provider.launches == []


def test_launch_prepared_run_rejects_staged_unflushed_run(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    assert run in service.session.new  # staged/unflushed before the call
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []
    assert run in service.session.new  # the guard did not flush the Session
    assert run.status == "pending"
    assert run.worker_pid is None
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_launch_prepared_run_rejects_non_pending_status(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run.status = "completed"
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []


def test_launch_prepared_run_rejects_existing_worker_pid(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = _prepare_local(service)
    run.worker_pid = 1
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert provider.launches == []


def test_launch_prepared_run_missing_provider_fails_closed(client):
    _add_recording(client)
    session = client.app.state.database.session_factory()
    session.add(AnalysisRunModel(
        id="run_noprov", recording_id="rec_x", pipeline_id="prepare_local",
        pipeline_version="1.0", executor="local_cpu", status="pending",
        parameters_json={}, execution_metadata_json={"runtime_descriptor": {}},
    ))
    session.commit()
    empty = AnalysisService(
        client.app.state.database.session_factory(),
        PipelineRegistry([]),
        client.app.state.job_manager,
        executor_registry=FakeRegistry({}),
    )
    with pytest.raises(PlatformError) as exc:
        empty.launch_prepared_run("run_noprov")
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_launch_prepared_run_remote_missing_token_fails_closed(client):
    session = client.app.state.database.session_factory()
    session.add(AnalysisRunModel(
        id="run_notoken", recording_id="rec_x", pipeline_id="remote_test",
        pipeline_version="1.0", executor="remote_gpu", status="pending",
        parameters_json={}, execution_metadata_json={"runtime_descriptor": {}},
    ))
    session.commit()
    service, _, launcher = _service_remote(client)
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run("run_notoken")
    assert exc.value.code == "ANALYSIS_RUN_NOT_LAUNCHABLE"
    assert launcher.launches == []


def test_launch_prepared_run_local_failure_marks_failed_and_persists(client):
    _add_recording(client)
    class RaisingProvider(FakeProvider):
        def launch(self, run_id, *, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_local(client, provider=RaisingProvider("local_cpu"))
    run = _prepare_local(service)
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_FAILED"
    assert exc.value.message == "Unable to launch local inference worker."
    with client.app.state.database.session_factory() as fresh:
        stored = fresh.get(AnalysisRunModel, run.id)
        assert stored.status == "failed"
        assert stored.error_type == "ANALYSIS_FAILED"
        assert stored.error_message == "boom"


def test_launch_prepared_run_remote_failure_uses_remote_message(client):
    _add_recording(client)
    class RaisingLauncher:
        def launch(self, run_id, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_remote(client, launcher=RaisingLauncher())
    run = _prepare_remote(service)
    service.session.commit()
    with pytest.raises(PlatformError) as exc:
        service.launch_prepared_run(run.id)
    assert exc.value.code == "ANALYSIS_FAILED"
    assert exc.value.message == "Unable to launch remote coordinator."
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(AnalysisRunModel, run.id).status == "failed"
```

### Steps

- [ ] **Write failing test** `backend/tests/test_analysis_launch_prepared_run.py` with the scaffolding from Task 1 re-declared locally plus the cases above.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_analysis_launch_prepared_run.py -v
```
Expected RED: `AttributeError: 'AnalysisService' object has no attribute 'launch_prepared_run'`.
- [ ] **Implement minimal code:** add `launch_prepared_run` exactly as above.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py -q
```
- [ ] **Commit checkpoint:** `feat: add prepared analysis run launch seam`

---

# TASK 3 — Route legacy `create_run` through prepare / commit / launch

**Files:**
- Modify: `backend/app/analysis/service.py` (rewrite `create_run`; delete `_create_local_run` and `_create_remote_run`).
- Create: `backend/tests/test_analysis_create_run_compat.py`.

**Interfaces:**
- Produces: `AnalysisService.create_run(*, recording_id, pipeline_id, executor, parameters, model_release_id=None) -> AnalysisRunModel` implemented as `prepare_run -> service-owned commit -> launch_prepared_run`.
- Removes: `_create_local_run`, `_create_remote_run`.

### Exact production code (replace `create_run`; delete both old `_create_*`)

```python
    def create_run(
        self,
        *,
        recording_id: str,
        pipeline_id: str,
        executor: str,
        parameters: dict,
        model_release_id: str | None = None,
    ) -> AnalysisRunModel:
        """Legacy Single-Recording path.

        Composes the caller-owned prepare seam with a service-owned preparation
        commit and the physical launch primitive, preserving current public
        behavior exactly.
        """
        run = self.prepare_run(
            recording_id=recording_id,
            pipeline_id=pipeline_id,
            executor=executor,
            parameters=parameters,
            model_release_id=model_release_id,
        )
        self.session.commit()
        self.session.refresh(run)
        return self.launch_prepared_run(run.id)
```

### Exact tests (`backend/tests/test_analysis_create_run_compat.py`)

**Scaffolding:** copy verbatim the module-level imports/classes/fixtures and the
`_service_local` / `_prepare_local` helpers from
`backend/tests/test_analysis_prepare_local.py`, and the `_service_remote` /
`_prepare_remote` helpers from `backend/tests/test_analysis_prepare_remote.py`.
Both test modules remain self-contained; never import one test module from
another.

**Real refactor RED — delegation/composition contract** (fails on the current
legacy `create_run`, passes only after the refactor):

```python
def test_create_run_composes_prepare_commit_launch(client, monkeypatch):
    _add_recording(client)
    service, provider, _ = _service_local(client)

    events = []
    real_prepare = AnalysisService.prepare_run
    real_launch = AnalysisService.launch_prepared_run
    real_commit = service.session.commit

    def spy_prepare(self, **kwargs):
        run = real_prepare(self, **kwargs)
        events.append(("prepare", run.id))
        return run

    def spy_commit(*args, **kwargs):
        events.append(("commit", None))
        return real_commit(*args, **kwargs)

    def spy_launch(self, run_id):
        events.append(("launch", run_id))
        return real_launch(self, run_id)

    monkeypatch.setattr(AnalysisService, "prepare_run", spy_prepare)
    monkeypatch.setattr(AnalysisService, "launch_prepared_run", spy_launch)
    monkeypatch.setattr(service.session, "commit", spy_commit)

    run = service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                             executor="local_cpu", parameters={})

    assert [event[0] for event in events] == ["prepare", "commit", "launch"]
    assert events[0][1] == run.id
    assert events[2][1] == run.id
    assert provider.launches == [(run.id, None)]
```

On the current legacy code the `prepare_run`/`launch_prepared_run` spies are
never invoked; only the internal `_create_local_run` commits fire the commit
spy. The ordered event list therefore does not equal
`["prepare", "commit", "launch"]`, so this test fails before the refactor and
passes after it.

**Compatibility regressions** (must pass before and after; the commit-failure
case is a regression, not the RED):

```python
def test_create_run_local_unchanged_observable_behavior(client):
    _add_recording(client)
    service, provider, _ = _service_local(client)
    run = service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                             executor="local_cpu", parameters={"threshold": 0.5})
    assert run.status == "pending"
    assert run.worker_pid == 4242
    assert run.executor == "local_cpu"
    assert run.parameters_json == {"threshold": 0.5}
    assert provider.launches == [(run.id, None)]
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 1


def test_create_run_remote_unchanged_observable_behavior(client):
    _add_recording(client)
    service, probe, launcher = _service_remote(client)
    run = service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                             executor="remote_gpu", parameters={})
    assert run.worker_pid == 12345
    assert launcher.launches == [(run.id, run.execution_metadata_json["coordinator_token"])]
    assert probe.calls == [("rec_x", "remote_test")]


def test_create_run_available_false_raises_before_staging(client):
    _add_recording(client)
    service, _, launcher = _service_remote(client, probe=FakeProbe(
        available=False, reason_code="REMOTE_EXECUTOR_UNAVAILABLE"))
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                           executor="remote_gpu", parameters={})
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"
    assert launcher.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_create_run_prepare_commit_failure_skips_launch(client, monkeypatch):
    # Compatibility regression, not the refactor RED: current legacy behavior
    # also commits before launching, so a failed preparation commit must never
    # reach provider.launch.
    _add_recording(client)
    service, provider, _ = _service_local(client)
    launched = []
    real_launch = AnalysisService.launch_prepared_run

    def spy_launch(self, run_id):
        launched.append(run_id)
        return real_launch(self, run_id)

    monkeypatch.setattr(AnalysisService, "launch_prepared_run", spy_launch)

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(service.session, "commit", boom)
    with pytest.raises(RuntimeError):
        service.create_run(recording_id="rec_x", pipeline_id="prepare_local",
                           executor="local_cpu", parameters={})
    assert launched == []
    assert provider.launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.query(AnalysisRunModel).count() == 0


def test_create_run_remote_failed_launch_marks_failed(client):
    _add_recording(client)
    class RaisingLauncher:
        def launch(self, run_id, coordinator_token):
            raise RuntimeError("boom")
    service, _, _ = _service_remote(client, launcher=RaisingLauncher())
    with pytest.raises(PlatformError) as exc:
        service.create_run(recording_id="rec_x", pipeline_id="remote_test",
                           executor="remote_gpu", parameters={})
    assert exc.value.code == "ANALYSIS_FAILED"
```

### Steps

- [ ] **Write failing test** `backend/tests/test_analysis_create_run_compat.py` (scaffolding locally re-declared) including `test_create_run_composes_prepare_commit_launch`.
- [ ] **Run exact RED command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_analysis_create_run_compat.py -v
```
Expected RED: `test_create_run_composes_prepare_commit_launch` fails because the current legacy `create_run` does not call `prepare_run`/`launch_prepared_run` (spy events list is empty). The observable-behavior and commit-failure compatibility tests pass.
- [ ] **Implement minimal code:** replace `create_run` as above and delete `_create_local_run` / `_create_remote_run`.
- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.
- [ ] **Focused regressions:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_remote_executor_availability.py \
  backend/tests/test_input_output_label_space.py \
  backend/tests/test_plugin_parameters_freeze.py \
  backend/tests/test_release_wiring.py \
  backend/tests/test_remote_startup_recovery.py -q
```
- [ ] **Commit checkpoint:** `refactor: route analysis create_run through prepare and launch`

---

# TASK 4 — Regression matrix + full verification (verification only)

**Files:**
- Create: `backend/tests/test_analysis_seam_regression.py`.
- No production change.

**Interfaces:** consumes everything from Tasks 1–3.

### Exact regression guards (self-contained; no cross-test import)

```python
from app.analysis.model import AnalysisRunModel
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.request_builder import build_batch

_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}

# Complete minimal legacy fixture defined locally (do not import another test
# module). Mirrors the frozen Task-B3 fixture with deterministic ids.
_LEGACY_FIXTURE_METADATA = {
    "request_id": "id_fixture",
    "batch_id": "id_fixture",
    "item_key": "id_fixture",
    "local_run_id": "run_fixture",
    "orchestrator_commit": "d" * 40,
    "required_remote_runtime_commit": "5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    "asset_manifest_sha256": "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08",
    "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3",
    "pipeline_version": "1.0.0",
    "remote_profile": "remote",
    "recording_fingerprint": "a" * 64,
    "source_data_sha256": "b" * 64,
    "dataset_name": "SpaceNet",
    "dataset_split": "test",
    "dataset_key": "0",
    "label_space": "spacenet_14",
    "parameters": {},
}
_LEGACY_REQUEST_SHA256 = "a96504b07998779d9053cc0ca472da3e5746c6740a765aaf7d1dde29b04ecc6c"


def test_analysis_run_schema_unchanged_by_g2():
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert not any("experiment" in name for name in AnalysisRunModel.__table__.columns.keys())
    assert "launch_requested_at" not in AnalysisRunModel.__table__.columns.keys()


def test_legacy_request_hash_byte_exact_after_g2():
    batch = build_batch(_LEGACY_FIXTURE_METADATA)
    assert batch.request_sha256 == _LEGACY_REQUEST_SHA256
    assert compute_request_sha256(batch) == _LEGACY_REQUEST_SHA256
```

The existing `backend/tests/test_request_release_provenance.py` is also run
mandatorily as the frozen-contract regression (Step below); the local fixture in
this file is not an import of that module.

### Steps

- [ ] **Add regression guards** `backend/tests/test_analysis_seam_regression.py`.
- [ ] **Run guards (expected PASS):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_analysis_seam_regression.py -v
```
- [ ] **Run the complete G2 suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_prepare_local.py \
  backend/tests/test_analysis_prepare_remote.py \
  backend/tests/test_analysis_launch_prepared_run.py \
  backend/tests/test_analysis_create_run_compat.py \
  backend/tests/test_analysis_seam_regression.py -v
```
- [ ] **Run frozen-contract regression:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_create_run.py \
  backend/tests/test_remote_executor_availability.py \
  backend/tests/test_input_output_label_space.py \
  backend/tests/test_plugin_parameters_freeze.py \
  backend/tests/test_release_wiring.py \
  backend/tests/test_request_release_provenance.py \
  backend/tests/test_remote_execution_canonical.py \
  backend/tests/test_remote_request_freeze.py \
  backend/tests/test_remote_startup_recovery.py \
  backend/tests/test_local_worker_provider.py \
  backend/tests/test_executor_registry.py \
  backend/tests/test_execution_certificate.py \
  backend/tests/test_model_release.py -q
```
- [ ] **Run full backend suite:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```
Require 0 failed / 0 errors (the current sealed baseline is `1400 passed, 28 skipped`; G2 must not reduce passed or introduce failures). If the execution tool times out, rerun via a background session until pytest prints a final summary; do not infer PASS from a partial run.
- [ ] **Commit checkpoint:** `test: add phase G G2 regression matrix`

---

## G2 Boundary

**G2 implements:**
- `AnalysisService.prepare_run(...)` (caller-owned staged `pending` Run; no commit/rollback/flush/launch).
- `AnalysisService._prepare_local_run(...)` / `_prepare_remote_run(...)` / `_freeze_remote_provenance(...)`.
- `AnalysisService.launch_prepared_run(run_id)` (guarded physical launch of a persisted prepared Run; owns only its result transaction; durability is a caller precondition).
- `create_run(...)` refactored to `prepare_run -> commit -> launch_prepared_run`, with `_create_local_run`/`_create_remote_run` deleted.
- Focused TDD tests for prepare/launch/transaction ownership and compat regressions.

**Explicitly deferred (must NOT appear in G2):**
- `DatasetExperimentAttempt` creation, Item status transitions (G3).
- DatasetExperiment coordinator, job manager wiring, bounded concurrency, best-effort loop, reconciliation (G3).
- Retry Failed, `Attempt.launch_requested_at` / Transaction B, launch-ambiguity recovery, coordinator token fencing, restart DatasetExperiment recovery (G4).
- DatasetEvaluation creation, evaluation lifecycle, REST endpoints, read routers (G5).
- Real 3-Recording acceptance (G6).
- Changes to `backend/app/dataset_experiments/*`, canonical hashing, AssetManifest V1, certificate semantics, ModelRelease authority, `analysis_runs` schema, or any scientific code.

---

## Plan Questions — Resolved From Repository Evidence

1. **prepare vs launch split.** `prepare_run` takes lines `service.py:117`–`:149` plus the *construction* halves of `_create_local_run`/`_create_remote_run` (through `session.add`). `launch_prepared_run` takes the *launch halves* (`provider.launch(...)`, worker_pid assignment, result commit, failure handling). Legacy `create_run` keeps the preparation commit between them.
2. **`session.flush()` in prepare?** No. `run_id` is generated explicitly and passed to `identity_resolver`, and `AnalysisRunModel.id` is assigned before `session.add`; G3's dependent `DatasetExperimentAttempt` FK ordering is handled by SQLAlchemy's unit-of-work topological insert order. `prepare_run` calls no flush.
3. **Remote token retrieval at launch.** `run.execution_metadata_json["coordinator_token"]`, frozen by `build_coordinator_metadata` inside `_freeze_remote_provenance`. `launch_prepared_run` reads it; it never calls `find_or_new_coordinator_token` or `build_coordinator_metadata`.
4. **Canonical parity guarantee.** The `freeze_request_provenance` call arguments are moved verbatim; nothing at launch recomputes them. `request_sha256` is derived from persisted metadata via `build_batch`, pinned by `LEGACY_REQUEST_SHA256` (`test_request_release_provenance.py:20`), the Task 4 local fixture, and `test_prepare_run_remote_provenance_matches_legacy_request_identity`.
5. **Relaunch preconditions.** A matching staged/unflushed `AnalysisRunModel` found by scanning `session.new` (checked before any query), `status != "pending"`, or `worker_pid is not None` → `ANALYSIS_RUN_NOT_LAUNCHABLE` (409). Durable commit ordering is a caller precondition.
6. **`provider.launch` raises.** `run.status="failed"`, `run.error_type="ANALYSIS_FAILED"`, `run.error_message=str(exc)[:1000]`, committed; raised `PlatformError` code `ANALYSIS_FAILED` with fixed message: `"Unable to launch remote coordinator."` (remote) or `"Unable to launch local inference worker."` (local). The raw exception text is not the raised message.
7. **Commit after prepare fails in legacy `create_run`.** Exception propagates; `launch_prepared_run` is never called; no durable Run (covered by the compatibility regression `test_create_run_prepare_commit_failure_skips_launch`). No rollback is added, matching current behavior.
8. **Launch-result transaction owner.** `launch_prepared_run` owns only its post-launch commit (worker_pid or failed result). Legacy `create_run` owns the preparation commit. G3 Transaction A and G4 Transaction B are caller-owned before launch. No conflict.
9. **Pinning tests.** `test_remote_create_run.py`, `test_analysis_runs.py`, `test_input_output_label_space.py`, `test_plugin_parameters_freeze.py`, `test_release_wiring.py`, `test_request_release_provenance.py`, `test_remote_execution_canonical.py`, `test_remote_request_freeze.py`, `test_remote_startup_recovery.py`, `test_local_worker_provider.py`, `test_executor_registry.py`, `test_execution_certificate.py`, `test_model_release.py`, `test_local_cpu_acceptance.py`, `test_cpn_local_cpu_acceptance.py`.
10. **AnalysisRun schema change?** No. Required answer is YES (no change), confirmed by `test_analysis_run_schema_unchanged_by_g2` and the absence of any model/migration edit.

---

## Plan Self-Review

1. `prepare_run` never commits/rolls back/flushes/launches — enforced by Task 1 code + tests.
2. `launch_prepared_run` does not claim `Session.new` proves commit durability — durability is an explicit caller precondition; the guard is defensive and pre-query.
3. The pending/unflushed guard runs before any `self.get`/query that could autoflush.
4. Durable ordering remains caller-owned: `Transaction A COMMIT -> Transaction B COMMIT -> physical launch`.
5. No remote prepare production behavior precedes its remote RED tests — Task 1 writes both RED test files first, then implements.
6. The prepare seam is one coherent, independently reviewable deliverable (Task 1).
7. The `create_run` refactor has a real failing delegation test (`test_create_run_composes_prepare_commit_launch`).
8. The commit-failure test is classified as a compatibility regression, not a fake RED.
9. No test module imports another test module — Task 4 defines its fixture locally and runs `test_request_release_provenance.py` directly.
10. Failure-message semantics match current `AnalysisService` exactly (fixed raised message; raw text in `error_message`).
11. `AnalysisRun` schema untouched — pinned by Task 4 guard.
12. Canonical remote request behavior unchanged — pinned by `LEGACY_REQUEST_SHA256` + parity tests.
13. Launch does not rebuild remote provenance.
14. No automatic retry added.
15. No `launch_requested_at` behavior added (G4 owns it).
16. No G3/G4/G5 behavior enters G2 (boundary section).
17. Legacy `create_run` remains compatible (Task 3 tests + existing suites).
18. Every task has exact files, interfaces, tests, commands, and checkpoints.
19. No TODO/TBD/XXX/hand-wavy placeholders.

---

## Out-of-Scope Guardrails During Implementation

- If `prepare_run` needs `commit`/`rollback`/`flush` to work, STOP.
- If `launch_prepared_run` needs to rebuild request provenance or regenerate a
  coordinator token, STOP.
- If a change is needed to `analysis_runs` schema, migrations, DatasetExperiment
  package, recovery, or any plugin/science code, STOP.
- If a new retry or launch-intent fence is proposed, STOP — that is G4.
