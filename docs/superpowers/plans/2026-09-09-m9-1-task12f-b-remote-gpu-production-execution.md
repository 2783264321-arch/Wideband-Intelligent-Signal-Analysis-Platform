# M9.1 Task 12F-B — Remote GPU Production Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the **remote (GPU server) half** of Task 12F so the detached AutoDL RTX 5090 worker can (a) verify its own trusted deployment context, (b) run the frozen `zoomspec_yolo26n_aug_combined_frn_v3` scientific pipeline on one SpaceNet recording, (c) serialize the result as an **Analysis Package v1** ZIP plus a `RemoteExecutionEnvelopeV1`, and (d) publish it atomically so the existing `runner` write-once verification accepts it. No local control-plane redesign, no coordinator redesign, no frontend/ORM/Algorithm Lab change, no 12F-C E2E.

**Architecture:** `RemoteProfile` remains **local** control-plane configuration. On the remote server a **`RemoteWorkerContext`** is built from fixed, validated environment assignments (no SSH/credential reference, no request-controlled paths, no deployment paths inside `RemoteExecutionBatchV1`). `runner._cli_probe` verifies server readiness without loading models. `ZoomSpecRemoteItemExecutor.execute(item, job_root)` performs the frozen scientific execution and atomic result publication. `runner.run_work` stays the lifecycle/write-once/status owner.

**Tech Stack:** Python 3.12 (AutoDL), PyTorch 2.8.0+cu128, ultralytics 8.4.114, NumPy 2.3.2, FastAPI, SQLAlchemy, Pydantic, OpenSSH/SCP. No real SSH/GPU required for the CPU/no-GPU unit phase; GPU-required acceptance is explicitly gated and must only run on a card-mode server.

**Spec:** `docs/superpowers/specs/2026-09-09-m9-1-task12f-remote-platform-integration-design.md` (§8.2, §9, §10, §14)

**Base:** `feature/m9-1-live-remote-gpu-inference @ 34d854411c721bfb0459e6b1aa0e149e7ceaf2de`

---

## Scope And Boundaries

**In scope (12F-B only):**
- Trusted `RemoteWorkerContext` (remote server execution context; no SSH credentials).
- Fixed validated deployment-path env bridge from `RemoteProfile`/`SshRunner` (worker env contract frozen below).
- Production `runner._cli_probe`.
- Production `ZoomSpecRemoteItemExecutor` (focused module).
- `PipelineOutput` → Analysis Package v1 ZIP + `RemoteExecutionEnvelopeV1`; atomic/write-once publication.
- `runner work` lazy wiring.
- CPU/no-GPU unit tests plus a clearly separated GPU-required acceptance gate.

**Out of scope (12F-A kept stable; 12F-C later):**
- No control-plane redesign; no coordinator redesign unless fresh evidence reveals a blocking interface mismatch.
- No local API E2E / Algorithm Lab flow (12F-C).
- No frontend, no new ORM, no multi-GPU scheduling.
- No Task12A-E scientific-semantic changes (LS-STFT/CPN/AHLP/FRN/postprocess unchanged).
- `runner.py` GPU-library import must remain lazy; importing runner.py stays GPU-free.

---

## Frozen Worker Environment Contract (authoritative)

`RemoteWorkerContext` is constructed on the remote server from **exact fixed scalar environment variable names only**, expanded by `SshRunner` into the remote command argv. **No JSON mapping crosses the remote SSH command.** No dynamic env names. No host/user/ssh_key/known_hosts in worker env.

`RemoteProfile` keeps its local JSON mappings (`dataset_roots`, `asset_paths`) as deployment configuration, but `SshRunner` expands only these trusted scalar `RemoteProfile` values into fixed scalar env names:

| Env var (fixed scalar) | Trusted value | Source (local, validated) |
|---|---|---|
| `WSP_REMOTE_REPO_ROOT` | remote repo root (POSIX) | `RemoteProfile.remote_repo_root` |
| `WSP_REMOTE_JOB_ROOT` | remote job root (POSIX) | `RemoteProfile.remote_job_root` |
| `WSP_REMOTE_SPACENET_ROOT` | SpaceNet dataset root (POSIX) | `RemoteProfile.dataset_roots["SpaceNet"]` (must exist) |
| `WSP_REMOTE_DETECTOR_CHECKPOINT` | detector checkpoint (POSIX) | `RemoteProfile.asset_paths["detector_checkpoint"]` |
| `WSP_REMOTE_FRN_CHECKPOINT` | FRN checkpoint (POSIX) | `RemoteProfile.asset_paths["frn_checkpoint"]` |
| `WSP_REMOTE_FROZEN_CONFIG` | frozen config (POSIX) | `RemoteProfile.asset_paths["frozen_config"]` |
| `WSP_REMOTE_LS_STFT_NORMALIZATION` | LS-STFT normalization (POSIX) | `RemoteProfile.asset_paths["ls_stft_normalization"]` |
| `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT` | 40-hex required remote runtime commit | `RemoteProfile.required_remote_runtime_commit` |
| `PYTHONPATH` | `<remote_repo_root>/backend` | derived from `remote_repo_root` |

All values are already validated safe absolute POSIX paths by `RemoteProfile`. `SshRunner` fails closed **before any SSH invocation** if `dataset_roots["SpaceNet"]` or any of the four required asset logical mappings is missing/unsafe.

**Repo-owned paths derived remotely from `WSP_REMOTE_REPO_ROOT` (no extra deployment config):**
- label-space root = `<remote_repo_root>/label_spaces`
- asset-manifest path = `<remote_repo_root>/backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json`

