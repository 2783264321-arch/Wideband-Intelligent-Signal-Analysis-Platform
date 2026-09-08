# M9.1 Task 12F-A — Control-Plane Remote Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the **local / control-plane** half of remote execution task 12F (subtask 12F-A) so the platform can expose the frozen `zoomspec_yolo26n_aug_combined_frn_v3` pipeline as a `remote_gpu`-only pipeline, verify remote availability through the existing `RemoteProfile`/`SshRunner`, and launch/freeze/supervise a per-run **local coordinator subprocess** that drives a single remote `AnalysisRun` to completion — without ever invoking the local scientific worker.

**Architecture:** The control plane stays entirely lightweight: registering ZoomSpec must not import torch/ultralytics or construct CPN/FRN models. `create_run(executor=remote_gpu)` freezes immutable request/provenance into `execution_metadata_json`, constructs a canonical `RemoteExecutionBatchV1` (one item per run, parameters `{}`), then dispatches a local coordinator subprocess (never `app.analysis.worker`). The coordinator runs a deterministic submit→poll→download→ingest→terminal loop, is restart-safe and idempotent, and reuses the existing `result_ingestor`/`AnalysisResultWriter` as the **only** persistence path. Startup recovery distinguishes `local_cpu` (interrupt) from `remote_gpu` (re-attach coordinator) and never blindly interrupts a remote worker. No `RemoteJob`/`GpuRun`/ORM entity is created.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic, OpenSSH/SCP subprocess transport (no `shell=True`), NumPy, pytest. No GPU and no real SSH required for 12F-A tests.

**Spec:** `docs/superpowers/specs/2026-09-09-m9-1-task12f-remote-platform-integration-design.md`

**Base:** `feature/m9-1-live-remote-gpu-inference @ c98f4f36deb2a4e13462070c5f7c13800288591a`

---

## Scope And Boundaries

**In scope (12F-A only):**
- Lightweight shared ZoomSpec definition + remote-only registry stub (no model load / no torch import).
- Local `RemoteExecutorProbe` adapter + `SshRunner` probe invocation + deterministic success/failure mapping.
- Explicit validated deployment source for `required_remote_runtime_commit` (no hardcoded SHA).
- Remote request/provenance freeze + canonical `RemoteExecutionBatchV1` construction.
- `create_run(executor=remote_gpu)` → local coordinator subprocess (never `app.analysis.worker`).
- Coordinator submit/create-or-attach → poll → download → ingest → terminal-state mapping.
- Restart recovery: `local_cpu running → interrupted`; `remote_gpu pending/running → respawn coordinator`.
- `result_ingestor`/`AnalysisResultWriter` remains the only persistence path.
- All tests run without GPU and without real SSH (fake subprocess/transport/job-manager objects).

**Out of scope (12F-B and later):**
- Remote `runner._cli_probe` production body and remote readiness semantics.
- Remote GPU production `ItemExecutor` / `RemoteWorkerContext` / `ZoomSpecRemoteItemExecutor`.
- Real SSH probe success (post-12F-B integration gate).
- Multi-GPU scheduling, batch UI, remote HTTP inference server, Redis/Celery, `RemoteJob` ORM.
- Any change to Task 12A–E scientific semantics.

**Design note — probe split:** 12F-A owns the *local* probe adapter and its deterministic mapping. It does **not** require the remote probe to succeed; 12F-A tests inject fake transport responses. 12F-B owns the remote probe body.

---

## Reference — Timeline Of The Lifecycle

```text
POST /api/analysis-runs {executor: "remote_gpu"}
  -> AnalysisService.create_run(remote_gpu)
      -> RequestBuilder.freeze(...)                      # pure, no I/O, no SSH
      -> AnalysisRun(remote_gpu, status=pending, execution_metadata_json=<frozen>)
      -> CoordinatorJobManager.launch(run_id)            # subprocess, never worker
  -> coordinator subprocess (new process, own DB session + its own lifecycle)
      -> RequestBuilder.build_batch(metadata)            # reconstruct identical batch
      -> verify request_sha256 reproduces exactly
      -> submit-or-attach (create-or-attach via batch_id)
      -> poll status
         -> running : sleep, re-poll (idempotent; re-query same batch first)
         -> completed : download envelope+zip, ingest_remote_result, mark completed
         -> failed    : mark failed (immutable)
         -> interrupted/missing-unrecoverable : mark interrupted (audited)
  -> restart recovery (startup):
      -> mark_stale_running_runs_interrupted: local_cpu running ONLY -> interrupted
      -> remote_gpu pending/running -> start/reattach one coordinator per run
```

---

# TASK 1 — Lightweight Shared Definition And Remote-Only Registry Stub

**First: read before editing**

