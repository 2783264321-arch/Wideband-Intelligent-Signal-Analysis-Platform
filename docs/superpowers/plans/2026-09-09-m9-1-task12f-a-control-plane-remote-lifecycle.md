# M9.1 Task 12F-A — Control-Plane Remote Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the **local / control-plane** half of remote execution task 12F (subtask 12F-A) so the platform can expose the frozen `zoomspec_yolo26n_aug_combined_frn_v3` pipeline as a `remote_gpu`-only pipeline, verify remote availability through the existing `RemoteProfile`/`SshRunner`, and launch/freeze/supervise a per-run **local coordinator subprocess** that drives a single remote `AnalysisRun` to completion — without ever invoking the local scientific worker.

**Architecture:** The control plane stays entirely lightweight: registering ZoomSpec must not import torch/ultralytics or construct CPN/FRN models. `create_run(executor=remote_gpu)` freezes immutable request/provenance into `execution_metadata_json`, constructs a canonical `RemoteExecutionBatchV1` (one item per run, parameters `{}`), then dispatches a local coordinator subprocess (never `app.analysis.worker`). The coordinator runs a deterministic submit→poll→download→ingest→terminal loop, is restart-safe and idempotent under a **per-launch coordinator fencing token**, and reuses the existing `result_ingestor`/`AnalysisResultWriter` as the **only** persistence path. Startup recovery distinguishes `local_cpu` (interrupt) from `remote_gpu` (re-attach under a fresh fencing token) and never blindly interrupts a remote worker. No `RemoteJob`/`GpuRun`/ORM entity is created.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic, OpenSSH/SCP subprocess transport (no `shell=True`), NumPy, pytest. No GPU and no real SSH required for 12F-A tests.

**Spec:** `docs/superpowers/specs/2026-09-09-m9-1-task12f-remote-platform-integration-design.md`

**Base:** `feature/m9-1-live-remote-gpu-inference @ a84b8395d33ce59039296efcb49842f02ec2d4e1`

---

## Scope And Boundaries

**In scope (12F-A only):**
- Lightweight shared ZoomSpec definition + remote-only registry stub (no model load / no torch import).
- Local `RemoteExecutorProbe` adapter + `SshRunner` probe invocation + deterministic success/failure mapping.
- **Frozen `RemoteProbeResponseV1` producer contract** (stdout JSON schema) for 12F-B; 12F-A only consumes it via fake responses.
- Explicit validated deployment source for `required_remote_runtime_commit` (no hardcoded SHA).
- Local identity resolution for recording fingerprint / source hash / `asset_manifest_sha256` / `orchestrator_commit`.
- Remote request/provenance freeze + canonical `RemoteExecutionBatchV1` construction.
- `create_run(executor=remote_gpu)` → local coordinator subprocess (never `app.analysis.worker`); availability probe consulted first.
- Coordinator submit/or-create-attach → poll → download → ingest → terminal-state mapping, fenced by `coordinator_token`.
- Restart recovery: `local_cpu running → interrupted`; `remote_gpu pending/running → rotate token + respawn coordinator`.
- `result_ingestor`/`AnalysisResultWriter` remains the only persistence path.
- All tests run without GPU and without real SSH (fake subprocess/transport/job-manager objects).

**Out of scope (12F-B and later):**
- Remote `runner._cli_probe` production body and remote readiness semantics (12F-B producer). 12F-A does **not** modify `runner.py`.
- Remote GPU production `ItemExecutor` / `RemoteWorkerContext` / `ZoomSpecRemoteItemExecutor`.
- Real SSH probe success (post-12F-B integration gate).
- Multi-GPU scheduling, batch UI, remote HTTP inference server, Redis/Celery, `RemoteJob` ORM.
- Any change to Task 12A–E scientific semantics.

**Design note — probe split:** 12F-A owns the *local* probe adapter and its deterministic mapping; the adapter validates the frozen `RemoteProbeResponseV1` and compares runtime/manifest identities to local expectations. 12F-A tests inject fake transport responses; 12F-B later implements the producer.

---

## Frozen Probe Wire Contract (authoritative for 12F-B producer)

**Runner error output:** `runner.main()` prints exactly one stderr line `f"{code}: {message}"` and returns 1 for a controlled `PlatformError`. There are **no stdout** `error_code`/`error_message` markers.

**Success probe stdout (frozen producer contract, strict JSON):**

```json
{
  "schema_version": 1,
  "status": "available",
  "remote_runtime_commit": "<40 lowercase hex>",
  "asset_manifest_sha256": "<64 lowercase hex>",
  "device": 0
}
```

`RemoteProbeResponseV1` model (in `remote_execution/schema.py` or the adapter module) validates exactly these fields with the existing `GitCommitSha` / `Sha256Hex` / `Literal[1]` / status `Literal["available"]` constraints. 12F-A fake transport responses produce this exact JSON; the adapter validates it and verifies `remote_runtime_commit == profile.required_remote_runtime_commit` and `asset_manifest_sha256 == local manifest self-hash`. 12F-B is the producer and is out of scope here.

---

# TASK 1 — Lightweight Shared Definition And Remote-Only Registry Stub

**First: read before editing**