`RemoteWorkerContext` fields:
```python
@dataclass(frozen=True)
class RemoteWorkerContext:
    repo_root: Path            # WSP_REMOTE_REPO_ROOT
    job_root: Path             # WSP_REMOTE_JOB_ROOT
    required_runtime_commit: str  # WSP_REMOTE_REQUIRED_RUNTIME_COMMIT (40-hex)
    dataset_root_space_net: Path  # WSP_REMOTE_SPACENET_ROOT
    detector_checkpoint: Path     # WSP_REMOTE_DETECTOR_CHECKPOINT
    frn_checkpoint: Path          # WSP_REMOTE_FRN_CHECKPOINT
    frozen_config_path: Path      # WSP_REMOTE_FROZEN_CONFIG
    ls_stft_normalization_path: Path  # WSP_REMOTE_LS_STFT_NORMALIZATION
    label_space_root: Path     # derived repo-owned path
    asset_manifest_path: Path  # derived repo-owned path
```
- **No** `host`, `user`, `port`, `ssh_key_path`, `known_hosts_path`, or any credential reference.
- **No** `WSP_REMOTE_DATASET_ROOTS_JSON` / `WSP_REMOTE_ASSET_PATHS_JSON` env names exist in the worker contract.
- Parsing uses the same safe-POSIX validator as `RemoteProfile` (`is_safe_remote_posix_path_text`) and fail-closes on any missing/unsafe field.

`RemoteExecutionBatchV1` / `RemoteExecutionItemV1` never carry deployment paths (existing wire schema already only carries logical identity + hashes + `remote_runtime_commit` + `asset_manifest_sha256`).

**Transport ownership (LOCKED):** Task 1 modifies `backend/app/remote_execution/transport.py` (scalar env expansion in `run_runner`) and `backend/tests/test_remote_transport.py` in addition to `worker_context.py`. `SshRunner` owns the fixed scalar env bridge; the same env assignments are present on `submit`, `probe` and `work` invocations; the detached `runner work` subprocess inherits the submit environment through existing `Popen` inheritance.

---

# TASK 1 — RemoteWorkerContext + Scalar Env Bridge (transport + worker_context)

**First: read before editing**
- `backend/app/remote_execution/profile.py` (`RemoteProfile`, `is_safe_remote_posix_path_text`, `_safe_posix_root`, `_safe_posix_mapping`).
- `backend/app/remote_execution/transport.py` (`SshRunner.run_runner` argv construction — currently sets `PYTHONPATH`, `WSP_REMOTE_JOB_ROOT`, `remote_python_path -m ...`; must be extended with the fixed scalar worker env names above).
- `backend/tests/test_remote_transport.py` (existing transport tests).

**Interfaces:**
- `backend/app/remote_execution/worker_context.py` (new):
```python
@dataclass(frozen=True)
class RemoteWorkerContext:
    repo_root: Path
    job_root: Path
    required_runtime_commit: str
    dataset_root_space_net: Path
    detector_checkpoint: Path
    frn_checkpoint: Path
    frozen_config_path: Path
    ls_stft_normalization_path: Path
    label_space_root: Path
    asset_manifest_path: Path

    @classmethod
    def from_env(cls, env=None) -> "RemoteWorkerContext": ...
        # reads the fixed scalar env var names above; fail closed on missing/unsafe.

def required_worker_env_vars() -> tuple[str, ...]: ...   # returns the exact env var names

def is_complete_worker_env(env) -> bool: ...             # True iff all required env present
```

`from_env` derives repo-owned `label_space_root` and `asset_manifest_path` from `repo_root`; validates every POSIX path with the shared safe-path validator; validates `required_runtime_commit` is 40-hex; fail-closes (raises `PlatformError("REMOTE_WORKER_CONTEXT_INVALID", ...)`) on any missing/unsafe scalar field.

**SshRunner scalar env bridge (Task 1 also modifies transport.py):**
`SshRunner.run_runner` expands only the trusted scalar `RemoteProfile` values into the remote `env` prefix:
- `dataset_roots["SpaceNet"]` → `WSP_REMOTE_SPACENET_ROOT` (fail closed before SSH if missing);
- `asset_paths["detector_checkpoint"|"frn_checkpoint"|"frozen_config"|"ls_stft_normalization"]` → `WSP_REMOTE_DETECTOR_CHECKPOINT` / `WSP_REMOTE_FRN_CHECKPOINT` / `WSP_REMOTE_FROZEN_CONFIG` / `WSP_REMOTE_LS_STFT_NORMALIZATION` (fail closed before SSH if missing/unsafe);
- plus existing `WSP_REMOTE_REPO_ROOT`, `WSP_REMOTE_JOB_ROOT`, `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT`, `PYTHONPATH=<repo>/backend`.

The same env assignments appear on `submit`, `probe` and `work`; the detached `runner work` subprocess inherits the submit environment through the existing `Popen` inheritance.