- `backend/app/pipelines/base.py` (`PipelineDefinition`, `Pipeline`, `PipelineOutput`).
- `backend/app/pipelines/registry.py` (`PipelineRegistry`, `create_pipeline_registry`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (`ZoomSpecFrozenPipeline.definition` — currently builds a literal `PipelineDefinition` in `property definition`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/__init__.py`.
- `backend/app/pipelines/dummy.py`, `backend/app/pipelines/stft_energy/pipeline.py` (existing lightweight Pipelines).

**Interfaces (files to create/modify):**

- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py` (new). Owns the single shared frozen definition + a remote-only stub:
  ```python
  from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput

  ZOOMSPEC_FROZEN_DEFINITION = PipelineDefinition(
      id="zoomspec_yolo26n_aug_combined_frn_v3",
      name="ZoomSpec YOLO26n + Combined FRN V3",
      version="1.0.0",
      label_space="spacenet_14",
      recommended_device="GPU",
      cpu_supported=False,
      stages=("ls_stft", "cpn", "ahlp", "frn", "postprocess"),
      inspectable_stages=(),
      task_capability="detection_classification",
      executors_supported=("remote_gpu",),
      recommended_executor="remote_gpu",
  )

  class ZoomSpecRemoteOnlyPipeline(Pipeline):
      @property
      def definition(self) -> PipelineDefinition:
          return ZOOMSPEC_FROZEN_DEFINITION

      def run(self, recording: RecordingInput, parameters: dict, workspace: Path) -> PipelineOutput:
          raise PlatformError("EXECUTOR_UNAVAILABLE", "ZoomSpec frozen pipeline is remote_gpu only and cannot run locally.")
  ```
  The stub must raise `PlatformError("EXECUTOR_UNAVAILABLE", ...)` in `run()` — it must never execute locally.

- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (modify): make `ZoomSpecFrozenPipeline.definition` return `ZOOMSPEC_FROZEN_DEFINITION` (the same shared object) instead of building a literal. No change to `run()`/`_compose_from_proposals()`.

- `backend/app/pipelines/registry.py` (modify): import the stub and register it in `create_pipeline_registry()`:
  ```python
  from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZoomSpecRemoteOnlyPipeline
  ...
  return PipelineRegistry([DummyPipeline(), STFTEnergyDetectorPipeline(), ZoomSpecRemoteOnlyPipeline()])
  ```

**RED test (`backend/tests/test_zoomspec_definition_registry.py`, new):**

```python
import subprocess, sys
def test_import_registry_does_not_import_torch_or_ultralytics():
    # load registry + definition in a subprocess and assert neither torch nor ultralytics is in sys.modules
```

Tests:
- `test_zoomspec_definition_matches_frozen_contract`: id, version, label_space, cpu_supported=False, executors_supported==("remote_gpu",), recommended_executor=="remote_gpu", task_capability=="detection_classification".
- `test_registry_exposes_zoomspec_without_model_load`: `create_pipeline_registry().get("zoomspec_yolo26n_aug_combined_frn_v3")` returns the stub; `.definition.id` equals the frozen id.
- `test_zoomspec_remote_only_stub_cannot_run_locally`: `pytest.raises(PlatformError)` with code `EXECUTOR_UNAVAILABLE` when calling `stub.run(...)` on a dummy `RecordingInput`.
- `test_importing_registry_does_not_import_torch_or_ultralytics`: fresh subprocess `python -c "import app.pipelines.registry; import sys; assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules"` exits 0.
- `test_zoomspec_frozen_pipeline_definition_is_shared_object`: `ZoomSpecFrozenPipeline.definition is ZOOMSPEC_FROZEN_DEFINITION` and `ZoomSpecRemoteOnlyPipeline().definition is ZOOMSPEC_FROZEN_DEFINITION`.

**Expected failure (RED):** `create_pipeline_registry()` currently returns only Dummy + STFT; `registry.get("zoomspec_yolo26n_aug_combined_frn_v3")` raises `PIPELINE_INCOMPATIBLE`; `definition.py` does not exist.

**Minimal implementation:** create `definition.py`, register the stub, and make `ZoomSpecFrozenPipeline.definition` reuse the shared object.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_definition_registry.py -v
```

**Commit checkpoint:** `feat: add zoomspec remote-only registry definition`

---

# TASK 2 — Probe Error Channel: Bounded Transport/Probe Result Interface

**First: read before editing**

- `backend/app/remote_execution/transport.py` (`SshRunner`, `RemoteTransportError` — currently `_invoke` raises generic `RemoteTransportError` on any nonzero return, collapsing stderr).
- `backend/app/remote_execution/executor.py` (`RemoteExecutorProbe` Protocol).
- `backend/app/analysis/schema.py` (`ExecutorAvailabilityRead`).
- `backend/app/core/errors.py` (`PlatformError`).
- How the runner surface reports errors: inspect `backend/app/remote_execution/runner.py` locate the `probe`/`status` command handling and how a controlled runner `PlatformError` is surfaced on stdout/stderr.

**Problem:** `SshRunner._invoke` raises `RemoteTransportError("Remote transport command exited nonzero.")` on any nonzero return, losing the runner's structured output. The local `RemoteExecutorProbe` adapter must distinguish a **controlled runner PlatformError code** (e.g. `REMOTE_EXECUTOR_UNAVAILABLE`, `REMOTE_IMPLEMENTATION_MISMATCH`, `PIPELINE_ASSET_MISMATCH`, `REMOTE_PROBE_UNAVAILABLE`) from a **generic SSH/transport failure** — without leaking arbitrary stderr/tracebacks to API users.

**Interfaces (files):**

- `backend/app/remote_execution/transport.py` (modify):
  ```python
  class RemoteTransportError(RuntimeError):
      """Remote transport command failed (SSH/SCP-level). No server stderr leaked."""
      pass

  class RemoteRunnerExit(RemoteTransportError):
      """Remote runner returned a controlled nonzero exit. Carries only a safe
      optional reason token (a validated error code) and a flagged hint.
      stdout is retained for structured probe parsing; stderr is NOT exposed."""
      def __init__(self, returncode: int, code: str | None, message: str | None, stdout: str):
          super().__init__(f"Remote runner exited with code {returncode}.")
          self.returncode = returncode
          self.code = code            # validated identifier or None
          self.message = message      # sanitized, truncated, no traceback
          self.stdout = stdout
  ```
  Define a bounded probe result interface:
  ```python
  @dataclass(frozen=True)
  class ProbeResult:
      available: bool
      reason_code: str | None
      reason_message: str | None
      remote_profile: str | None
  ```
  - `_invoke` must be the minimal change: on nonzero, raise `RemoteRunnerExit(returncode, _extract_runner_code(result.stdout), _sanitize_message(result.stdout, result.stderr), result.stdout)` for the runner path, preserving the generic `RemoteTransportError` for pure transport (e.g. ssh/ssh-key not found). Keep `shell=False` fixed-argv. Add helpers `_extract_runner_code(stdout)` and `_sanitize_message(stdout, stderr)` that parse only the runner's controlled trailing `error_code`/`error_message` markers and **discard** arbitrary tracebacks; never include untrusted stderr in a user-facing message.
  - Net contract: a runner **nonzero** exit yields `RemoteRunnerExit` (structured, deterministic), while a runner **zero** exit with malformed/unparseable probe output yields `RemoteTransportError` (or a `REMOTE_PROBE_UNAVAILABLE` mapping in the adapter). Generic SSH failure yields `RemoteTransportError`.

- `backend/app/remote_execution/executor.py` (modify): keep the `RemoteExecutorProbe` Protocol and add the concrete adapter:
  ```python
  class SshRemoteExecutorProbe:
      """Local control-plane probe adapter. Reads profile + transports probe
      invocation and maps a deterministic probe result. Mockable via injected
      transport/profile; no network is touched in unit tests."""

      def __init__(self, profile: RemoteProfile, transport: SshRunner): ...

      def availability(self, recording, pipeline, source_data_sha256) -> ExecutorAvailabilityRead:
          # derive profile; invoke transport.run_runner("probe", (...))
          # map success -> available=True; RemoteRunnerExit -> explicit reason;
          # RemoteTransportError -> REMOTE_TRANSPORT_UNAVAILABLE; missing config -> unavailable.
  ```
  Never crash on unavailable remote; result is always a well-formed `ExecutorAvailabilityRead`.

- `backend/app/remote_execution/profile.py` (modify, bounded): the profile is the validated config source already; add a frozen `RemoteRuntimeConfig`/field for `required_remote_runtime_commit` (see Task 3) and keep `RemoteProfile.from_env` fail-closed.

**RED test (`backend/tests/test_remote_probe_error_channel.py`, new):** use a fake `run_process` returning a controlled runner exit and a fake normal zero exit.

Tests:
- `test_probe_success_maps_available_true`: fake zero exit with `{"status": "available"}` probe JSON → `available=True`, `reason_code is None`.
- `test_probe_structured_failure_maps_explicit_unavailable`: fake nonzero with runner's error markers code `REMOTE_IMPLEMENTATION_MISMATCH` → `available=False`, `reason_code=="REMOTE_IMPLEMENTATION_MISMATCH"`.
- `test_probe_generic_transport_failure_maps_unavailable`: fake nonzero with no runner markers (pure ssh failure) → `available=False`, `reason_code=="REMOTE_TRANSPORT_UNAVAILABLE"` (or a distinct code), **never** a crash.
- `test_probe_does_not_leak_stderr_traceback`: assert user-facing `reason_message` never contains `Traceback` / a raw `.py` path / arbitrary stderr.
- `test_probe_out_of_scope_for_remote_success`: adapter does not require the remote probe body; with a deterministic fake response it maps correctly regardless of 12F-B state.

**Expected failure (RED):** `RemoteRunnerExit` / `SshRemoteExecutorProbe` do not exist; `_invoke` collapses everything into `RemoteTransportError`.

**Minimal implementation:** add `RemoteRunnerExit` + bounded `ProbeResult`, branch `_invoke`, add `SshRemoteExecutorProbe` adapter + `_sanitize_message`/`_extract_runner_code`.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_probe_error_channel.py -v
```

**Commit checkpoint:** `feat: add bounded remote probe error channel`

---

# TASK 3 — Explicit Deployment Source For `required_remote_runtime_commit`

**First: read before editing**

- `backend/app/remote_execution/profile.py` (`RemoteProfile` from env; currently has no runtime-commit field).
- `backend/app/remote_execution/schema.py` (`GitCommitSha` `^[0-9a-f]{40}$`; `RemoteExecutionRequestV1.required_remote_runtime_commit`, `RemoteExecutionBatchV1.required_remote_runtime_commit`, `RemoteExecutionEnvelopeV1.remote_runtime_commit`).
- `backend/app/remote_execution/source_hash.py` (existing SHA helpers).
- How the orchestrator commit is currently obtained (search `orchestrator_commit`).

**Decision (LOCKED):** `required_remote_runtime_commit` MUST be a **validated 40-char deployment-configured commit** sourced from the `RemoteProfile` lifecycle, separate from the locally-observed `orchestrator_commit`. It MUST NOT be a hardcoded old SHA (`9a6f0fe…` / `c98f4f3…`) in production code and MUST NOT be inferred from the local working tree.

**Interfaces (files):**

- `backend/app/remote_execution/profile.py` (modify):
  ```python
  @dataclass(frozen=True)
  class RemoteRuntimeConfig:
      """Deployment-pinned remote runtime identity, validated and separate from
      the locally observed orchestrator commit."""
      required_remote_runtime_commit: str  # validated ^[0-9a-f]{40}$

  # RemoteProfile gains: required_remote_runtime_commit: str  (parsed from env)
  ```
  - Add `required_remote_runtime_commit` to `RemoteProfile`; parse from `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT` (a new env key), validate via the existing 40-hex rule; raise `_unavailable` on invalid/missing.
  - Keep `from_env` fail-closed and never tolerate an empty/invalid commit.

- Add a small validator (reuse the existing `_SHA256_RE`-style pattern): `_require_runtime_commit(value)` raising `PlatformError("REMOTE_EXECUTOR_UNAVAILABLE", ...)` on non-40-hex.

**RED test (`backend/tests/test_remote_runtime_commit_config.py`, new):**

Tests:
- `test_valid_runtime_commit_accepted`: 40-hex env → `RemoteProfile.required_remote_runtime_commit == value`.
- `test_missing_runtime_commit_is_unavailable`: env absent → `PlatformError` code `REMOTE_EXECUTOR_UNAVAILABLE`.
- `test_invalid_runtime_commit_length_rejected`: 39-hex / 41-hex / with non-hex char → rejected.
- `test_runtime_commit_distinct_from_orchestrator_commit`: freeze path keeps `orchestrator_commit` (from local `git rev-parse HEAD` at coordinator/control-plane) separate from `required_remote_runtime_commit` (deployment config) and asserts they are **not** coerced together.
- `test_no_hardcoded_sha_in_request_builder`: scan the new `request_builder.py` source for the literal strings `9a6f0fe` / `c98f4f3` / any 40-hex literal → none present (the value comes from `RemoteProfile`).

**Expected failure (RED):** `RemoteProfile` has no runtime-commit field; `from_env` does not read it.

**Minimal implementation:** add the field + validator + parse; wire `RemoteRuntimeConfig`/`required_remote_runtime_commit` into profile construction.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_runtime_commit_config.py -v
```

**Commit checkpoint:** `feat: source required runtime commit from validated profile`

---

# TASK 4 — Request/Provenance Freeze And Canonical Batch Construction

**First: read before editing**

- `backend/app/remote_execution/schema.py` (`RemoteExecutionBatchV1`, `RemoteExecutionRequestV1`, `RemoteExecutionItemV1`, `RemotePipelineRefV1`, `RemoteRecordingRefV1`, `GitCommitSha`, `WireIdentifier`, `Sha256Hex`, `LabelSpace`).
- `backend/app/remote_execution/canonical.py` (`compute_request_sha256`, `canonical_request_payload`, `canonical_request_bytes`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json` (assets + `asset_manifest_sha256`).
- `backend/app/recordings/model.py` (`RecordingModel`: id, name, dataset_name, dataset_split, label_space, source_data_sha256, external_path, data_path, sample_rate_hz, center_frequency_hz, frequency_low_hz, frequency_high_hz, num_samples, duration_s).
- `backend/app/remote_execution/resolver.py` (SpaceNet identity + fingerprint/source-hash primitives) and `backend/app/imported_runs/fingerprint.py` (`build_recording_fingerprint`, `ManifestRecording`).

**Decision (LOCKED):** Define `remote_execution/request_builder.py` as the **pure** request/provenance construction module.

- One `RemoteExecutionItemV1` per `AnalysisRun` (V1 = one-recording-per-run).
- `parameters == {}` for the frozen ZoomSpec pipeline.
- Reuse existing `build_recording_fingerprint` / `compute_file_sha256` / `resolve_source_data_sha256` for recording identity; **no second identity scheme**. The SpaceNet logical identity is `dataset_key = recording.name`, `dataset_name`, `dataset_split`, `label_space`.
- `execution_metadata_json` keys (exact, ordered for canonical reproduction):
  ```
  request_id
  batch_id
  item_key
  request_sha256
  orchestrator_commit
  required_remote_runtime_commit
  asset_manifest_sha256
  pipeline_id
  pipeline_version
  remote_profile
  recording_fingerprint
  source_data_sha256
  dataset_name
  dataset_split
  dataset_key
  label_space
  parameters             # = {} (frozen)
  payload_sha256         # absent until ingest; set only by result_ingestor
  ```
  `parameters_json` remains algorithm parameters only (`{}`); SSH/batch/host provenance is never mixed into `parameters_json`.

**Interfaces (files):**

- `backend/app/remote_execution/request_builder.py` (new):
  ```python
  from app.remote_execution.schema import RemoteExecutionBatchV1

  def freeze_request_provenance(*, recording, pipeline_definition, profile,
                                orchestrator_commit, asset_manifest_sha256) -> dict:
      """Pure provenance construction (no I/O, no SSH, no DB writes).
      Returns the exact execution_metadata_json dict."""

  def build_batch(metadata: dict) -> RemoteExecutionBatchV1:
      """Reconstruct an identical RemoteExecutionBatchV1 from persisted
      execution_metadata_json alone (restart-safe)."""

  def verify_request_sha256(batch: RemoteExecutionBatchV1, metadata: dict) -> bool:
      """compute_request_sha256(batch) == metadata['request_sha256']."""
  ```
  - `freeze_request_provenance` generates `request_id`/`batch_id`/`item_key` (format-validated system UUIDs, never client input), builds the batch via `build_batch`, computes `request_sha256 = compute_request_sha256(batch)`, and returns the metadata dict (with `request_sha256` inside).
  - `build_batch` reads only `metadata` (restart-safe) and rebuilds `RemoteExecutionBatchV1` with `items=[RemoteExecutionItemV1(...)]` and `request_sha256` recomputed. A restarted coordinator can reproduce the identical batch and request SHA.

**RED test (`backend/tests/test_remote_request_freeze.py`, new):**

Tests:
- `test_freeze_produces_all_required_provenance_keys`: the returned metadata contains every key in the locked list.
- `test_freeze_parameters_is_empty_dict`: `metadata["parameters"] == {}`.
- `test_batch_has_one_item_per_run`: `len(batch.items) == 1`; `batch.items[0].request_id == metadata["request_id"]`.
- `test_request_sha_reconstructs_exactly_from_persisted_metadata`: `build_batch(metadata)` then `compute_request_sha256(...) == metadata["request_sha256"]` and `verify_request_sha256(...) is True`.
- `test_freeze_reuses_existing_fingerprint_and_source_hash_primitives`: recording identity comes from `build_recording_fingerprint`/`resolve_source_data_sha256` (assert the freeze path doesn't invent a new identity; use the resolver/fingerprint module).
- `test_batch_parameters_empty_and_no_batch_host_provenance_in_parameters`: `batch.items[0].parameters == {}` and SSH/batch/host fields never appear in `parameters`.
- `test_ids_are_system_generated_and_format_validated`: `request_id`/`batch_id`/`item_key` match `WireIdentifier`; they are not free text.
- `test_same_inputs_reproduce_same_request_sha`: two freezes with identical semantic inputs → identical `request_sha256`.

**Expected failure (RED):** `request_builder.py` / `freeze_request_provenance` / `build_batch` do not exist.

**Minimal implementation:** add request_builder with the three functions; wire fingerprint/source-hash reuse.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_request_freeze.py -v
```

**Commit checkpoint:** `feat: freeze remote request provenance and canonical batch`

---

# TASK 5 — Coordinator And Coordinator Job Manager (Subprocess)

**First: read before editing**

- `backend/app/remote_execution/request_builder.py` (Task 4).
- `backend/app/remote_execution/job_manager.py` (`RemoteGpuJobManager.submit`/`status`/`download`).
- `backend/app/remote_execution/result_ingestor.py` (`ingest_remote_result`).
- `backend/app/remote_execution/schema.py` (`RemoteBatchStatusV1`, `RemoteItemStatusV1`, statuses `queued|running|completed|failed|interrupted`).
- `backend/app/analysis/model.py` (`AnalysisRunModel`).
- `backend/app/analysis/service.py` (`mark_stale_running_runs_interrupted`, `AnalysisService`).
- `backend/app/remote_execution/validation.py` (`AnalysisResultWriter`).
- `backend/app/core/config.py` (`Settings`), `backend/app/main.py` (`create_app`).

**Decision (LOCKED):** Three modules with strict separation:

- `remote_execution/request_builder.py` — pure request/provenance (Task 4, no I/O/SSH).
- `remote_execution/coordinator.py` — one-run reconciliation loop (owns submit/attach, poll, download, ingest, terminal mapping; restart-safe, idempotent).
- `remote_execution/coordinator_job_manager.py` — subprocess launcher (starts a coordinator process per run).

No SSH polling logic inside `AnalysisService`.

**Interfaces (files):**

- `backend/app/remote_execution/coordinator_job_manager.py` (new):
  ```python
  class CoordinatorJobManager:
      """Launches one local coordinator subprocess per remote AnalysisRun.
      Uses a fixed argv to `app.remote_execution.coordinator <run_id>`; never
      invokes app.analysis.worker."""
      def __init__(self, settings: Settings): ...
      def launch(self, run_id: str, *, coordinator_module: str = "app.remote_execution.coordinator") -> int:
          # subprocess.Popen([sys.executable, "-m", coordinator_module, run_id],
          #                  cwd=backend_root, env=<WSP_*>, DEVNULL, shell=False, close_fds=True)
          # Mockable: inject a fake Popen factory in tests.
  ```
  Keep a deterministic fixture-friendly interface so 12F-A tests can substitute a fake launcher (no real subprocess).

- `backend/app/remote_execution/coordinator.py` (new):
  ```python
  class Coordinator:
      """Single deterministic loop for ONE remote AnalysisRun. Process entry:
      python -m app.remote_execution.coordinator <run_id>."""

      def __init__(self, *, job_manager, ingestor, metadata, run_id, logger): ...
      def run(self) -> str:   # terminal_status
          ...
  ```
  `run()`: submit/create-or-attach (reconcile same `batch_id` first) → poll → completed: download envelope+zip → `ingest_remote_result` → mark `completed`; `failed` → mark `failed`; `interrupted`/missing-unrecoverable → mark `interrupted`. Transient submit/status uncertainty re-queries the same batch first. Idempotent: a completed run with same `payload_sha256` is a no-op; a run already `completed`/`failed`/`interrupted` is immutable.

  - `main()` coordinator entrypoint parses `run_id`, reconstructs metadata from DB, builds batch via `build_batch`, verifies `request_sha256`, runs the loop. Restart calls re-enter `run()` safely.

  - Terminal-state mapping is atomic in the DB via the coordinator's own session; `ingest_remote_result` remains the only persistence path (no direct output writes).

**RED test (`backend/tests/test_remote_coordinator.py`, new):** use fake job-manager transport responses (a fake `RemoteGpuJobManager`/`SshRunner`) and fake ingestor (call-through to `ingest_remote_result` with a small validated package) — no SSH, no GPU.

Tests:
- `test_coordinator_created_and_running_flow`: fake status `running` → coordinator stays supervising.
- `test_coordinator_completed_ingests_once`: fake status `completed` → download called once → ingest called once → run `completed`.
- `test_coordinator_repeated_completion_is_idempotent`: re-run → no second ingest; `payload_sha256` no-op path.
- `test_coordinator_failed_marks_failed`: fake `failed` → run `failed`, immutable.
- `test_coordinator_interrupted_marks_interrupted`: missing/unrecoverable batch → run `interrupted` (audited).
- `test_coordinator_restart_reattaches_same_batch`: re-entering with same metadata re-queries the same `batch_id`, does **not** submit a duplicate.
- `test_coordinator_never_invokes_local_worker`: assert launched module is `app.remote_execution.coordinator`, not `app.analysis.worker`.

**Expected failure (RED):** `coordinator.py` / `coordinator_job_manager.py` do not exist.

**Minimal implementation:** add both modules with the interval loop + terminal mapping; the coordinator module exposes a `main()`.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_coordinator.py -v
```

**Commit checkpoint:** `feat: add remote coordinator and subprocess job manager`

---

# TASK 6 — `create_run(remote_gpu)` Freeze + Coordinator Dispatch (Never `app.analysis.worker`)

**First: read before editing**

- `backend/app/analysis/service.py` (`AnalysisService.create_run`, currently raises `EXECUTOR_UNAVAILABLE` for any non-`local_cpu`).
- `backend/app/analysis/job_manager.py` (`LocalJobManager`).
- `backend/app/remote_execution/request_builder.py` (Task 4), `coordinator_job_manager.py` (Task 5).
- `backend/app/analysis/router.py` (passes `job_manager`; need to pass a coordinator launcher + probe).
- `backend/app/remote_execution/executor.py` (probe).

**Interfaces (files):**

- `backend/app/analysis/service.py` (modify): extend constructor
  ```python
  def __init__(self, session, registry, job_manager,
               *, remote_executor_probe=None, remote_coordinator_launcher=None,
               orchestrator_commit=None, runtime_commit_config=None, asset_manifest_sha256=None):
  ```
  Add a `create_run` `remote_gpu` path:
  ```python
  if executor == "remote_gpu":
      # validate pipeline is remote-capable; validation must not import torch
      if "remote_gpu" not in definition.executors_supported:
          raise PlatformError("EXECUTOR_UNAVAILABLE", ...)
      if definition.cpu_supported or recording.label_space != definition.label_space:
          raise PlatformError("PIPELINE_INCOMPATIBLE", ...)
      metadata = freeze_request_provenance(...)   # pure
      run = AnalysisRunModel(id=f"run_{uuid4().hex}", ..., executor="remote_gpu",
                             status="pending", parameters_json={}, execution_metadata_json=metadata)
      self.session.add(run); self.session.commit(); self.session.refresh(run)
      try:
          run.worker_pid = remote_coordinator_launcher.launch(run.id)   # coordinator, NOT worker
          self.session.commit(); self.session.refresh(run)
      except Exception as exc:
          run.status = "failed"; run.error_type = "ANALYSIS_FAILED"; run.error_message = str(exc)[:1000]
          self.session.commit()
          raise PlatformError("ANALYSIS_FAILED", "Unable to launch remote coordinator.") from exc
      return run
  ```
  The existing `local_cpu` branch is **unchanged**.

- `backend/app/analysis/router.py` (modify): wire the coordinator launcher + probe + runtime-commit config into the `AnalysisService` construction (from request.app.state). No new endpoints required for core gate (an availability endpoint is optional).

- `backend/app/main.py` (modify): wire `RemoteProfile`/`SshRunner`/`SshRemoteExecutorProbe`/`CoordinatorJobManager` optionally; if remote config is missing, the app stays healthy and `remote_gpu` is unavailable (see Task 7). Pass `orchestrator_commit` from the current `git rev-parse HEAD` (boundary — see self-review) into `AnalysisService`.

**RED test (`backend/tests/test_remote_create_run.py`, new):** use a fake coordinator launcher (records launch) and a remote-capable fake pipeline; assert `LocalJobManager.start` is never called for `remote_gpu`.

Tests:
- `test_remote_create_run_freezes_all_provenance`: the run's `execution_metadata_json` has all locked keys.
- `test_remote_create_run_launches_coordinator_not_local_worker`: fake launcher called with the run id; assert `LocalJobManager` never invoked (`worker_pid` set only from launcher).
- `test_remote_create_run_failed_launch_marks_failed`: launcher raises → run `failed`.
- `test_local_cpu_path_unchanged`: existing local_cpu create still uses `LocalJobManager`.
- `test_remote_create_run_rejects_local_cpu_pipeline`: pipeline without `remote_gpu` → `EXECUTOR_UNAVAILABLE`.
- `test_remote_create_run_label_mismatch_rejected`: `PIPELINE_INCOMPATIBLE`.

**Expected failure (RED):** `create_run(remote_gpu)` raises `EXECUTOR_UNAVAILABLE` today.

**Minimal implementation:** add the `remote_gpu` create_run path + wiring; keep local path unchanged.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_create_run.py -v
```

**Commit checkpoint:** `feat: dispatch remote_gpu create_run to coordinator`

---

# TASK 7 — Startup Recovery + Bootstrap Wiring

**First: read before editing**

- `backend/app/analysis/service.py` (`mark_stale_running_runs_interrupted` — currently marks **every** `running` analysis run `interrupted`; must be scoped to `local_cpu` only).
- `backend/app/main.py` (`create_app` recovery block).
- `backend/app/analysis/model.py` (`AnalysisRunModel.executor`).
- `backend/app/remote_execution/profile.py` / `coordinator_job_manager.py`.

**Interfaces (files):**

- `backend/app/analysis/service.py` (modify): scope `mark_stale_running_runs_interrupted` to `local_cpu` only:
  ```python
  def mark_stale_running_runs_interrupted(session) -> int:
      # where status == "running" AND executor == "local_cpu"  -> interrupted
  ```
  Remote `pending`/`running` is **never** blindly interrupted.

- Add a startup helper (e.g. `app/remote_execution/recovery.py` or `app/remote_execution/coordinator_recovery.py`):
  ```python
  def coordinate_orphaned_remote_runs(session, *, launcher, seen_run_ids: set[str]) -> int:
      """Finds remote_gpu pending/running runs and starts/reattaches one
      coordinator per run. If a run is already being coordinated, it is skipped
      (dedup within a single startup pass). No GET/list side effects."""
  ```
  Dedup within one pass (an in-memory `seen_run_ids` set) to avoid double launch.

- `backend/app/main.py` (modify): in `create_app`, after recovery:
  - call `mark_stale_running_runs_interrupted` (local_cpu only);
  - call `coordinate_orphaned_remote_runs` with the coordinator launcher;
  - wire `RemoteProfile`/`SshRunner`/`SshRemoteExecutorProbe`/`CoordinatorJobManager` **optionally** — missing remote config leaves the app healthy with `remote_gpu` unavailable (probe returns unavailable), never prevents startup.

**RED test (`backend/tests/test_remote_startup_recovery.py`, new):** use a fake launcher + a DB session with mixed runs.

Tests:
- `test_mark_stale_interrupts_local_cpu_running_only`: a `local_cpu` running run → interrupted; a `remote_gpu` running run → **not** interrupted.
- `test_startup_preserves_remote_pending_and_running`: remote `pending`/`running` remain `pending`/`running`.
- `test_startup_relaunches_coordinator_for_remote_runs`: running `remote_gpu` → launcher called with its run id.
- `test_startup_dedupes_coordinator_within_pass`: a run already handled in this pass is not launched twice.
- `test_startup_no_get_list_side_effects`: helper performs no HTTP GET/list; only DB queries.
- `test_missing_remote_config_keeps_app_healthy_and_reports_unavailable`: `create_app` with no `WSP_REMOTE_*` env → app builds; `executor_availability(remote_gpu)` returns `available=False` and startup succeeds.
- `test_missing_remote_config_does_not_block_startup`: creating the app with no remote config does not raise.

**Expected failure (RED):** `mark_stale` currently interrupts ALL running runs (including remote); no coordinator recovery helper.

**Minimal implementation:** scope the stale-interrupt to `local_cpu`; add `coordinate_orphaned_remote_runs`; wire bootstrap.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_startup_recovery.py -v
```

**Commit checkpoint:** `feat: scope restart recovery and reattach remote coordinators`

---

# TASK 8 — Full Backend Regression + Final Static/Self-Review Gate

**Final safety verification (no new behavior; only confirms no regression):**

- Run the full backend suite under `.venv`:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -v
  ```
  Require zero failures. Preserve all existing tests.
- Run the 12F-A-focused suite with a fake transport/job-manager (no GPU, no SSH):
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
    backend/tests/test_zoomspec_definition_registry.py \
    backend/tests/test_remote_probe_error_channel.py \
    backend/tests/test_remote_runtime_commit_config.py \
    backend/tests/test_remote_request_freeze.py \
    backend/tests/test_remote_coordinator.py \
    backend/tests/test_remote_create_run.py \
    backend/tests/test_remote_startup_recovery.py -v
  ```
- Static guard: scan the new production modules for forbidden tokens (`import torch`, `import ultralytics`, `from ultralytics`, `import scipy`, `/root/autodl-tmp`, `sys.path` mutation, `subprocess` outside the coordinator launcher, arbitrary stderr leak). The control plane must be torch-free.
- `git diff --check` clean.

**RED expectation:** N/A (verification task). If any regression appears, return to the affected task.

**GREEN commands:** the two pytest commands above.

**Commit checkpoint:** `chore: verify task12f-a regression and static guards`

---

## Final Scope (12F-A implemented files)

```
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py        (new)
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py          (modify: shared definition)
backend/app/pipelines/registry.py                                                (modify: register stub)
backend/app/remote_execution/transport.py                                        (modify: error channel)
backend/app/remote_execution/executor.py                                         (modify: probe adapter)
backend/app/remote_execution/profile.py                                          (modify: runtime commit)
backend/app/remote_execution/request_builder.py                                  (new)
backend/app/remote_execution/coordinator.py                                      (new)
backend/app/remote_execution/coordinator_job_manager.py                          (new)
backend/app/remote_execution/recovery.py (or coordinator_recovery.py)            (new)
backend/app/analysis/service.py                                                  (modify)
backend/app/analysis/router.py                                                   (modify: wiring)
backend/app/main.py                                                              (modify: bootstrap)
backend/tests/test_zoomspec_definition_registry.py                               (new)
backend/tests/test_remote_probe_error_channel.py                                 (new)
backend/tests/test_remote_runtime_commit_config.py                               (new)
backend/tests/test_remote_request_freeze.py                                      (new)
backend/tests/test_remote_coordinator.py                                         (new)
backend/tests/test_remote_create_run.py                                          (new)
backend/tests/test_remote_startup_recovery.py                                    (new)
```

**Explicitly NOT modified:** `remote_execution/runner.py` (remote probe body is 12F-B), no `frn.py`/`detector.py`/`preprocessing.py`/`ahlp.py`/`postprocess.py` scientific change, no `recordings`, no `imported_runs`, no frontend, no new ORM entity.

---

## Self-Review

- **Spec coverage:** 12F-A scope covers spec §2 (subtask A), §3 (registry/definition + shared definition owner), §4 (create_run freeze + request provenance), §5 (one local coordinator subprocess, batch-first not in V1), §6 (restart: local_cpu interrupt only, remote re-coordinate), §7 (RemoteProfile vs RemoteWorkerContext — only local half here), §8.1 (local control-plane probe adapter), §13 (torch-free control plane, fixed-argv, no secrets), §14 (12F-A acceptance: registry no model load, probe mapping, freeze, coordinator not worker, idempotent attachment, startup interrupt local only, ingest-once, failed/interrupted mapping). §8.2/§9/§11 remote bodies are intentionally 12F-B.
- **Placeholders:** none — every task lists exact files, interfaces, RED test, expected failure, minimal implementation, GREEN command, commit checkpoint. The repo's existing `frn.py`/`postprocess.py`/`ZoomSpecFrozenPipeline` (Task 12E) are referenced but not modified for 12F-A except the shared-definition reuse.
- **Interface/type consistency:** reuses `PipelineDefinition` (shared object), `RemoteExecutionBatchV1`, `compute_request_sha256`/`canonical_request_payload`, `ExecutorAvailabilityRead`, `RemoteExecutorProbe`, `build_recording_fingerprint`/`resolve_source_data_sha256`, `RemoteProfile`, `SshRunner`, `ingest_remote_result`/`AnalysisResultWriter`. No new ORM. `required_remote_runtime_commit` is sourced only from validated `RemoteProfile`. `request_sha256` reconstructs exactly from persisted `execution_metadata_json`.
- **Boundary:** `AnalysisService` never contains SSH polling logic; the coordinator owns the loop. The coordinator is restart-safe/idempotent and re-queries the same `batch_id` first. `local_cpu` behavior is unchanged. Task 12F-B is explicitly out of scope; the probe adapter works with deterministic fake transport responses.
- **One open item (non-blocking):** where the local control plane obtains `orchestrator_commit` (the local repo `git rev-parse HEAD` at launch) is a boundary detail resolved by Task 6 wiring via a small injected accessor; the plan deliberately keeps it separate from `required_remote_runtime_commit` and avoids hardcoding.