- `backend/app/pipelines/base.py` (`PipelineDefinition`, `Pipeline`, `PipelineOutput`).
- `backend/app/pipelines/registry.py` (`create_pipeline_registry`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (`ZoomSpecFrozenPipeline` — its `definition` property currently builds a literal `PipelineDefinition`; its **constructor loads CPN/FRN models**).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/__init__.py`, `dummy.py`, `stft_energy/pipeline.py`.

**Interfaces (files to create/modify):**

- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py` (new):
  ```python
  from app.pipelines.base import Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
  from app.core.errors import PlatformError

  ZOOMSPEC_FROZEN_DEFINITION = PipelineDefinition(
      id="zoomspec_yolo26n_aug_combined_frn_v3", name="ZoomSpec YOLO26n + Combined FRN V3",
      version="1.0.0", label_space="spacenet_14", recommended_device="GPU", cpu_supported=False,
      stages=("ls_stft", "cpn", "ahlp", "frn", "postprocess"), inspectable_stages=(),
      task_capability="detection_classification", executors_supported=("remote_gpu",),
      recommended_executor="remote_gpu",
  )

  class ZoomSpecRemoteOnlyPipeline(Pipeline):
      @property
      def definition(self) -> PipelineDefinition:
          return ZOOMSPEC_FROZEN_DEFINITION
      def run(self, recording: RecordingInput, parameters: dict, workspace: Path) -> PipelineOutput:
          raise PlatformError("EXECUTOR_UNAVAILABLE", "ZoomSpec frozen pipeline is remote_gpu only and cannot run locally.")
  ```

- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (modify): `ZoomSpecFrozenPipeline.definition` returns `ZOOMSPEC_FROZEN_DEFINITION`; the model-loading constructor is unchanged.

- `backend/app/pipelines/registry.py` (modify): register `ZoomSpecRemoteOnlyPipeline()` in `create_pipeline_registry()`.

**RED test (`backend/tests/test_zoomspec_definition_registry.py`, new):**

```python
def test_import_registry_does_not_import_torch_or_ultralytics():
    # fresh subprocess: import app.pipelines.registry; assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules
```

Tests:
- `test_zoomspec_definition_matches_frozen_contract`: id/version/label_space/cpu_supported=False/executors_supported==("remote_gpu",)/recommended_executor=="remote_gpu"/task_capability=="detection_classification".
- `test_registry_exposes_zoomspec_without_model_load`: `create_pipeline_registry().get("zoomspec_yolo26n_aug_combined_frn_v3")` returns the stub; `.definition.id` matches.
- `test_zoomspec_remote_only_stub_cannot_run_locally`: `pytest.raises(PlatformError)` code `EXECUTOR_UNAVAILABLE`.
- `test_importing_registry_does_not_import_torch_or_ultralytics`.
- `test_zoomspec_frozen_pipeline_definition_matches_shared_definition`: evaluate `ZoomSpecFrozenPipeline.definition` **without invoking the model-loading constructor** via `object.__new__(ZoomSpecFrozenPipeline)` and assert it equals `ZOOMSPEC_FROZEN_DEFINITION` (do **not** compare the class property object itself to a `PipelineDefinition`).

**Expected failure (RED):** registry has no ZoomSpec; `definition.py` missing; `pipeline.py` builds its own literal (non-shared) definition.

**Minimal implementation:** create `definition.py`, register stub, share the frozen definition.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_definition_registry.py -v
```

**Commit checkpoint:** `feat: add zoomspec remote-only registry definition`

---

# TASK 2 — Runner/Probe Wire Contract + Local Probe Adapter

**First: read before editing**

- `backend/app/remote_execution/transport.py` (`SshRunner`, `RemoteTransportError`, `_invoke`, `run_runner`, `_is_safe_runner_token`).
- `backend/app/remote_execution/runner.py` `main()` (line ~692) — controlled `PlatformError` prints `f"{code}: {message}"` to stderr, returns 1. **Do not modify runner.py in 12F-A.**
- `backend/app/remote_execution/executor.py` (`RemoteExecutorProbe` Protocol).
- `backend/app/remote_execution/profile.py` (`RemoteProfile`) — Task 3 adds runtime commit; Task 2 may reference the field as present after Task 3.
- `backend/app/analysis/schema.py` (`ExecutorAvailabilityRead`).
- `backend/app/core/errors.py` (`PlatformError`).

**Runner error contract (LOCKED):** controlled runner error is exactly one stderr line `CODE: message` (Code uppercase, validated identifier), returncode 1. There are no stdout markers. `_cli_probe` currently always raises `REMOTE_PROBE_UNAVAILABLE` (12F-B producer); 12F-A consumes only the frozen contract.

**Interfaces (files):**

- `backend/app/remote_execution/transport.py` (modify):
  ```python
  class RemoteTransportError(RuntimeError):
      """Generic SSH/SCP/transport failure. Never carries arbitrary server stderr."""
      pass

  class RemoteRunnerExit(RemoteTransportError):
      """A runner subprocess returned nonzero with a single controlled stderr
      line 'CODE: message'. Only a validated uppercase error code + bounded
      sanitized message are retained; arbitrary stderr is never exposed."""
      def __init__(self, returncode: int, code: str | None, message: str | None, stdout: str):
          super().__init__(f"Remote runner exited with code {returncode}.")
          self.returncode = returncode
          self.code = code            # validated identifier or None
          self.message = message      # sanitized, truncated, no traceback
          self.stdout = stdout
  ```
  - Keep `_invoke` **generic**: any SSIM/transport nonzero → `RemoteTransportError`. Do **not** classify generic failure as a runner error.
  - Add a **runner-specific invocation path** used only by `run_runner` (e.g. `_invoke_runner` that calls `run_process` and, on nonzero, inspects **stderr**):
    - if `stderr.strip()` strictly matches one bounded runner-error line `CODE: message` (uppercase `[A-Z_]+` code, single space, non-empty bounded message) → raise `RemoteRunnerExit(returncode, code, _sanitize(message), stdout)`;
    - otherwise (arbitrary ssh stderr / traceback) → raise `RemoteTransportError` (generic).
  - Add helpers `_parse_runner_error_line(stderr) -> tuple[str, str] | None` and `_sanitize(message) -> str` (truncate to a bounded length, strip control chars, never include traceback / raw paths).
  - `run_runner` uses the runner-specific path; `upload_file`/`download_file` keep the generic `_invoke`.

- `backend/app/remote_execution/schema.py` (or executor module) (new): freeze `RemoteProbeResponseV1` (see Frozen Probe Wire Contract above).

- `backend/app/remote_execution/executor.py` (modify):
  ```python
  def _probe_response_from_stdout(stdout: str) -> RemoteProbeResponseV1:
      # strict JSON parse + field validation; raise on malformed.

  class SshRemoteExecutorProbe:
      def __init__(self, profile: RemoteProfile, transport: SshRunner): ...
      def availability(self, recording, pipeline, source_data_sha256, *, expected_runtime_commit, expected_manifest_sha256) -> ExecutorAvailabilityRead:
          # invoke transport.run_runner("probe", ())
          # success → parse RemoteProbeResponseV1 → validate identities == local expectations → available=True (mismatch → explicit reason)
          # RemoteRunnerExit(code=...) → available=False + mapped reason
          # RemoteTransportError → available=False + REMOTE_TRANSPORT_UNAVAILABLE
  ```

**RED test (`backend/tests/test_remote_probe_error_channel.py`, new):** fake `run_process` returns controlled runner exits and generic transport failures.

Tests:
- `test_probe_success_maps_available_true`: fake zero exit, stdout = exact `RemoteProbeResponseV1` JSON → `available=True`, `reason_code is None`, and identity checks pass.
- `test_probe_runtime_commit_mismatch_is_unavailable`: response `remote_runtime_commit != profile.required_remote_runtime_commit` → `available=False`, reason `REMOTE_IMPLEMENTATION_MISMATCH`.
- `test_probe_manifest_mismatch_is_unavailable`: response `asset_manifest_sha256 != local` → `available=False`, reason `PIPELINE_ASSET_MISMATCH`.
- `test_probe_structured_runner_failure_maps_explicit_unavailable`: stderr `REMOTE_IMPLEMENTATION_MISMATCH: <msg>` → `available=False`, `reason_code=="REMOTE_IMPLEMENTATION_MISMATCH"`.
- `test_runner_error_requires_single_uppercase_code_line`: a two-line stderr or lowercase/freeform stderr → **generic `RemoteTransportError`** (not `RemoteRunnerExit`).
- `test_generic_ssh_failure_stays_remote_transport_error`: nonzero with arbitrary ssh stderr (e.g. `Connection refused\n...`) → `RemoteTransportError`.
- `test_probe_does_not_leak_stderr_traceback`: user-facing `reason_message` never contains `Traceback` / `.py` path / arbitrary stderr.
- `test_probe_malformed_stdout_maps_unavailable`: zero exit but stdout is not valid `RemoteProbeResponseV1` → `available=False`, `REMOTE_PROBE_UNAVAILABLE`.

**Expected failure (RED):** `RemoteRunnerExit`/`RemoteProbeResponseV1`/`SshRemoteExecutorProbe` missing; `_invoke` collapses everything to `RemoteTransportError`.

**Minimal implementation:** runner-specific error path in transport, frozen response model, probe adapter + identity verification.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_probe_error_channel.py -v
```

**Commit checkpoint:** `feat: freeze probe wire contract and local probe adapter`

---

# TASK 3 — Validated Deployment Source For `required_remote_runtime_commit`

**First: read before editing**

- `backend/app/remote_execution/profile.py` (`RemoteProfile`, `from_env` — currently has no runtime-commit field).
- `backend/app/remote_execution/schema.py` (`GitCommitSha` `^[0-9a-f]{40}$`).

**Decision (LOCKED):** `required_remote_runtime_commit` MUST be a **validated 40-char deployment-configured commit** sourced from `RemoteProfile` (env `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT`), separate from the locally-observed `orchestrator_commit`. No hardcoded SHA in production.

**Interfaces (file):**

- `backend/app/remote_execution/profile.py` (modify): add
  ```python
  def _require_runtime_commit(value: str) -> str:
      if not _GIT_COMMIT_RE.fullmatch(value):  # ^[0-9a-f]{40}$
          raise _unavailable("WSP_REMOTE_REQUIRED_RUNTIME_COMMIT must be a 40-hex commit.")
      return value
  ```
  Add `required_remote_runtime_commit: str` to `RemoteProfile`; parse `_require_runtime_commit(_get("WSP_REMOTE_REQUIRED_RUNTIME_COMMIT"))`. Keep fail-closed.

**RED test (`backend/tests/test_remote_runtime_commit_config.py`, new):**

Tests:
- `test_valid_runtime_commit_accepted`.
- `test_missing_runtime_commit_is_unavailable`: absent env → `PlatformError("REMOTE_EXECUTOR_UNAVAILABLE")`.
- `test_invalid_runtime_commit_rejected`: non-40-hex / wrong length → rejected.
- `test_runtime_commit_not_derived_from_local_git`: it is read only from env, not `git rev-parse`.

**Expected failure (RED):** `RemoteProfile` has no runtime-commit field.

**Minimal implementation:** add validator + field + parse.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_runtime_commit_config.py -v
```

**Commit checkpoint:** `feat: source required runtime commit from validated profile`

*(Task 3 does not touch request_builder.py; hardcoded-SHA source scans and builder/orchestrator separation live in Tasks 4/6.)*

---

# TASK 4 — Pure Request/Provenance Builder + Canonical Batch + Local Identity Resolution

**First: read before editing**

- `backend/app/remote_execution/schema.py` (`RemoteExecutionBatchV1`, `RemoteExecutionRequestV1`, `RemoteExecutionItemV1` — note it requires `local_run_id`, `RemotePipelineRefV1`, `RemoteRecordingRefV1`, `GitCommitSha`/`WireIdentifier`/`Sha256Hex`/`LabelSpace`).
- `backend/app/remote_execution/canonical.py` (`compute_request_sha256`, `canonical_request_payload`, `canonical_request_bytes`).
- `backend/app/remote_execution/assets.py` (`load_pipeline_asset_manifest` — strict loader that validates the self-hash `asset_manifest_sha256`).
- `backend/app/remote_execution/source_hash.py` (`resolve_source_data_sha256(session, recording, data_root)`), `backend/app/remote_execution/resolver.py`, `backend/app/recordings/model.py`, `backend/app/imported_runs/fingerprint.py` (`build_recording_fingerprint`) and `backend/app/imported_runs/batch_validation.py` (`_manifest_recording_for(recording, gt_rows)`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json` (assets + `asset_manifest_sha256`).

**Decision (LOCKED):** `remote_execution/request_builder.py` is **construction-only** — no file/DB/GT I/O. It accepts **already-resolved** identities:

```python
# freeze_request_provenance input is the resolved identity bundle, not a recording/session.
```

Locked resolved-identity inputs:
- `local_run_id` (AnalysisRun id, generated BEFORE freeze — see Task 6)
- `recording_fingerprint` (from `build_recording_fingerprint`, resolved before freeze)
- `source_data_sha256` (from `resolve_source_data_sha256`, resolved before freeze)
- dataset identity: `dataset_name`, `dataset_split`, `dataset_key` (= `recording.name`), `label_space`
- pipeline id/version (from `ZOOMSPEC_FROZEN_DEFINITION`)
- `required_remote_runtime_commit` (from `RemoteProfile`)
- `orchestrator_commit` (from the local `git rev-parse HEAD` helper — Task 6)
- `asset_manifest_sha256` (from `load_pipeline_asset_manifest` self-hash)
- `remote_profile` (profile name)
- `parameters == {}`

**Local identity resolution (LOCKED — pinned):** add **one** small local identity resolver, `remote_execution/identity.py`:
```python
class RemoteRecordingIdentity:
    recording_fingerprint: str
    source_data_sha256: str
    dataset_name: str
    dataset_split: str
    dataset_key: str
    label_space: str
    local_run_id: str

def resolve_remote_recording_identity(session, recording, data_root, local_run_id) -> RemoteRecordingIdentity:
    source = resolve_source_data_sha256(session, recording, data_root)          # exact raw-IQ bytes
    gt_rows = list(session.scalars(select(GroundTruthModel).where(GroundTruthModel.recording_id == recording.id)))
    manifest_recording = _manifest_recording_for(recording, gt_rows)            # records + GT semantics
    fingerprint = build_recording_fingerprint(recording.dataset_name, recording.dataset_split, recording.label_space, manifest_recording).sha256
    return RemoteRecordingIdentity(..., dataset_key=recording.name, ...)

def resolve_local_orchestrator_commit(project_root) -> str:
    # read-only: git -C <project_root> rev-parse HEAD -> exactly 40 lowercase hex; no Git mutation.

def resolve_asset_manifest_sha256(asset_manifest_path) -> str:
    manifest = load_pipeline_asset_manifest(asset_manifest_path)                # strict loader + validated self-hash
    return manifest.asset_manifest_sha256
```
No second fingerprint/hash scheme; reuses `build_recording_fingerprint`, `resolve_source_data_sha256`, `load_pipeline_asset_manifest`. `_manifest_recording_for` is the exact existing Recording→ManifestRecording builder (used by `batch_validation`); promate/reuse it (not reinvent). `resolve_local_orchestrator_commit` runs `git rev-parse HEAD` read-only and validates exactly 40 lowercase hex.

**Builder (file):**

`backend/app/remote_execution/request_builder.py` (new):

```python
def freeze_request_provenance(*, local_run_id, recording_fingerprint, source_data_sha256,
                              dataset_name, dataset_split, dataset_key, label_space,
                              pipeline_id, pipeline_version, required_remote_runtime_commit,
                              orchestrator_commit, asset_manifest_sha256, remote_profile,
                              *, id_factory=None) -> dict:
    """Pure provenance construction. ID factory injectable for deterministic tests;
    production uses fresh safe UUIDs (default). Returns the exact metadata dict."""

def build_batch(metadata: dict) -> RemoteExecutionBatchV1:
    """Reconstruct an identical RemoteExecutionBatchV1 from metadata alone."""

def verify_request_sha256(batch: RemoteExecutionBatchV1, metadata: dict) -> bool:
    return compute_request_sha256(batch) == metadata["request_sha256"]
```

`freeze_request_provenance` generates `request_id`/`batch_id`/`item_key` via the (injected) id factory (default fresh safe IDs), builds the batch (one item, `parameters={}`), computes `request_sha256 = compute_request_sha256(batch)`, returns metadata including `local_run_id`. `build_batch` reconstructs the full item from metadata alone (request_id/batch_id/item_key/local_run_id/pipeline/recording/parameters) and recomputes `request_sha256`.

**`execution_metadata_json` keys (exact, ordered):**
```
request_id, batch_id, item_key, local_run_id, request_sha256, orchestrator_commit,
required_remote_runtime_commit, asset_manifest_sha256, pipeline_id, pipeline_version,
remote_profile, recording_fingerprint, source_data_sha256, dataset_name, dataset_split,
dataset_key, label_space, parameters, coordinator_token
```
**Initial metadata does NOT contain `payload_sha256`** — `payload_sha256` is appended only after successful ingest (by `result_ingestor`, unchanged). `coordinator_token` is set by Task 5/7 (Task 5 sets on first launch; Task 7 rotates).

**RED test (`backend/tests/test_remote_request_freeze.py`, new):**

Tests:
- `test_freeze_produces_all_required_provenance_keys`: metadata has every locked key.
- `test_freeze_has_no_payload_sha256_initially`: `payload_sha256` absent before ingest.
- `test_freeze_parameters_is_empty_dict`.
- `test_batch_has_one_item_per_run_and_requires_local_run_id`: `len(batch.items)==1`; `batch.items[0].local_run_id == metadata["local_run_id"] == local_run_id`.
- `test_request_sha_reconstructs_from_persisted_metadata`: freeze once → persist metadata → `build_batch(metadata)` → `compute_request_sha256(...) == metadata["request_sha256"]` and `verify_request_sha256(...) is True`.
- `test_two_fresh_freezes_generate_distinct_ids_and_shas`: two freezes with default (random) id factory differ in request_id/batch_id/item_key and normally differ in request_sha256.
- `test_deterministic_id_factory_produces_reproducible_sha`: with an injected deterministic id factory, two freezes of identical resolved identities produce the same request_sha256.
- `test_ids_are_system_generated_and_format_validated`: ids match `WireIdentifier`, not free text.
- `test_batch_parameters_empty_and_no_host_provenance_in_parameters`.
- `test_identity_resolution_reuses_existing_primitives`: `resolve_remote_recording_identity` calls `resolve_source_data_sha256`, `build_recording_fingerprint`, `_manifest_recording_for` (assert via mocks/spies), no new hash scheme.
- `test_orchestrator_commit_from_local_git_validated_40hex`: `resolve_local_orchestrator_commit` returns a valid 40-hex; test with a real `git rev-parse HEAD` in a fixture repo.
- `test_asset_manifest_sha256_not_hardcoded`: `resolve_asset_manifest_sha256` uses `load_pipeline_asset_manifest` self-hash (no literal `16cc053…` in the builder).

**Expected failure (RED):** `request_builder.py`/`identity.py` missing.

**Minimal implementation:** identity resolver + pure builder + build_batch/verify.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_request_freeze.py -v
```

**Commit checkpoint:** `feat: freeze remote request provenance and canonical batch`

---

# TASK 5 — Coordinator + Coordinator Job Manager (Subprocess) + Fencing

**First: read before editing**

- `backend/app/remote_execution/request_builder.py` (Task 4), `remote_execution/schema.py`, `remote_execution/job_manager.py` (`RemoteGpuJobManager`), `remote_execution/result_ingestor.py`, `remote_execution/validation.py` (`AnalysisResultWriter`), `analysis/model.py`, `analysis/service.py`.

**Decision (LOCKED):** three modules:
- `request_builder.py` — pure construction (Task 4).
- `coordinator.py` — one-run loop, restart-safe, idempotent, **fenced**.
- `coordinator_job_manager.py` — subprocess launcher passing `coordinator_token`.

**Fencing token (LOCKED):** `coordinator_token` is a per-launch **local** fencing token:
- stored in `execution_metadata_json`, but **EXCLUDED** from `RemoteExecutionBatchV1` and from `request_sha256` (it is not a semantic request field),
- passed to the launched coordinator (env/argv);
- the coordinator verifies its token still equals the DB token before any polling side effect, download, ingest, or terminal mutation; on mismatch it exits without mutating the run;
- startup recovery rotates/persists a NEW token BEFORE launching a replacement coordinator (Task 7);
- `worker_pid` remains the local coordinator PID (never a remote PID).

**Interfaces (files):**

- `backend/app/remote_execution/coordinator_job_manager.py` (new):
  ```python
  class CoordinatorJobManager:
      def __init__(self, settings: Settings, *, popen_factory=None): ...
      def launch(self, run_id: str, coordinator_token: str, *, coordinator_module="app.remote_execution.coordinator") -> int:
          # subprocess.Popen([sys.executable, "-m", coordinator_module, run_id, "--coordinator-token", token],
          #                  cwd=backend_root, env=<WSP_*>, DEVNULL, shell=False, close_fds=True)
          # injectable popen_factory so tests never spawn a real subprocess.
  ```

- `backend/app/remote_execution/coordinator.py` (new):
  ```python
  class Coordinator:
      def __init__(self, *, job_manager, ingestor, metadata, coordinator_token, sleep_fn=None, poll_interval=1.0, logger=None): ...
      def run(self) -> str:  # terminal status
          # 1) verify coordinator_token == DB token, else exit
          # 2) rebuild batch via build_batch(metadata); verify request_sha256
          # 3) submit-or-attach: submit the SAME immutable batch every start (server create-or-attach idempotent)
          # 4) poll: RemoteBatchStatusV1
          #    - queued/running -> pending/running; set started_at once on running; bounded poll delay (sleep_fn)
          #    - completed -> download envelope+zip -> strict envelope parse -> ingest_remote_result once -> completed
          #    - failed -> failed
          #    - interrupted / unrecoverable remote job -> interrupted
          #    - uncertain generic submit/status transport failure -> re-query SAME batch before terminal decision
          #    - transient status unavailable -> remain recoverable, retry with bounded delay
          # 5) terminal states set finished_at; completed only after local ingest transaction commits
  def main(argv=None) -> int:  # python -m app.remote_execution.coordinator <run_id> --coordinator-token <token>
  ```
  Local AnalysisRun lifecycle mapping: remote `queued`→`pending`; `running`→`running` (`started_at` once); `completed`→`completed` only after local ingest transaction; `failed`/`interrupted`→terminal with `finished_at`. `ingest_remote_result` stays the only persistence path (no direct output writes).

- `backend/app/remote_execution/job_manager.py` (modify): `RemoteGpuJobManager` must preserve `RemoteRunnerExit` codes/messages for `submit`/`status` (currently catches `RemoteTransportError` and collapses them). Generic transport remains generic. Map: `RemoteRunnerExit` → `PlatformError(REMOTE_SUBMIT_FAILED / REMOTE_STATUS_UNAVAILABLE ...)` carrying the runner code; `RemoteTransportError` → generic transport error. This preserves safe runner diagnostics for the coordinator to interpret.

**RED test (`backend/tests/test_remote_coordinator.py`, new):** fake job-manager transport responses (fake `RemoteGpuJobManager`/`SshRunner`) + call-through fake ingestor; inject `sleep_fn` so tests never hang.

Tests:
- `test_coordinator_rejects_stale_fencing_token`: DB token != coordinator token → exits without download/ingest/terminal mutation.
- `test_coordinator_request_sha_reconstructs_from_persisted_metadata`.
- `test_coordinator_submits_same_batch_every_start`: two starts submit the same immutable batch (idempotent create-or-attach), no duplicate.
- `test_coordinator_queued_running_completed_sequence`: `queued→running→completed`; started_at set once; download+ingest once; terminal `completed`.
- `test_coordinator_ingests_once_on_completion`.
- `test_coordinator_repeated_completion_is_idempotent`: re-run → no second ingest.
- `test_coordinator_failed_marks_failed` (terminal, finished_at set).
- `test_coordinator_interrupted_marks_interrupted`.
- `test_coordinator_transient_status_unavailable_retries_with_bounded_delay`: sleep_fn records calls; run stays recoverable, no terminal.
- `test_coordinator_uncertain_submit_transport_failure_reconciles_same_batch_before_terminal`.
- `test_coordinator_never_invokes_local_worker`: launched module is `app.remote_execution.coordinator`, not `app.analysis.worker`.
- `test_coordinator_token_excluded_from_request_sha`: changing only the token in metadata does not change `request_sha256`.

**Expected failure (RED):** `coordinator.py`/`coordinator_job_manager.py` missing; `job_manager` collapses runner exits.

**Minimal implementation:** coordinator + launcher + job-manager error preservation + fencing.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_coordinator.py -v
```

**Commit checkpoint:** `feat: add fenced remote coordinator and subprocess job manager`

---

# TASK 6 — `create_run(remote_gpu)` Freeze + Dispatch + Availability Route

**First: read before editing**

- `backend/app/analysis/service.py` (`create_run`, `executor_availability`), `analysis/job_manager.py` (`LocalJobManager`), `analysis/router.py`, `remote_execution/identity.py`/`request_builder.py` (Tasks 4), `coordinator_job_manager.py` (Task 5), `remote_execution/executor.py` (probe).

**Decision (LOCKED) — `create_run(remote_gpu)` semantics:**
- Generate the AnalysisRun id **BEFORE** freezing provenance.
- require `"remote_gpu" in definition.executors_supported`;
- validate label-space compatibility;
- **do NOT reject merely because `cpu_supported=True`** (ZoomSpec is `cpu_supported=False`, but the gate must not couple to it);
- the frozen ZoomSpec path **MUST explicitly reject any non-empty parameters** (`PIPELINE_INCOMPATIBLE` / `PARAMETERS_NOT_SUPPORTED`), never silently discard them;
- consult the same remote availability/probe service **before** creating/dispatching; unavailable → explicit `EXECUTOR_UNAVAILABLE` before any coordinator launch;
- on success, freeze provenance (with `local_run_id`), create `AnalysisRun(executor="remote_gpu", status="pending", execution_metadata_json=metadata)`, set `coordinator_token`, launch the coordinator via `CoordinatorJobManager`.
- `local_cpu` behavior unchanged.

**Interfaces (file):**

- `backend/app/remote_execution/startup.py` (new): `build_coordinator_metadata(metadata, *, coordinator_token) -> dict` that adds/rotates `coordinator_token` (used by create_run and recovery). (Keeps token logic out of AnalysisService.)
- `backend/app/analysis/service.py` (modify): extend constructor with `remote_executor_probe`, `remote_coordinator_launcher`, `identity_resolver`, `runtime_commit_config`, `orchestrator_commit_resolver`, `asset_manifest_sha256_resolver`, `id_factory=None`. Implement the `remote_gpu` create_run path as above. `local_cpu` unchanged.
- `backend/app/analysis/router.py` (modify): wire the new dependencies into `_service(...)`; add
  ```python
  @router.get("/api/executor-availability", response_model=ExecutorAvailabilityRead)
  def executor_availability(request, recording_id: str, pipeline_id: str):
      return _service(request, session).executor_availability(recording_id, pipeline_id)
  ```
  (No frontend work.)

**RED test (`backend/tests/test_remote_create_run.py`, new):**

Tests:
- `test_remote_create_run_requires_remote_gpu_capability`: pipeline without `remote_gpu` → `EXECUTOR_UNAVAILABLE`, probe not called.
- `test_remote_create_run_validates_label_space`: mismatched label space → `PIPELINE_INCOMPATIBLE`.
- `test_remote_create_run_does_not_reject_cpu_supported_true`: a remote-capable pipeline with `cpu_supported=True` is **not** rejected on that basis (only capability + label-space drive the gate).
- `test_remote_create_run_rejects_non_empty_frozen_parameters`: non-empty `parameters` → rejected; empty `{}` accepted.
- `test_remote_create_run_consults_probe_before_dispatch`: unavailable probe → `EXECUTOR_UNAVAILABLE`, coordinator launch **not** called.
- `test_remote_create_run_freezes_provenance_with_local_run_id`: `execution_metadata_json["local_run_id"] == run.id`, all locked keys present, `payload_sha256` absent.
- `test_remote_create_run_launches_coordinator_not_local_worker`: fake launcher receives run id + token; `LocalJobManager` never invoked; `worker_pid` set only from launcher.
- `test_remote_create_run_failed_launch_marks_failed`.
- `test_local_cpu_path_unchanged`.
- `test_executor_availability_route_success`: `GET /api/executor-availability` → `available=True` read model.
- `test_executor_availability_route_unavailable`: unavailable probe / no config → `available=False` read model.

**Expected failure (RED):** `create_run(remote_gpu)` raises `EXECUTOR_UNAVAILABLE`; no availability route.

**Minimal implementation:** remote create_run path + wiring + availability route.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_create_run.py -v
```

**Commit checkpoint:** `feat: dispatch remote_gpu create_run to coordinator with availability route`

---

# TASK 7 — Startup Recovery (local_cpu Only Interrupt; Remote Re-coordinate Under Fresh Token)

**First: read before editing**

- `backend/app/analysis/service.py` (`mark_stale_running_runs_interrupted`), `analysis/model.py`, `main.py`, `remote_execution/coordinator_job_manager.py`, `remote_execution/startup.py`.

**Decision (LOCKED):**
- `mark_stale_running_runs_interrupted` marks **`local_cpu`** `running` runs `interrupted` only; `remote_gpu` pending/running is **never** blindly interrupted.
- Startup recovery: for each `remote_gpu` pending/running run, **rotate/persist a NEW `coordinator_token`** first, then launch one replacement coordinator **only if valid remote config exists**. `seen_run_ids` is per-pass convenience only (dedup within one pass), not correctness.
- If remote configuration is missing at startup: app stays **healthy** (startup does not raise), availability=false, existing remote pending/running runs are **preserved** (not interrupted), and **no coordinator is launched** until valid config exists.
- An old surviving coordinator sees a stale token (rotated) and exits without mutating.

**Interfaces (file):**

- `backend/app/remote_execution/recovery.py` (new):
  ```python
  def mark_stale_local_cpu_runs_interrupted(session) -> int:  # status==running AND executor=="local_cpu"
  def find_orphaned_remote_runs(session) -> list[str]:          # remote_gpu pending/running
  def rotate_coordinator_token(metadata: dict) -> dict:         # replace coordinator_token
  def coordinate_orphaned_remote_runs(session, *, launcher, remote_config_available: bool, seen_run_ids: set[str]) -> int:
      # for each orphaned run: if remote_config_available: rotate token, persist, launch once (dedup within pass).
      # if not remote_config_available: leave runs untouched, launch nothing.
  ```

- `backend/app/main.py` (modify): in `create_app` recovery block, replace the blanket `mark_stale_running_runs_interrupted` with `mark_stale_local_cpu_runs_interrupted` + `coordinate_orphaned_remote_runs(..., remote_config_available=<bool>)`.

**RED test (`backend/tests/test_remote_startup_recovery.py`, new):** fake launcher + DB session.

Tests:
- `test_mark_stale_interrupts_local_cpu_running_only`: local_cpu running → interrupted; remote_gpu running → not interrupted.
- `test_startup_preserves_remote_pending_and_running`.
- `test_startup_relaunches_coordinator_with_fresh_token`: remote running → token rotated in DB + launcher called once with the new token.
- `test_startup_dedupes_coordinator_within_pass_two_runs`: two orphaned runs → two launches (one each), no double-launch of the same run.
- `test_old_surviving_coordinator_stale_token_exits`: coordinator with the OLD token sees mismatch and does not ingest/mutate (covered by coordinator test; recovery rotates token so the old token is stale).
- `test_missing_remote_config_keeps_app_healthy_and_preserves_runs`: no `WSP_REMOTE_*` → startup succeeds, remote pending/running preserved, **no** coordinator launch.
- `test_missing_remote_config_reports_unavailable`.
- `test_create_app_no_remote_config_does_not_raise`.

**Expected failure (RED):** blanket `mark_stale` interrupts remote runs; no recovery helper.

**Minimal implementation:** scope stale-interrupt to `local_cpu`; add recovery helper + token rotation; wire bootstrap.

**GREEN command:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_startup_recovery.py -v
```

**Commit checkpoint:** `feat: scope restart recovery and reattach fenced remote coordinators`

---

# TASK 8 — Full Backend Regression + Final Static/Self-Review Gate (verification only)

**No new behavior.** Verify only; commit only if verification causes an actual correction.

- Full backend suite:
  ```bash
  PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -v
  ```
  Require zero failures; preserve all existing tests.
- 12F-A-focused suite (no GPU, no SSH):
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
- Static guard: scan the new production modules for forbidden tokens (`import torch`, `import ultralytics`, `from ultralytics`, `import scipy`, `/root/autodl-tmp`, `sys.path` mutation, arbitrary stderr leak); the control plane must be torch-free.
- `git diff --check` clean.

**Commit:** only if a real correction was made.

---

## Final Scope (12F-A implemented files)

```
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py      (new)
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py        (modify: shared definition)
backend/app/pipelines/registry.py                                              (modify: register stub)
backend/app/remote_execution/transport.py                                      (modify: runner error path)
backend/app/remote_execution/schema.py                                         (modify: RemoteProbeResponseV1)
backend/app/remote_execution/executor.py                                       (modify: probe adapter)
backend/app/remote_execution/profile.py                                        (modify: runtime commit)
backend/app/remote_execution/identity.py                                       (new: local identity resolver)
backend/app/remote_execution/request_builder.py                                (new)
backend/app/remote_execution/coordinator.py                                    (new)
backend/app/remote_execution/coordinator_job_manager.py                        (new)
backend/app/remote_execution/startup.py                                        (new: coordinator_token helper)
backend/app/remote_execution/recovery.py                                       (new)
backend/app/remote_execution/job_manager.py                                    (modify: preserve runner exits)
backend/app/analysis/service.py                                                (modify)
backend/app/analysis/router.py                                                 (modify: wiring + availability route)
backend/app/main.py                                                            (modify: bootstrap + recovery)
backend/tests/test_zoomspec_definition_registry.py                             (new)
backend/tests/test_remote_probe_error_channel.py                               (new)
backend/tests/test_remote_runtime_commit_config.py                             (new)
backend/tests/test_remote_request_freeze.py                                    (new)
backend/tests/test_remote_coordinator.py                                       (new)
backend/tests/test_remote_create_run.py                                        (new)
backend/tests/test_remote_startup_recovery.py                                  (new)
```

**Explicitly NOT modified:** `remote_execution/runner.py` (12F-B producer; no change), no `frn.py`/`detector.py`/`preprocessing.py`/`ahlp.py`/`postprocess.py` scientific change, no `recordings`, no `imported_runs`, no frontend, no new ORM entity.

---

## Self-Review

- **Schema reconstruction:** `RemoteExecutionItemV1` requires `local_run_id`; Task 4 generates the AnalysisRun id before freeze, persists `local_run_id`, and `build_batch(metadata)` reconstructs the full item (request_id/batch_id/item_key/local_run_id/pipeline/recording/parameters) from metadata alone. Every schema-required field is reconstructible.
- **No forward dependency:** Task 3 adds `required_remote_runtime_commit` to profile; Task 2 only references it after Task 3; Task 4/6 do the hardcoded-SHA source scans and builder/orchestrator separation; Task 5 adds fencing; Task 6 wires create_run + availability route; Task 7 startup. Each RED fails only for that task's missing behavior.
- **Identity semantics:** the deterministic-id test uses an injected `id_factory`; two fresh freezes produce distinct random IDs and normally distinct SHAs (never asserted equal). No test contradicts UUID/request-identity semantics.
- **Fencing:** an old surviving coordinator, after token rotation, sees a mismatch and exits without download/ingest/terminal mutation; only the token rotates `request_sha256` unchanged (tested). `seen_run_ids` is per-pass convenience only.
- **Frozen parameters:** non-empty ZoomSpec parameters are explicitly rejected, never silently ignored.
- **Probe error channel:** generic SSH/SCP failure stays `RemoteTransportError`; only a single bounded `CODE: message` stderr line becomes `RemoteRunnerExit`. No arbitrary stderr/traceback reaches API users.
- **12F-A boundary:** no real SSH/GPU/runner-body implementation enters 12F-A; the probe adapter works with deterministic fake transport responses; `runner.py` is untouched. Availability is consulted before coordinator dispatch, and remote_gpu unavailable yields explicit `EXECUTOR_UNAVAILABLE`.