**RED test (`backend/tests/test_remote_worker_context.py`, new) + transport tests (`backend/tests/test_remote_transport.py`, extend):**
- `test_context_from_env_valid_full`: a complete valid scalar env produces a `RemoteWorkerContext` with every field set; `asset_manifest_path` and `label_space_root` are derived from repo_root.
- `test_context_contains_no_ssh_credential_fields`: the dataclass has no host/user/port/ssh_key/known_hosts field and no credential value anywhere.
- `test_context_missing_spacenet_root_fails_closed`: `WSP_REMOTE_SPACENET_ROOT` absent → PlatformError.
- `test_context_missing_asset_scalar_fails_closed`: any of the four asset scalars absent → PlatformError.
- `test_context_unsafe_path_rejected`: path with `..`/shell chars → PlatformError.
- `test_context_invalid_runtime_commit_rejected`: non-40-hex runtime commit → PlatformError.
- `test_runner_argv_contains_exact_fixed_worker_env_assignments` (transport): the remote command argv contains exactly the fixed scalar env assignments (`WSP_REMOTE_REPO_ROOT=`, `WSP_REMOTE_JOB_ROOT=`, `WSP_REMOTE_SPACENET_ROOT=`, `WSP_REMOTE_DETECTOR_CHECKPOINT=`, `WSP_REMOTE_FRN_CHECKPOINT=`, `WSP_REMOTE_FROZEN_CONFIG=`, `WSP_REMOTE_LS_STFT_NORMALIZATION=`, `WSP_REMOTE_REQUIRED_RUNTIME_COMMIT=`, `PYTHONPATH=`).
- `test_worker_env_values_round_trip` (transport): every scalar value in the argv equals the corresponding validated `RemoteProfile` value.
- `test_missing_spacenet_mapping_fails_before_ssh` (transport): `dataset_roots` without `"SpaceNet"` → PlatformError raised before any SSH invocation (no subprocess call).
- `test_missing_asset_logical_mapping_fails_before_ssh` (transport): asset map missing a required logical name → PlatformError before SSH.
- `test_no_credential_field_in_remote_env` (transport): no `host`/`user`/`ssh_key`/`known_hosts` value appears in the remote env/argv.
- `test_no_json_mapping_in_remote_command` (transport): neither `WSP_REMOTE_DATASET_ROOTS_JSON` nor `WSP_REMOTE_ASSET_PATHS_JSON` appears anywhere in the remote command.

**Expected failure (RED):** `worker_context.py` does not exist; `SshRunner.run_runner` does not yet expand the scalar worker env.

**Minimal implementation:** add `worker_context.py` (scalar `from_env` + `required_worker_env_vars` + `is_complete_worker_env`); extend `SshRunner.run_runner` with the fixed scalar env expansion (fail-closed pre-SSH validation of required dataset/asset logical mappings).

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_worker_context.py backend/tests/test_remote_transport.py -v
```

**Commit checkpoint:** `feat: add scalar worker env bridge and remote worker context`

---

# TASK 2 — Production Probe (`runner._cli_probe`)

**First: read before editing**
- `backend/app/remote_execution/runner.py` (`_cli_probe` current placeholder at lines ~589-603; `main` error line `CODE: message` to stderr).
- `backend/app/remote_execution/worker_context.py` (Task 1).
- `backend/app/remote_execution/assets.py` (`load_pipeline_asset_manifest`, `verify_assets`, `verify_remote_runtime_commit`, `verify_asset_manifest`).
- `backend/app/labels/service.py` (`LabelSpaceService.get`).
- `backend/app/remote_execution/schema.py` (`RemoteProbeResponseV1`).

**Frozen probe stdout contract:** exact `RemoteProbeResponseV1` JSON:
```json
{"schema_version": 1, "status": "available",
 "remote_runtime_commit": "<observed 40-hex>",
 "asset_manifest_sha256": "<validated 64-hex>",
 "device": 0}
```

**Probe responsibilities (fail closed, lazy GPU import):**
1. `RemoteWorkerContext.from_env()` parses safely.
2. `verify_remote_runtime_commit(repo_root, required_runtime_commit)` — observed repo HEAD equals required runtime commit.
3. `load_pipeline_asset_manifest(asset_manifest_path)` — self-hash valid.
4. All four asset files exist and their SHA256 match `verify_assets(manifest, asset_paths)` (using worker context paths).
5. SpaceNet root exists/is a directory.
6. `spacenet_14` loadable via `LabelSpaceService(label_space_root).get("spacenet_14")`.
7. CUDA available (`torch.cuda.is_available()`) and device 0 usable (`torch.cuda.get_device_name(0)` succeeds) — import torch lazily inside the handler.

**Probe MUST NOT:** construct `ZoomSpecFrozenPipeline`; load YOLO or FRN checkpoint; perform inference.

**Implementation (modify `backend/app/remote_execution/runner.py`):** replace `_cli_probe` placeholder body with lazy imports of a production helper (prefer a dedicated helper module so probe logic is unit-testable without argparse): `backend/app/remote_execution/probe.py` (new) exposing:
```python
def run_probe(worker: RemoteWorkerContext, *, torch_import=None) -> RemoteProbeResponseV1: ...
```
`runner._cli_probe` builds the context from env, calls `run_probe`, prints `response.model_dump_json()`, returns 0. Controlled `PlatformError` propagates to `main` (single `CODE: message` stderr line). Do not move torch/ultralytics imports to runner module top level.

**RED test (`backend/tests/test_remote_probe.py`, new):** inject fake assets/labels/CUDA via monkeypatch or dependency-injection seams (no GPU).
- `test_probe_success_returns_exact_response`: fake everything valid → `run_probe` returns `RemoteProbeResponseV1` with `status="available"`, matching 40-hex runtime commit and 64-hex manifest, `device=0`.
- `test_probe_context_invalid_fails_closed`.
- `test_probe_runtime_commit_mismatch_fails`: fake repo HEAD != required → PlatformError.
- `test_probe_asset_manifest_self_hash_mismatch_fails`.
- `test_probe_asset_byte_mismatch_fails`: one asset file with wrong bytes → PlatformError.
- `test_probe_missing_dataset_root_fails`.
- `test_probe_label_space_unavailable_fails`.
- `test_probe_cuda_unavailable_fails`: fake `torch.cuda.is_available()==False` → PlatformError.
- `test_probe_device_zero_missing_fails`.
- `test_probe_does_not_instantiate_or_load_models`: assert no `ZoomSpecFrozenPipeline`/`CPNDetector`/`FRNRefiner`/`YOLO` is constructed during `run_probe` (spy on imports/construction).
- `test_runner_module_import_does_not_import_torch_or_ultralytics`: fresh subprocess import `app.remote_execution.runner`; assert `'torch' not in sys.modules` and `'ultralytics' not in sys.modules`.

**Expected failure (RED):** `_cli_probe` still raises `REMOTE_PROBE_UNAVAILABLE`; `probe.py` missing.

**Minimal implementation:** add `probe.py` `run_probe`; wire `_cli_probe`.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_probe.py -v
```

**Commit checkpoint:** `feat: implement production remote runner probe`

---

# TASK 3 — Analysis Package v1 Publisher

**First: read before editing**
- `backend/app/imported_runs/schema.py` (`Manifest`, `PipelineMetadata`, `RecordingMetadata`, `ExecutionMetadata`, `ResultPaths`, `PackageDetection`).
- `backend/app/imported_runs/archive.py` (`invalid`, `read_json`, `safe_path`).
- `backend/app/imported_runs/validation.py` (`validate_extracted_package`, `ValidatedAnalysisPackage`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py` (`ZOOMSPEC_FROZEN_DEFINITION`).
- `backend/app/pipelines/base.py` (`PipelineOutput`, `DetectionPayload`).

**Decision (LOCKED):** reuse Analysis Package v1 exactly; no second result schema.

**Interfaces (file to create):** `backend/app/remote_execution/package_publisher.py` (new):
```python
def manifest_for(
    *,
    pipeline_definition,
    label_space: str,          # "spacenet_14"
    recording_name: str,
    dataset_name: str,
    device: int,               # 0
) -> Manifest: ...

def build_analysis_package_zip(
    output: PipelineOutput,
    recording_name: str,
    dataset_name: str,
    workspace: Path,
) -> Path:
    """Serialize PipelineOutput -> detections.json + manifest.json inside a ZIP,
    returns the zip path."""
```

Manifest fields (frozen; no ambiguity):
- `pipeline`: id/name/version from `ZOOMSPEC_FROZEN_DEFINITION`.
- `label_space`: `spacenet_14`.
- `recording`: `{name: recording.name, dataset: recording.dataset_name}`.
- `execution`: `{executor: "remote_gpu", device: "cuda:0", environment: None}` (exact values; no alternatives).
- `results.detections`: `detections.json`.
- `parameters`: `{}`.

Every `PipelineOutput.detection` (a `DetectionPayload`) maps 1:1 to a `PackageDetection` preserving `t_start_s/t_end_s/f_low_hz/f_high_hz/class_id/class_name/confidence/scores`. Result ZIP must be accepted by the existing `validate_extracted_package`/`result_ingestor` path (root `manifest.json` + referenced `detections.json`).

**RED test (`backend/tests/test_remote_package_publisher.py`, new):** no GPU.
- `test_manifest_for_matches_frozen_definition`: pipeline id/name/version == `ZOOMSPEC_FROZEN_DEFINITION`; label_space spacenet_14; executor remote_gpu; results.detections == detections.json; parameters == {}.
- `test_manifest_execution_metadata_exact`: `execution.executor == "remote_gpu"`, `execution.device == "cuda:0"`, `execution.environment is None`.
- `test_package_zip_roundtrips_through_validate_extracted_package`: build a small `PipelineOutput` with 1-2 `DetectionPayload`, serialize via `build_analysis_package_zip`, extract with `archive.extract_package`, validate with `validate_extracted_package(root, recording_model, labels)` → the detections match 1:1 (physical seconds / Hz / class / confidence / scores preserved).
- `test_package_detection_mapping_preserves_all_fields`: compare each output DetectionPayload to the parsed PackageDetection.
- `test_empty_output_produces_empty_detections_array`.

**Expected failure (RED):** `package_publisher.py` missing.

**Minimal implementation:** add `package_publisher.py` `manifest_for` + `build_analysis_package_zip`.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_package_publisher.py -v
```

**Commit checkpoint:** `feat: publish analysis package v1 result zip`

---

# TASK 4 — Atomic Envelope + ZIP Publication (Write-Once)

**First: read before editing**
- `backend/app/remote_execution/runner.py` (`_verify_terminal_result`, `run_work` — expects `<job_root>/results/<item_key>/{envelope.json,analysis_result.zip}` and verifies envelope identity vs the frozen batch + `payload_sha256`).
- `backend/app/remote_execution/schema.py` (`RemoteExecutionEnvelopeV1`).
- `backend/app/remote_execution/result_ingestor.py` (`parse_remote_execution_envelope_json`).
- `backend/app/remote_execution/source_hash.py` (`compute_file_sha256`).

**Interfaces (file to create):** `backend/app/remote_execution/result_publisher.py` (new):
```python
def publish_result(
    *,
    job_root: Path,
    item: RemoteExecutionItemV1,
    batch: RemoteExecutionBatchV1,
    zip_path: Path,
    remote_runtime_commit: str,
    asset_manifest_sha256: str,
    hardware: dict,
    workspace: Path,
) -> None:
    """Atomically expose BOTH terminal files as one result directory:
    <job_root>/results/<item_key>/{envelope.json,analysis_result.zip}."""
```

**Atomic result-pair design (V1, LOCKED):**
- Final directory: `<job_root>/results/<item_key>`.
- Create a unique staging directory under `<job_root>/results` on the SAME filesystem.
- Write `analysis_result.zip` and `envelope.json` inside staging.
- fsync both files; fsync the staging directory where supported.
- Require the final result directory does **not** already exist.
- Atomically `os.replace`/`rename` staging → final item directory.
- fsync the parent `<job_root>/results` directory where supported.
- Never overwrite an existing final result directory (a second publication cannot replace it).
- `payload_sha256 = compute_file_sha256(zip_path)` over the exact `analysis_result.zip` bytes.
- Envelope identity fields exactly match the frozen batch/item: batch_id, item_key, request_id, local_run_id, recording_fingerprint, source_data_sha256, pipeline id/version, orchestrator_commit, remote_runtime_commit, asset_manifest_sha256.
- `hardware`/runtime metadata from the actual remote runtime (see Task 5 runtime_info_provider).
- Crash before directory rename → no terminal result visible. Crash after rename → both terminal files visible. The existing runner crash-window verification remains authoritative.

**RED test (`backend/tests/test_remote_result_publisher.py`, new):** no GPU.
- `test_publish_creates_valid_terminal_artifacts`: after `publish_result`, `<job_root>/results/<item_key>/` contains both files; `envelope.json` parses and `analysis_result.zip` exists; `_verify_terminal_result(batch, item, job_root)` passes (write-once verifier accepts the final directory).
- `test_publish_envelope_identity_matches_frozen_batch`.
- `test_publish_payload_sha256_is_exact_zip_hash`.
- `test_no_observer_sees_only_one_terminal_file`: after successful publish both files are visible simultaneously (directory-level atomicity — an observer never sees exactly one terminal file).
- `test_pre_rename_failure_leaves_final_directory_absent`: injected failure before the directory rename → final result directory absent (no terminal artifact visible).
- `test_publish_refuses_to_overwrite_terminal_result`: a second publication cannot replace the final directory (existing envelope/zip unchanged or a write-once error is raised).
- `test_publish_is_atomic_no_partial_files_on_failure`: simulate failure; no partial terminal directory/files remain.

**Expected failure (RED):** `result_publisher.py` missing.

**Minimal implementation:** add `result_publisher.py` `publish_result` with staging-directory write → fsync → atomic directory rename (same filesystem) → parent fsync, plus write-once refusal.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_result_publisher.py -v
```

**Commit checkpoint:** `feat: atomic write-once remote result publication`

---

# TASK 5 — `ZoomSpecRemoteItemExecutor`

**First: read before editing**
- `backend/app/remote_execution/runner.py` (`ItemExecutor` protocol at line ~49: `execute(self, item, job_root)`; `run_work` calls `item_executor.execute(item, job_root)`).
- `backend/app/remote_execution/worker_context.py` (Task 1).
- `backend/app/remote_execution/resolver.py` (`resolve_space_net(dataset_root, split, key, label_space, expected_fingerprint, expected_source_hash, label_space_root)`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/pipeline.py` (`ZoomSpecFrozenPipeline.__init__(detector_checkpoint_path, frn_checkpoint_path, normalization, label_space, device)`).
- `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/preprocessing.py` (`LSSTFTNormalization`; normalization values come from the `ls_stft_normalization.json` asset, NOT copied literal).
- `backend/app/labels/service.py` (`LabelSpaceService.get`).
- `backend/app/remote_execution/assets.py` (`verify_asset_manifest`).

**Interfaces (file to create):** `backend/app/remote_execution/zoomspec_executor.py` (new):
```python
def default_runtime_info_provider() -> dict:
    """Production provider. Lazily imports torch INSIDE execute and returns the
    actual remote runtime metadata:
      device_index = 0
      device_type = "cuda"
      device_name = torch.cuda.get_device_name(0)
      torch_version = str(torch.__version__)
      cuda_version = str(torch.version.cuda)
    """

class ZoomSpecRemoteItemExecutor:
    """Implements the existing ItemExecutor protocol (execute(item, job_root)).
    Batch-wide immutable fields are injected in the constructor by _cli_work after
    loading the frozen batch. Constructed only on the remote work path."""
    def __init__(
        self,
        *,
        batch: RemoteExecutionBatchV1,
        worker: RemoteWorkerContext,
        pipeline_factory=None,             # injectable seam for CPU tests
        runtime_info_provider=None,        # injectable seam; default = default_runtime_info_provider
    ) -> None:
        self._batch = batch
        self._worker = worker
        self._pipeline_factory = pipeline_factory  # default builds ZoomSpecFrozenPipeline
        self._runtime_info_provider = runtime_info_provider or default_runtime_info_provider

    def execute(self, item: RemoteExecutionItemV1, job_root: Path) -> None: ...
```

**Pipeline identity rule (LOCKED):** pipeline identity comes from **`batch.pipeline`**, NOT `item.recording`. The executor requires:
- `batch.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id`
- `batch.pipeline.version == ZOOMSPEC_FROZEN_DEFINITION.version`

`item.recording` is used only for dataset/split/key/label-space and the double identity (fingerprint + source hash).

`execute` sequence:
1. Re-verify required runtime commit + complete asset manifest/assets (`verify_asset_manifest` with the worker context paths; repo-owned asset-manifest path + runtime commit). `remote_runtime_commit` may only be asserted as `worker.required_runtime_commit` AFTER this proves the deployed HEAD equals the required commit.
2. Require `batch.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id` and `batch.pipeline.version == ZOOMSPEC_FROZEN_DEFINITION.version`.
3. Require `item.parameters == {}`.
4. Resolve SpaceNet logical identity via `resolve_space_net(worker.dataset_root_space_net, dataset_split, dataset_key, label_space, expected_recording_fingerprint, expected_source_data_sha256, worker.label_space_root)` (dataset/split/key/label_space from `item.recording`).
5. `resolve_space_net` verifies the recording fingerprint and the exact source-data SHA (double-identity enforcement; GroundTruth participates only in fingerprint validation and never reaches inference).
6. Load `spacenet_14` label space.
7. Load LS-STFT normalization from the verified `ls_stft_normalization` asset file (values parsed from JSON; never a copied literal).
8. Construct `ZoomSpecFrozenPipeline(detector_checkpoint_path, frn_checkpoint_path, normalization, label_space, device=0)` (lazy/within execute; `pipeline_factory` seam for tests).
9. `remote_started_at = utc now`; `output = pipeline.run(recording_input, {}, workspace)`; `remote_finished_at = utc now` (UTC timestamps produced around actual item execution).
10. `runtime_info = self._runtime_info_provider()` (production provider lazily imports torch here; CPU tests inject a fake provider so importing `zoomspec_executor.py` remains torch-free).
11. `build_analysis_package_zip(output, recording_name, dataset_name, workspace)` — Analysis Package `execution.device == "cuda:0"`, `environment = None`.
12. `publish_result(...)` atomically with `hardware = runtime_info`, `remote_started_at`, `remote_finished_at`.

`frozen_config` remains a **required verified asset identity** even though `ZoomSpecFrozenPipeline` does not consume it directly; it is verified in step 1 and no new scientific use is invented.

**RED test (`backend/tests/test_zoomspec_remote_executor.py`, new):** CPU/no-GPU, via injected fake pipeline factory + faked assets/resolver/runtime-info provider where appropriate.
- `test_execute_requires_batch_pipeline_id_match`: `batch.pipeline.id != ZOOMSPEC_FROZEN_DEFINITION.id` → PlatformError.
- `test_execute_requires_batch_pipeline_version_match`: `batch.pipeline.version != ZOOMSPEC_FROZEN_DEFINITION.version` → PlatformError.
- `test_execute_ignores_item_recording_for_pipeline_identity`: item with a different id/version context still executes when `batch.pipeline` matches (proving identity is batch-owned).
- `test_execute_rejects_non_empty_parameters`: item.parameters != {} → PlatformError.
- `test_execute_verifies_runtime_commit_and_assets_fail_closed`: mismatch → PlatformError before pipeline construction.
- `test_execute_calls_pipeline_with_empty_params_and_device_zero`: fake pipeline records `(recording_input, {}, workspace)` and `device == 0`.
- `test_execute_verifies_recording_fingerprint_and_source_hash` (resolver double-identity enforced via fake resolver).
- `test_execute_ground_truth_never_reaches_inference`: fake pipeline asserts no GT passed (only RecordingInput without GT).
- `test_execute_hardware_comes_from_runtime_info_provider`: injected fake provider returns a dict; the published envelope `hardware` equals it exactly.
- `test_execute_records_remote_started_and_finished_at`: both UTC timestamps are produced around actual pipeline execution.
- `test_execute_publishes_terminal_envelope_and_zip` accepted by `run_work`/`_verify_terminal_result` (reuse result_publisher test fixture).
- `test_executor_module_does_not_import_torch_or_ultralytics_at_import`: fresh subprocess import `app.remote_execution.zoomspec_executor`; assert no torch/ultralytics in sys.modules (the production runtime-info provider imports torch lazily inside `execute`).

**Expected failure (RED):** `zoomspec_executor.py` missing.

**Minimal implementation:** add `ZoomSpecRemoteItemExecutor`.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_zoomspec_remote_executor.py -v
```

**Commit checkpoint:** `feat: add production zoomspec remote item executor`

---

# TASK 6 — Runner Work Lazy Wiring

**First: read before editing**
- `backend/app/remote_execution/runner.py` (`_cli_work` placeholder at lines ~640-660; `run_work(batch_id, job_root, item_executor)`).
- `backend/app/remote_execution/worker_context.py` (Task 1).
- `backend/app/remote_execution/zoomspec_executor.py` (Task 5).
- `backend/app/remote_execution/job_manager.py` (runner submit/status env construction).
- `backend/app/remote_execution/transport.py` (`SshRunner.run_runner` argv).

**Interfaces (modify `backend/app/remote_execution/runner.py`):** replace `_cli_work` placeholder with production lazy wiring:
```python
def _cli_work(args: argparse.Namespace) -> int:
    # lazy imports inside handler: worker context + ZoomSpecRemoteItemExecutor
    # load the frozen batch from job_root/request.json (validate request_sha256)
    worker = RemoteWorkerContext.from_env()          # fail closed if env missing
    executor = ZoomSpecRemoteItemExecutor(batch=batch, worker=worker)
    run_work(batch.batch_id, job_root, executor)
    return 0
```
`runner.run_work` remains the lifecycle/write-once/status owner; `ZoomSpecRemoteItemExecutor` owns scientific execution + result creation. `runner` module import stays GPU-free (imports stay lazy inside `_cli_work`).

**RED test (`backend/tests/test_remote_runner_work_wiring.py`, new):** no GPU.
- `test_cli_work_wires_production_executor`: call the wiring function (or its helper) with a fake batch/job_root; assert `run_work` is invoked with a `ZoomSpecRemoteItemExecutor`.
- `test_runner_work_lazy_import_does_not_import_torch_or_ultralytics`: fresh subprocess runs the wiring path with a faked executor; assert no torch/ultralytics import.
- `test_runner_module_import_still_gpu_free`: existing subprocess guard.

**Expected failure (RED):** `_cli_work` still raises `REMOTE_EXECUTOR_UNAVAILABLE`.

**Minimal implementation:** replace `_cli_work` placeholder with lazy production wiring.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_runner_work_wiring.py -v
```

**Commit checkpoint:** `feat: wire runner work to production remote executor`

---

# TASK 7 — Negative / Error-Semantics Coverage

**Goal:** lock the error semantics end to end (CPU/no-GPU, using fakes). No behavior change beyond hardening if a test exposes a real gap.

**Interfaces (files):** existing modules; add tests only unless a real defect is found.

**RED test (`backend/tests/test_remote_executor_negative.py`, new):**
- request/runtime/asset/fingerprint/source mismatch → explicit `PlatformError` codes (`REMOTE_REQUEST_INVALID`, `REMOTE_IMPLEMENTATION_MISMATCH`, `PIPELINE_ASSET_MISMATCH`, `RECORDING_FINGERPRINT_MISMATCH`, `SOURCE_DATA_HASH_MISMATCH`).
- pipeline/data/scientific execution error (fake pipeline raises) → runner item `failed` (`PIPELINE_EXECUTION_FAILED`), no traceback in status.
- terminal artifact corruption → `REMOTE_RESULT_CORRUPTED`/`interrupted` semantics.
- no traceback or arbitrary local path leaks into protocol error messages/status.

**Expected failure (RED):** only if a specific negative case currently leaks or maps incorrectly; otherwise the task is primarily adding missing tests (this is acceptable as "RED = missing coverage" only when the plan marks the assertion as the behavior under test; do not weaken tests).

**Minimal implementation:** fix only genuine error-mapping defects surfaced; otherwise add the tests and confirm they pass.

**Focused verification:**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_remote_executor_negative.py -v
```

**Commit checkpoint:** `test: lock remote executor error semantics`

---

# TASK 8 — CPU/No-GPU Regression + GPU-Required Acceptance Gate Specification

**No-GPU regression (must run on a CPU/no-card machine):**
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_remote_worker_context.py \
  backend/tests/test_remote_probe.py \
  backend/tests/test_remote_package_publisher.py \
  backend/tests/test_remote_result_publisher.py \
  backend/tests/test_zoomspec_remote_executor.py \
  backend/tests/test_remote_runner_work_wiring.py \
  backend/tests/test_remote_executor_negative.py \
  backend/tests/test_remote_runner.py backend/tests/test_remote_transport.py \
  backend/tests/test_remote_execution_schema.py backend/tests/test_remote_result_ingestor.py -v
```
And the full backend suite:
```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -v
```
Require zero failures; preserve every existing test. Static guard: runner.py module import is GPU-free; control-plane torch/ultralytics-free; runner.py's only torch/ultralytics references are inside lazy handlers.

## GPU-REQUIRED — DO NOT RUN IN NO-CARD MODE

The following steps are **GPU REQUIRED** and must only be executed after the operator switches the AutoDL server to card mode. They are NOT part of the CPU unit phase; the plan author does not run them.

**Server environment rule (LOCKED):** `RemoteProfile` remains **local control-plane only** and is never constructed on the server. The real server probe is run directly with the exact `RemoteWorkerContext` scalar env contract above and **NO SSH credential env** (`WSP_REMOTE_HOST`/`WSP_REMOTE_USER`/`WSP_REMOTE_SSH_KEY_PATH`/`WSP_REMOTE_KNOWN_HOSTS_PATH` must not be set on the server side).

1. **Real remote runner probe on AutoDL** verifying actual CUDA availability and device 0 usability:
   ```bash
   # on the AutoDL server, first verify prerequisites:
   #   - repo/backend import path is valid (PYTHONPATH=<repo_root>/backend)
   #   - ALL fixed scalar worker env values are populated
   #     (WSP_REMOTE_REPO_ROOT, WSP_REMOTE_JOB_ROOT, WSP_REMOTE_SPACENET_ROOT,
   #      WSP_REMOTE_DETECTOR_CHECKPOINT, WSP_REMOTE_FRN_CHECKPOINT,
   #      WSP_REMOTE_FROZEN_CONFIG, WSP_REMOTE_LS_STFT_NORMALIZATION,
   #      WSP_REMOTE_REQUIRED_RUNTIME_COMMIT)
   # then run with the deployed remote runtime (no Python minor version claimed):
   /root/miniconda3/bin/python -m app.remote_execution.runner probe
   ```
   Expect stdout = exact `RemoteProbeResponseV1` JSON (`status=available`, real observed `remote_runtime_commit`, validated `asset_manifest_sha256`, `device=0`), exit 0.
2. **Real `ZoomSpecRemoteItemExecutor` on one SpaceNet sample** (e.g. `test`/`0`): construct the real executor with the real worker context, `run_work` a real batch, confirm one `envelope.json` + `analysis_result.zip` produced.
3. **Actual checkpoint + normalization + frozen_config SHA verification**: the four real asset files pass `verify_assets` against the tracked manifest; `ls_stft_normalization` values parse to the verified `LSSTFTNormalization`.
4. **Real envelope + ZIP accepted by existing runner verification**: `run_work` marks the item `completed` and `_verify_terminal_result` passes.

These gates use real `detector_checkpoint`, `frn_checkpoint`, `frozen_config`, `ls_stft_normalization`, the real `spacenet_14` label space, and real CUDA device 0. They are performed by the implementer only after the operator confirms a GPU/card-mode server.

**Post-12F-B REAL SSH probe (separate integration gate):** local `SshRunner` → fixed scalar env bridge → remote `runner probe`. This is distinct from the direct server probe above and follows 12F-B.

**12F-B closure target (after the GPU gates):** real server runner probe succeeds; real worker context has no SSH credentials; real asset/runtime/dataset/label/device readiness proven; one real item produces a valid immutable envelope+ZIP; runner terminal verification accepts it. The local API user E2E / Algorithm Lab flow is 12F-C and is NOT performed here.

---

## Self-Review

- **Spec coverage:** §8.2 (RemoteWorkerContext + probe), §9 (ZoomSpecRemoteItemExecutor), §10 (device 0 normalization preserved), §11 (Analysis Package v1 reuse; envelope → hardware_info_json, provenance stays in execution metadata), §13 (security: no request-controlled path, fixed validated env bridge, fail-closed identity), §14 (12F-B acceptance). No local control-plane/coordinator redesign.
- **No JSON mapping crosses the remote SSH command:** the worker env bridge is exact fixed scalar names only (`WSP_REMOTE_SPACENET_ROOT`, `WSP_REMOTE_DETECTOR_CHECKPOINT`, `WSP_REMOTE_FRN_CHECKPOINT`, `WSP_REMOTE_FROZEN_CONFIG`, `WSP_REMOTE_LS_STFT_NORMALIZATION`, etc.); `WSP_REMOTE_DATASET_ROOTS_JSON` / `WSP_REMOTE_ASSET_PATHS_JSON` never appear in the remote command (tested).
- **Task 1 owns transport env propagation:** `SshRunner` expands the trusted scalar `RemoteProfile` values and fails closed before SSH on missing/unsafe SpaceNet/asset mappings; tests live in `test_remote_transport.py`.
- **Detached work inherits the exact worker context:** the same scalar env assignments are present on `submit`/`probe`/`work`; the detached `runner work` subprocess inherits the submit environment through existing `Popen` inheritance.
- **batch.pipeline owns pipeline identity:** executor requires `batch.pipeline.id/version == ZOOMSPEC_FROZEN_DEFINITION.id/version`; `item.recording` is dataset/split/key/label-space + double identity only (negative tests for wrong id and wrong version).
- **Package execution metadata has one exact value:** `executor="remote_gpu"`, `device="cuda:0"`, `environment=None` — no alternatives.
- **Runtime hardware has one owner + CPU injection seam:** `runtime_info_provider` (default `default_runtime_info_provider` lazily imports torch inside `execute`) produces the envelope `hardware`; CPU tests inject a fake provider; importing `zoomspec_executor.py` remains torch-free.
- **Result pair is atomically visible as a directory:** staging directory → fsync → same-filesystem directory rename → parent fsync; never a partial single-file view; second publication cannot replace the final directory; crash windows covered by existing runner verification.
- **Envelope runtime commit provenance:** `remote_runtime_commit` is asserted as `worker.required_runtime_commit` only after `verify_asset_manifest` proves deployed HEAD == required commit; `remote_started_at`/`remote_finished_at` are UTC timestamps around actual item execution.
- **GPU server never constructs RemoteProfile:** the direct server probe uses only the scalar worker env with no SSH credential env; `RemoteProfile` stays local control-plane only; the real SSH probe is a separate post-12F-B integration gate.
- **No TODO/TBD/placeholders or `or`/ellipsis ambiguity:** every task lists exact files, exact interfaces/signatures, RED test, expected failure, minimal implementation, focused verification, and commit checkpoint; no alternative values are offered anywhere.
- **No unowned interface:** `RemoteWorkerContext`, `run_probe`, `manifest_for`, `build_analysis_package_zip`, `publish_result`, `default_runtime_info_provider`, `ZoomSpecRemoteItemExecutor`, `_cli_probe`, `_cli_work` all have one owner each.
- **No duplicate result schema:** only Analysis Package v1 (`Manifest`/`PackageDetection`) reused.
- **No worker access to RemoteProfile/SSH credentials:** `RemoteWorkerContext` carries no host/user/port/ssh-key/known_hosts; only fixed validated scalar env names.
- **No request-controlled path:** all remote paths derive from `RemoteProfile`/`remote_repo_root` validated POSIX roots; `RemoteExecutionBatchV1` stays logical-identity-only.
- **No GPU import at runner module import time:** torch/ultralytics imports remain lazy inside `_cli_probe`/`_cli_work`/`ZoomSpecRemoteItemExecutor.execute`; CPU tests confirm this.
- **GPU-required gates clearly identified:** Task 8 lists each GPU-required verification so the operator can switch the server to card mode before running.
- **12F-A stability:** no control-plane/coordinator change proposed; only remote-side additions plus a lazy wiring replacement of the two runner placeholders that currently raise `REMOTE_EXECUTOR_UNAVAILABLE`/`REMOTE_PROBE_UNAVAILABLE`.
- **frozen_config:** verified as a required asset identity in every executor/probe path; no new scientific use introduced.

---

## GPU-Required Verification Checklist (for the implementer after the operator enables a GPU)

- [ ] AutoDL server confirmed in card mode (`nvidia-smi` shows a GPU; memory limit not the 2 GiB reduced container).
- [ ] Real `runner probe` returns `RemoteProbeResponseV1` `available=true`.
- [ ] Real executor produces a valid immutable envelope + ZIP on one SpaceNet sample.
- [ ] Existing `run_work`/`_verify_terminal_result` accepts the produced result as `completed`.
