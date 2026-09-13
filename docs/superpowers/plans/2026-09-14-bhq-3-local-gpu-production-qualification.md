# BHQ-3 — Local-GPU Production Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:using-git-worktrees`
> (Task 0), `superpowers:writing-plans` (this document),
> `superpowers:test-driven-development` (Tasks 2–7), and
> `superpowers:verification-before-completion` before any acceptance/certificate
> claim. If a named skill is not installed in the environment, follow the explicit
> steps in this document; they are self-contained and take precedence.

**Goal (BHQ-3):** Make `local_gpu / cuda / device_index=0 / precision=float16` a
real, fail-closed, platform-certified execution environment for
`cpn_bandwidth_tier 1.0.0 / golden` and
`zoomspec_yolo26n_aug_combined_frn_v3 1.0.0 / golden`, with **zero** change to
frozen scientific semantics, via the existing out-of-process local inference
worker path.

**Final supported path (must be proven end-to-end):**

```text
AnalysisRun
→ executor=local_gpu
→ LocalInferenceWorkerProvider
→ separate ML interpreter (/root/miniconda3/bin/python)
→ app.analysis.local_inference_worker
→ PluginHandle.load_runtime
→ real CUDA science (same frozen code accepted in BHQ-2)
→ AnalysisResultWriter
→ persisted DetectionResult
→ completed
```

No SSH/remote execution is involved in BHQ-3.

**Architecture authority:** `docs/superpowers/specs/2026-09-10-m9-2-inference-plugin-execution-framework-design.md`
(§5.6 ExecutionCapability, §5.8 RuntimeDescriptor, §5.9 ExecutionCertificate,
§8.2/§8.4 capability + `executors_supported` projection, §11.1–11.4 executors and
readiness probe, §18 certification rules).

**Sealed production baseline:** `c809eb02a5286737819136f9391d13949e8a117b`
(`feature/m9-2-implementation`, worktree `/root/autodl-tmp/WISA-m9-2-implementation`).

**Isolation branch:** `feature/bhq-local-gpu`
**Isolation worktree:** `/root/autodl-tmp/WISA-bhq-local-gpu`
(This worktree was created from `c809eb0` and currently contains only the BHQ-3
plan commit. The plan commit is documentation-only; the production diff baseline
is still exactly `c809eb0`.)

**Authoritative precision (Task 1 conclusion):** `float16` means the certified
**CUDA mixed-precision deployment mode**, not "every operation is FP16".
See Task 1.

**Immutable local-GPU runtime reference (Task 6):**
`local:autodl_primary:gpu:7b958347b5af`

**Two-stage evidence lifecycle (no circular dependency):**

```text
Tasks 1–9
  → C6  PRE-CERT QUALIFICATION EVIDENCE   (docs/superpowers/acceptance/... , pre-cert facts only)
  → C7  certificates (evidence_ref point at IDs frozen by C6)
  → Task 11 post-cert real production AnalysisRuns
  → Task 12 full regression
  → Task 13 scope audit
  → C8  FINAL BHQ-3 ACCEPTANCE EVIDENCE    (finalizes/updates the same document)
```

The final BHQ-3 seal is based on **C8**, not C6. C6 must contain only facts that
already exist before certificate issuance (it must never reference post-cert runs).

**Certificate `evidence_ref`s (stable identifiers frozen by C6):**
`m9_2_bhq3_cpn_local_gpu_acceptance`,
`m9_2_bhq3_zoomspec_local_gpu_acceptance`

---

## Global Constraints (must hold for every task)

1. Phase G sealed semantics unchanged.
2. M9.2 Plugin / ModelRelease / Executor separation preserved.
3. Control-plane `.venv` remains torch/ultralytics-free.
4. Heavy ML imports stay only in the separate inference worker.
5. AssetManifest V1 hashes unchanged; no asset/model byte modified.
6. No scientific threshold/constant/numeric change (LS-STFT, CPN, AHLP, FRN,
   postprocess).
7. No DB schema or migration change.
8. No AnalysisRun lifecycle change.
9. No DatasetExperiment behavior change.
10. No remote transport/coordinator/recovery change.
11. No frontend change.
12. No automatic executor selection/substitution.
13. No certificate before hardware evidence.
14. Never modify the existing certificates (CPN `local_cpu`, ZoomSpec
    `remote_gpu`, dummy `local_cpu`, stft `local_cpu`).
15. The historical `remote_gpu` certificate is immutable in BHQ-3.
16. BHQ-3 is `local_gpu` only.
17. `executors_supported` is derived only from
    `technical capability ∩ registered provider ∩ exact certificate` — never from
    `cpu_supported` or raw plugin declaration.
18. One real ML case at a time (never concurrent); each real case in its own
    subprocess.
19. **Cgroup memory gate (superseded by Amendment A1):** before EVERY real model
    case in Task 8 and Task 11, require `headroom = memory.max - memory.current
    >= 4 GiB`; otherwise STOP `BHQ_3_BLOCKED_BY_CGROUP_MEMORY`. Never auto-kill
    OpenCode / Jupyter / TensorBoard / autopanel or unrelated processes to free
    memory. (Original raw-headroom rule retained for provenance; Task 8/Task 11
    admission now also permits the fail-closed cache-aware Path B defined in
    **Amendment A1**.)
20. **Honest resource evidence:** a controller process cannot read another
    process's torch CUDA allocator peaks. Record only what is externally
    observable (controller wall time; `/proc/<worker_pid>/status` RSS where
    observable; external `nvidia-smi` GPU process memory where observable);
    otherwise record `unavailable`. Never invent a value and never modify
    `local_inference_worker` just to expose test metrics.
21. **Recovery scope:** BHQ-3 certifies only the normal local_gpu execution path.
    local_gpu crash/startup recovery is NOT qualified here and is deferred to
    BHQ-5 (§Task 14 final evidence must state this).

---

## Stop Conditions (fail closed; no certificate after any earlier blocker)

```text
PRECISION_CONTRACT_UNRESOLVED          Task 1 cannot reconcile local_gpu precision with accepted remote_gpu semantics without changing science.
CUDA_PROBE_FAILED                      Task 2 probe cannot detect/prove CUDA readiness in the configured interpreter.
ASSET_MISMATCH                         Task 8/9 asset manifest or bytes differ from the frozen golden manifests.
PRE_CERT_GATE_FAILED                   Task 7: provider+capability without a certificate does NOT report EXECUTION_NOT_CERTIFIED.
BHQ_3_BLOCKED_BY_CGROUP_MEMORY         Task 8/11 pre-launch gate (superseded by Amendment A1): memory.max - memory.current < 4 GiB before a real ML worker.
REAL_LOCAL_GPU_ACCEPTANCE_FAILED       Task 8: real worker run does not reach completed with persisted DetectionResults.
BHQ2_PARITY_FAILED                     Task 9: gross divergence between the committed direct-science oracle and the production local_gpu worker outputs.
PRODUCTION_PATH_FAILED                 Task 11: post-cert real AnalysisRun fails or falls back to CPU/remote.
REGRESSION_FAILED                      Task 12: any pytest failed/error.
SCOPE_VIOLATION                        Task 13: a frozen scientific / remote / DB / frontend file changed.
```

If a stop triggers: STOP immediately, do not issue/modify any certificate, record
the exact failure in the appropriate evidence stage, and end the run with
`BHQ_3_BLOCKED_BY_<reason>`.

If a stop triggers **after C7** (during Task 11, Task 12, or Task 13), the C7
certificates are candidate-only: STOP, do NOT create C8, do NOT merge
`feature/bhq-local-gpu` into the sealed production branch, and do NOT claim BHQ-3
complete (see Task 10.1).

---

## Task 0 — Isolation

**Deliverable:** isolated worktree/branch; sealed reference untouched.

```bash
cd /root/autodl-tmp/WISA-m9-2-implementation
git worktree add -b feature/bhq-local-gpu /root/autodl-tmp/WISA-bhq-local-gpu \
  c809eb02a5286737819136f9391d13949e8a117b
cd /root/autodl-tmp/WISA-bhq-local-gpu
git branch --show-current      # feature/bhq-local-gpu
git rev-parse HEAD             # c809eb0... (plus the docs-only BHQ-3 plan commit)
git status --short             # clean
```

If the worktree/branch already exists (as it does after this planning pass),
verify it instead of recreating it:

```bash
git -C /root/autodl-tmp/WISA-bhq-local-gpu merge-base HEAD c809eb02a5286737819136f9391d13949e8a117b
# must equal c809eb02a5286737819136f9391d13949e8a117b
git -C /root/autodl-tmp/WISA-m9-2-implementation rev-parse HEAD   # unchanged c809eb0
```

All BHQ-3 commands in later tasks run from `/root/autodl-tmp/WISA-bhq-local-gpu`
with `$PWD` = that worktree. The sealed worktree `/root/autodl-tmp/WISA-m9-2-implementation`
must never be edited.

---

## Task 1 — Precision Semantics Audit (must precede all capability/certificate work)

### 1.1 Source-of-truth facts (audit result, already established)

- `preprocessing.py::build_ls_stft_spectrogram` builds the spectrogram with
  `torch.float32` window/tensors and returns a **`uint8`** image
  (`LSSTFTSpectrogram.image: np.ndarray`). It is device-parameterized; on CUDA it
  runs the FFT on CUDA in FP32.
- `detector.py::_detect_raw_batch` calls `model.predict(source=<uint8 images>,
  device=..., ...)` with **no `half=True`**. Ultralytics default is FP32 compute;
  CPN does not enable half.
- `frn.py::FRNRefiner._forward_raw_batch` wraps the forward in
  `torch.autocast(device_type="cuda", dtype=torch.float16, enabled=is_cuda)` —
  FCUDA runs in **FP16 autocast**; off-CUDA autocast is disabled.
- `ahlp.py` operates on numpy `complex64/float32/float64` (CPU).
- `postprocess.py` is pure Python/numpy; no dtype.
- `RuntimeDescriptor.precision` is an **identity/token only**: grep shows it is
  never passed into any pipeline or `build_runtime` call. It participates only in
  descriptor metadata, certificate matching, `ExecutionCapability` matching, and
  the remote transport env bridge. The effective dtype is fixed by frozen code.
- The historical accepted `remote_gpu / cuda / float16` certificate therefore
  means the **certified CUDA deployment mode**, where frozen stages may be FP32
  (LS-STFT, CPN, AHLP, postprocess) and FRN intentionally uses FP16 autocast.

### 1.2 Decision

```text
precision = "float16"  ⇒  Answer B:
the certified CUDA mixed-precision execution mode, NOT "every operation is FP16".
local_gpu must reproduce the SAME frozen semantics as remote_gpu.
```

No scientific code may be changed to force full FP16.

If the executor cannot reproduce this (e.g. it must alter FRN autocast or CPN
dtype), STOP with `PRECISION_CONTRACT_UNRESOLVED`.

### 1.3 Required effective-dtype evidence (acceptance-only instrumentation)

Add committed acceptance tooling (NOT production code):

`scripts/bhq3_precision_dtype_probe.py` — run with
`/root/miniconda3/bin/python`; runs one bounded CPN case and one bounded ZoomSpec
case on `SpaceNet/test/2` and records, without altering results:

- LS-STFT: `spectrogram.image.dtype` (expect `uint8`) and the pre-quantization
  torch tensor dtype (expect `torch.float32`).
- CPN: register a `register_forward_pre_hook` on the YOLO model's first `Conv2d`
  and record the captured `input.dtype` (expect `torch.float32` on CUDA). Hooks do
  not change results.
- FRN: wrap `torch.autocast` with a recording delegate that calls the original
  and records `(device_type, dtype)` (expect `("cuda", torch.float16)` for the
  CUDA case).
- Write `/tmp/bhq3_work/precision_dtype_evidence.json`.

This script is evidence-only; it must not modify pipeline modules.

**Verification:** `scripts/bhq3_precision_dtype_probe.py` prints a JSON summary
matching the decision; otherwise STOP `PRECISION_CONTRACT_UNRESOLVED`.

---

## Task 2 — Local-GPU Provider Probe Hardening (TDD)

**Files**

- Modify: `backend/app/analysis/local_executor.py`
- Modify: `backend/tests/test_local_worker_provider.py`

### 2.1 Tests first (RED)

Add to `backend/tests/test_local_worker_provider.py` (unit, control-plane only;
monkeypatch `app.analysis.local_executor.subprocess.run`):

```text
test_local_gpu_probe_success_parses_cuda_payload
test_local_gpu_probe_torch_import_failure
test_local_gpu_probe_cuda_unavailable
test_local_gpu_probe_device_index_absent
test_local_gpu_probe_fp16_operation_failure
test_local_gpu_probe_interpreter_missing            (reuse existing missing-interpreter path)
test_local_gpu_probe_interpreter_not_executable
test_local_cpu_probe_unchanged                       (CPU still only runs interpreter health check)
test_local_executor_import_is_ml_free_subprocess     (import local_executor => "torch" not in sys.modules)
```

Each fake `CompletedProcess` supplies bounded stdout JSON for the GPU stage and a
plain success for the interpreter stage.

### 2.2 Implementation (GREEN)

In `backend/app/analysis/local_executor.py`:

- Add a module-level bounded, platform-owned script constant:

```python
_GPU_PROBE_SCRIPT = """
import json, sys
result = {"ok": False, "reason": None, "detail": {}}
try:
    import torch
except Exception:
    result["reason"] = "torch import failed"
    print(json.dumps(result)); sys.exit(0)
try:
    if not torch.cuda.is_available():
        result["reason"] = "cuda unavailable"
        print(json.dumps(result)); sys.exit(0)
    index = int(sys.argv[1])
    count = torch.cuda.device_count()
    if count <= index:
        result["reason"] = "configured cuda device index is not available"
        print(json.dumps(result)); sys.exit(0)
    props = torch.cuda.get_device_properties(index)
    x = torch.randn((64, 64), device="cuda:%d" % index, dtype=torch.float16)
    y = x @ x
    torch.cuda.synchronize()
    result["ok"] = True
    result["detail"] = {
        "device_name": props.name,
        "compute_capability": [props.major, props.minor],
        "device_count": count,
    }
except Exception as exc:
    result["reason"] = "cuda health check failed: " + type(exc).__name__
print(json.dumps(result))
"""
_GPU_PROBE_TIMEOUT_S = 120
```

- Add `self._device_index = 0` in `LocalInferenceWorkerProvider.__init__` (index 0
  is the only BHQ-3 supported index; `_LOCAL_SPECS` already yields cuda/float16).
- Split the current `probe()` into `_probe_interpreter()` (existing checks) and
  `_probe_gpu()`:

```python
def _probe_gpu(self) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            [str(self._interpreter), "-c", _GPU_PROBE_SCRIPT, str(self._device_index)],
            shell=False, capture_output=True, text=True, timeout=_GPU_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "Unable to run the configured local GPU probe."
    if result.returncode != 0:
        return False, "Configured local GPU interpreter failed its CUDA health check."
    payload = None
    for line in reversed((result.stdout or "").strip().splitlines()):
        try:
            candidate = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            payload = candidate
            break
    if payload is None:
        return False, "Configured local GPU probe returned no valid result."
    if not payload.get("ok"):
        return False, payload.get("reason") or "Configured local GPU runtime is not ready."
    return True, None

def probe(self) -> tuple[bool, str | None]:
    ok, reason = self._probe_interpreter()
    if not ok:
        return ok, reason
    if self._executor_kind == "local_gpu":
        return self._probe_gpu()
    return True, None
```

Rules: `shell=False`; fixed platform script only; no user-controlled code;
bounded diagnostics; control plane never imports torch. CPU provider behavior is
unchanged.

**Commit C1:** `feat: add local_gpu CUDA health probe to local inference provider`

---

## Task 3 — CPN `local_gpu` Technical Capability

**Files**

- Modify: `backend/app/pipelines/cpn_bandwidth_tier/definition.py`
- Modify: `backend/app/pipelines/cpn_bandwidth_tier/plugin.py`
- Modify: `backend/tests/test_cpn_bandwidth_tier_plugin.py`

### 3.1 Definition

Add exactly one capability (order: keep existing two, append new):

```python
technical_execution_capabilities=(
    ExecutionCapability("remote_gpu", "cuda", "float16"),
    ExecutionCapability("local_cpu", "cpu", "float32"),
    ExecutionCapability("local_gpu", "cuda", "float16"),
),
```

`executors_supported` and `recommended_execution` are **not** changed.

### 3.2 Runtime gate

In `plugin.py::_require_device`, add a branch before the final raise:

```python
if (executor, device_type, precision) == ("local_gpu", "cuda", "float16"):
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise PlatformError(
            "EXECUTOR_UNAVAILABLE",
            "local_gpu requires an explicit non-negative integer CUDA device index.",
        )
    return index
```

`remote_gpu` and `local_cpu` branches unchanged.

### 3.3 Tests

- Positive: `RuntimeDescriptor("local_gpu","cuda",0,"float16")` accepted and
  returns index `0`; index `3` returns `3` (mirroring the remote plumbing tests).
- Negative (must raise `EXECUTOR_UNAVAILABLE`):
  `local_gpu/cpu/0/float16`, `local_gpu/cuda/None/float16`,
  `local_gpu/cuda/-1/float16`, `local_gpu/cuda/0/float32`,
  `local_gpu/cuda/True/float16`, plus crossed descriptors
  `local_cpu/cuda/0/float16`, `remote_gpu/cpu/0/float32`.
- Capability membership: `("local_gpu","cuda","float16")` present; `remote_gpu`
  and `local_cpu` still present.

**Commit C2:** `feat: declare and gate local_gpu capability for cpn_bandwidth_tier`

---

## Task 4 — ZoomSpec `local_gpu` Technical Capability

**Files**

- Modify: `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py`
- Modify: `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/plugin.py`
- Modify: `backend/tests/test_zoomspec_plugin.py`

Mirror Task 3:

- Append `ExecutionCapability("local_gpu", "cuda", "float16")`.
- In `plugin.py::_require_device_index`, add the identical `local_gpu` branch.
- Tests: same positive/negative/crossed cases as Task 3; keep the existing
  `_REMOTE_RUNTIME_REF` and remote/local_cpu assertions unchanged.

Do not touch `preprocessing.py`, `detector.py`, `ahlp.py`, `frn.py`,
`postprocess.py`, or the shared composition.

**Commit C3:** `feat: declare and gate local_gpu capability for zoomspec pipeline`

---

## Task 5 — Legacy Metadata Boundary Regression

**Goal:** prove `executors_supported` never derives from `cpu_supported`.

- Add to `backend/tests/test_cpn_local_cpu_acceptance.py` (or a focused new test)
  an assertion that CPN `deployment_qualified_executors` is computed purely from
  `technical ∩ provider ∩ certificate`: with `local_gpu` declared but no local_gpu
  certificate and a registered local_gpu provider, `local_gpu` must NOT appear.
- Add a test that a definition with `cpu_supported=True` but no `local_cpu`
  technical capability/certificate is still not runnable (projection empty).
- Do **not** deprecate/remove `cpu_supported` (out of BHQ-3 scope).

**Commit owner:** the Task 5 tests are added to
`backend/tests/test_local_gpu_pre_cert_gate.py` and committed in **C4**
(`test: add local_gpu pre-certificate fail-closed gate`). There is no separate
Task 5 commit; C4 is the deterministic boundary for Tasks 5 and 7.

---

## Task 6 — Immutable Local-GPU Runtime Identity

**Files**

- No production file change. Recorded in the C6 evidence doc and used verbatim in
  `execution_certificates.json` (Task 10).
- Acceptance tool: `scripts/bhq3_local_gpu_runtime_identity.py` (prints/verifies).

**Execution (must inspect the ML interpreter, never the torch-free `.venv`):**

```bash
PYTHONPATH="$PWD/backend" \
/root/miniconda3/bin/python \
scripts/bhq3_local_gpu_runtime_identity.py
```

The repo `.venv` must remain torch/ultralytics-free; the identity tool must run
under `/root/miniconda3/bin/python` (it imports torch/ultralytics to compute the
material). It must print the material JSON, the 12-hex generation, and the full
`runtime_ref`, and exit non-zero if the recomputed `runtime_ref` differs from the
expected value below.

Define generation material and digest exactly as computed at plan time:

```python
material = {
    "python": "3.12.3",
    "torch": "2.8.0+cu128",
    "torch_cuda": "12.8",
    "ultralytics": "8.4.114",
    "numpy": "2.3.2",
    "scipy": "1.18.0",
    "device_name": "NVIDIA GeForce RTX 5090",
    "compute_capability": "12.0",
    "driver_version": "580.105.08",
    "cuda_available": True,
}
canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
generation = hashlib.sha256(canonical.encode()).hexdigest()[:12]   # 7b958347b5af
runtime_ref = f"local:autodl_primary:gpu:{generation}"             # local:autodl_primary:gpu:7b958347b5af
```

Rules:

- `runtime_ref` is operator/platform-owned immutable generation identity; never
  derived from request data.
- The digest is generation material, not a complete environment hash.
- Any material interpreter/dependency/GPU change requires a NEW `runtime_ref` and
  re-certification (Task 10 re-run).
- The CPU ref `local:autodl_primary:cpu:a1237f8faae7` is never reused.

**Expected runtime_ref:** `local:autodl_primary:gpu:7b958347b5af`.
If live recomputation differs at execution time, STOP and regenerate evidence +
certificates (do not silently change the digest).

---

## Task 7 — Pre-Certification Fail-Closed Gate (mandatory before Task 10)

**File:** `backend/tests/test_local_gpu_pre_cert_gate.py` (new)

Prove that a plugin declaration cannot self-certify. For BOTH plugins, with:

```text
local_gpu provider configured (build_local_providers with WSP_LOCAL_GPU_* set to the ML interpreter + runtime_ref)
+ plugin technical_execution_capabilities contains local_gpu/cuda/float16
+ NO local_gpu certificate in the production store
```

the platform must report `EXECUTION_NOT_CERTIFIED`:

```python
registry = ExecutorRegistry(providers, ExecutionCertificateStore(load_execution_certificates(CERT_PATH)))
result = registry.availability_for(definition, resolved_release, recording, "local_gpu")
assert result.available is False and result.reason_code == "EXECUTION_NOT_CERTIFIED"
supported, _ = registry.deployment_qualified_executors(definition, release_id)
assert "local_gpu" not in supported
```

Also assert `AnalysisService.create_run(executor="local_gpu", ...)` fails/returns
the uncertified error against the production store.

**STOP `PRE_CERT_GATE_FAILED`** if any assertion fails.

No certificate may be committed before this gate is green.

---

## Task 8 — Pre-Cert Real Hardware Acceptance (temporary acceptance-only certificate)

**Files**

- `scripts/bhq3_local_gpu_acceptance.py` (new, committed acceptance tooling)
- `backend/tests/test_local_gpu_acceptance.py` (new; skips when torch/CUDA/assets
  unavailable in the configured interpreter)

**Temporary certificate constraint:** the temp certificate is constructed
**in memory only** and must never be written to
`backend/app/pipelines/execution_certificates.json` before acceptance passes.

**Harness (per plugin, per stem):**

1. Build `Settings` with:
   - `database_url = sqlite:////tmp/bhq3_work/<plugin>_<stem>.db`
   - `data_root = /tmp/bhq3_work/<plugin>_<stem>/data`
   - `local_gpu_python_path = /root/miniconda3/bin/python`
   - `local_gpu_runtime_ref = local:autodl_primary:gpu:7b958347b5af`
   - `local_inference_work_root = /tmp/bhq3_work/<plugin>_<stem>/work`
   - `local_asset_paths = { "<plugin_id>/1.0.0/<manifest_sha>": { ...real golden asset paths... } }`
2. `providers = build_local_providers(settings)` → must contain `local_gpu` with
   descriptor `local_gpu / cuda / 0 / float16`.
3. Build `temp_store = ExecutionCertificateStore(production_certs + [temp local_gpu cert])`
   where the temp cert tuple is
   `(plugin_id, "1.0.0", "golden", "local_gpu", "cuda", "float16", runtime_ref)`.
4. `registry = ExecutorRegistry(providers, temp_store)`.
5. **Pre-launch cgroup memory gate (per case, immediately before launch)
   [superseded by Amendment A1]:**

   ```python
   headroom = int(Path("/sys/fs/cgroup/memory.max").read_text()) - int(Path("/sys/fs/cgroup/memory.current").read_text())
   if headroom < 4 * 1024**3:
       raise SystemExit("BHQ_3_BLOCKED_BY_CGROUP_MEMORY")
   ```

   Never auto-kill OpenCode/Jupyter/TensorBoard/autopanel or unrelated processes.
   **Amendment A1** replaces this call with `require_memory_admission()` (Path A
   raw OR guarded Path B) plus live `MemoryMonitor` abort conditions.
6. **Create provider work roots before `create_app`/availability:**

   ```python
   settings.local_inference_work_root.mkdir(parents=True, exist_ok=True)
   settings.data_root.mkdir(parents=True, exist_ok=True)
   ```

   Note: `local_inference_work_root` is provider configuration; the actual local
   worker result workspace is under the configured `data_root` /
   `StorageService` artifact path (`StorageService.artifact_dir(run.id)`).
7. `app = create_app(settings)`; then override
   `app.state.executor_registry = registry` (real production seams, temp cert
   injected at runtime).
8. Register the real SpaceNet Recording via external-path semantics (no IQ copy):
   use `app.datasets.spacenet.SpaceNetAdapter` to load `test/<stem>`, then insert
   `RecordingModel` with `external_path` + metadata + `GroundTruthModel` rows.
9. Launch via the real API path: `POST /api/analysis-runs`
   `{recording_id, pipeline_id, executor:"local_gpu", model_release_id:"golden", parameters:{}}`.
10. Poll `GET /api/analysis-runs/{id}` until terminal.
11. Assert: `status == "completed"`, `executor == "local_gpu"`, no error, no
    CPU/remote fallback; persisted `DetectionResult` rows with correct output label
    space; `execution_metadata_json.runtime_descriptor` is the exact tuple.

**Resource evidence (honest, externally observable only).** For each production
worker case capture:

- `wall_time_s`: measured by the controller around the API launch→terminal wait;
- `worker_pid`: `AnalysisRunModel.worker_pid`;
- `worker_peak_rss_kb`: controller or external observer reads
  `/proc/<worker_pid>/status` `VmHWM` **while the worker is alive**; if the worker
  has already exited or the value is unavailable, record `"unavailable"`;
- `gpu_process_memory`: external `nvidia-smi --query-compute-apps=pid,used_memory
  --format=csv` polled during the run (no new required dependency); if the run is
  too short to observe, record `"unavailable"`;
- **never** claim the controller read the child's
  `torch.cuda.max_memory_allocated/reserved` — that is not observable from the
  controller. BHQ-2 direct-science torch-allocator peaks remain the
  allocator-level reference only.

Do not modify `local_inference_worker` to expose test metrics.

**Runner:** `scripts/bhq3_local_gpu_acceptance.py` runs the same steps for
`{cpn, zoomspec} × {stem 2, stem 0}` in separate processes (one case at a time),
writes `/tmp/bhq3_work/bhq3_temp_cert_evidence.json`, and prints a summary.

**STOP `BHQ_3_BLOCKED_BY_CGROUP_MEMORY`** on the pre-launch gate, and
**STOP `REAL_LOCAL_GPU_ACCEPTANCE_FAILED`** if any case is not `completed` with
persisted detections, or if the worker did not run under
`/root/miniconda3/bin/python`.

**Commit C4:** `test: add local_gpu pre-certificate fail-closed gate`
(includes Task 5 legacy-metadata boundary tests + Task 7 gate in
`backend/tests/test_local_gpu_pre_cert_gate.py`).

**Commit C5:** `test: add real local_gpu hardware acceptance harness`
(includes `backend/tests/test_local_gpu_acceptance.py` and ALL acceptance
tooling: `scripts/bhq3_precision_dtype_probe.py`,
`scripts/bhq3_local_gpu_runtime_identity.py`,
`scripts/bhq3_direct_science_oracle.py`,
`scripts/bhq3_local_gpu_acceptance.py`,
`scripts/bhq3_local_gpu_production_acceptance.py`.)

---

## Task 9 — Reproducible Parity via the Direct-Science Oracle

Task 9 must be self-contained and reproducible even if `/tmp` (and the BHQ-2
scratch output) is gone. It compares two artifacts produced from the SAME frozen
code, stems, assets, and CUDA device:

1. the **committed direct-science oracle** (fresh, current tree), and
2. the **production `local_gpu` worker** outputs from Task 8.

### 9.1 Committed oracle tooling

`scripts/bhq3_direct_science_oracle.py` (committed in C5), run with
`/root/miniconda3/bin/python`, must rerun the SAME frozen direct-science
pipelines in fresh subprocesses (one case per process) for
`{cpn, zoomspec} × {stem 2, stem 0}` and emit full JSON to
`/tmp/bhq3_work/direct_science_oracle_<plugin>_stem<stem>.json`:

- `CPNBandwidthTierPipeline(detector_checkpoint_path, normalization, label_space, device=0)`
  on `app.datasets.spacenet.SpaceNetAdapter`-derived `RecordingInput`;
- `ZoomSpecFrozenPipeline(detector_checkpoint_path, frn_checkpoint_path, normalization,
  label_space, device=0)`;
- each detection: `t_start_s`, `t_end_s`, `f_low_hz`, `f_high_hz`, `class_id`,
  `class_name`, `confidence`, `scores`;
- `run_metadata` (CPN: `cpn_proposal_count`/`detection_count`; ZoomSpec:
  `cpn_proposal_count`, `frn_valid_count`, `score_threshold_survivor_count`,
  `post_nms_count`).

No `RuntimeDescriptor`, no `build_runtime()`, no worker, no DB. This reruns the
same frozen composition accepted in BHQ-2, but produces full committed-provenance
JSON rather than relying on the BHQ-2 `/tmp` scratch.

### 9.2 Comparison

Compare `current direct-science oracle` vs `production local_gpu worker` per
matching stem:

- physical TF boxes (`t_start_s`, `t_end_s`, `f_low_hz`, `f_high_hz`) matched by
  TF IoU;
- class ids and class names;
- confidence;
- ZoomSpec stage counts (`cpn_proposal_count`, `frn_valid_count`,
  `score_threshold_survivor_count`, `post_nms_count`), read from the real run's
  artifact workspace `run_metadata.json`
  (`StorageService.artifact_dir(run.id) / "run_metadata.json"`), not from a log.

### 9.3 Historical sanity counts (not the primary parity evidence)

```text
CPN      stem 2: 13 detections (Narrow 10 / Mid 2 / Wide 1)
CPN      stem 0: 17 detections (Narrow 9 / Mid 3 / Wide 5)
ZoomSpec stem 2: 11 detections
ZoomSpec stem 0: 13 detections
```

These BHQ-2 counts remain sanity checks. The authoritative BHQ-3 parity evidence
is the oracle-vs-worker comparison above.

Because the production worker and the oracle execute the same frozen code, a gross
divergence (count mismatch beyond NMS ties, class/label disagreement, systematic
confidence shift) is a blocker. CPU-vs-GPU bitwise equality is NOT required. No
scientific code change is permitted.

**STOP `BHQ2_PARITY_FAILED`** on gross divergence.

---

## Task 10 — Certificate Issuance (only after Tasks 7–9 pass)

**Files (C7 owns BOTH atomically — certificate store + exact-certificate
regression test only):**

```text
backend/app/pipelines/execution_certificates.json
backend/tests/test_local_gpu_certificates_exact.py
```

Append two certificates to the store exactly:

```json
{
  "plugin_id": "cpn_bandwidth_tier",
  "plugin_version": "1.0.0",
  "model_release_id": "golden",
  "executor": "local_gpu",
  "device_type": "cuda",
  "precision": "float16",
  "runtime_ref": "local:autodl_primary:gpu:7b958347b5af",
  "evidence_ref": "m9_2_bhq3_cpn_local_gpu_acceptance"
}
```

```json
{
  "plugin_id": "zoomspec_yolo26n_aug_combined_frn_v3",
  "plugin_version": "1.0.0",
  "model_release_id": "golden",
  "executor": "local_gpu",
  "device_type": "cuda",
  "precision": "float16",
  "runtime_ref": "local:autodl_primary:gpu:7b958347b5af",
  "evidence_ref": "m9_2_bhq3_zoomspec_local_gpu_acceptance"
}
```

`evidence_ref` points to the **C6 PRE-CERT QUALIFICATION EVIDENCE** document,
which must already exist before this commit.

The C7 test `backend/tests/test_local_gpu_certificates_exact.py` must assert:

- the exact new CPN `local_gpu` certificate exists;
- the exact new ZoomSpec `local_gpu` certificate exists;
- `runtime_ref` is exactly `local:autodl_primary:gpu:7b958347b5af`;
- `precision` is exactly `float16`;
- `evidence_ref`s are the C6-frozen identifiers
  (`m9_2_bhq3_cpn_local_gpu_acceptance`, `m9_2_bhq3_zoomspec_local_gpu_acceptance`);
- all four historical certificates remain byte-identical;
- no unexpected certificate tuple / widening appears (exact store membership).

**Commit C6 (pre-cert evidence):** `docs: add bhq-3 local gpu pre-cert qualification evidence`
**Commit C7 (certificates):** `feat: certify cpn and zoomspec local_gpu execution`
(certificate store + exact certificate regression test only)

Ordering invariant (no future-evidence cycle): C6 contains only facts that exist
before certificate issuance (Tasks 1–9). The `evidence_ref` identifiers are frozen
by C6. C7 only adds the two certificates plus their exact regression test. All
post-cert facts are recorded exclusively in C8 (Task 14).

### Task 10.1 — Post-C7 candidate state is fail-closed (candidate, not sealed)

C7 certificate issuance produces a **candidate certificate state pending
post-cert production verification**. If Task 11, Task 12, or Task 13 fails:

```text
STOP BHQ_3_BLOCKED_BY_<reason>
DO NOT create C8
DO NOT merge feature/bhq-local-gpu into the sealed production branch
DO NOT claim BHQ-3 complete
DO NOT claim complete local_gpu qualification
```

The branch may retain C7 temporarily for debugging, but it is **unsealed and
non-mergeable**. If the qualification is abandoned, the candidate local_gpu
certificates must be reverted/removed before any integration. Only a green
Task 11 + Task 12 + Task 13, followed by C8 and the final external Git audit,
produces a sealed BHQ-3 result.

---

## Task 11 — Post-Certification Real Production AnalysisRun

**No injected certificate.** Use the real production store and real app state.

**Driver:** `scripts/bhq3_local_gpu_production_acceptance.py` (committed in C5).

1. Build `Settings` with a dedicated DB (`/tmp/bhq3_work/postcert/...`), real
   SpaceNet external-path recording, `WSP_LOCAL_GPU_PYTHON_PATH=/root/miniconda3/bin/python`,
   `WSP_LOCAL_GPU_RUNTIME_REF=local:autodl_primary:gpu:7b958347b5af`,
   `WSP_LOCAL_INFERENCE_WORK_ROOT`, and the namespaced `WSP_LOCAL_ASSET_PATHS_JSON`
   for each golden manifest.
2. **Pre-launch cgroup memory gate** (same as Task 8 step 5; `>= 4 GiB`, else STOP
   `BHQ_3_BLOCKED_BY_CGROUP_MEMORY`) **[superseded by Amendment A1: use
   `require_memory_admission()` + live monitor]**. Never auto-kill unrelated
   processes.
3. **Create work roots before `create_app`:**

   ```python
   settings.local_inference_work_root.mkdir(parents=True, exist_ok=True)
   settings.data_root.mkdir(parents=True, exist_ok=True)
   ```
4. `app = create_app(settings)` — builds the real `ExecutorRegistry` from the
   committed certificate file + `build_local_providers`.
5. `POST /api/analysis-runs` with `executor:"local_gpu"` for CPN and ZoomSpec.
6. Require: `status == "completed"`, `executor == "local_gpu"`, separate ML
   interpreter execution, exact runtime descriptor tuple, `model_release_id == "golden"`,
   exact manifest SHA, persisted DetectionResults, no CPU/remote fallback.
7. **Executor projection (correct invariant).** `GET /api/pipelines`: require
   `local_gpu ∈ executors_supported` for CPN and ZoomSpec. For
   `recommended_executor`, assert the **M9.2 deployment-qualified projection for
   the actually configured providers/certificates** — i.e.
   `recommended_executor == definition.recommended_execution if that executor is in
   executors_supported else None`. Do **not** unconditionally require `None`.
   If the BHQ harness intentionally disables `remote_gpu` (no remote provider
   configured), then `remote_gpu` is absent from `executors_supported`, so
   `recommended_executor is None` — this must be asserted **with that condition
   stated explicitly**.
8. `GET /api/executor-availability?executor=local_gpu` returns available when the
   probe passes.

**Resource evidence:** capture the same externally observable metrics as Task 8
(controller wall time; `/proc/<worker_pid>/status` `VmHWM` while alive;
external `nvidia-smi` compute-apps memory; else `"unavailable"`). Never claim the
controller read the child's torch allocator peaks.

**Recovery limitation (must be recorded, not fixed):**

```text
BHQ-3 certifies the normal local_gpu execution path.
local_gpu crash/startup recovery is not qualified here and is deferred to BHQ-5.
```

BHQ-3 must not claim "complete local_gpu lifecycle qualification." Startup
recovery continues to handle only stale `local_cpu` runs; do not modify recovery.

**STOP `PRODUCTION_PATH_FAILED`** on any failure or fallback.

**Commit C8 (final acceptance evidence):** `docs: finalize bhq-3 local gpu acceptance evidence`
(update/finalize the C6 document with post-cert facts, final projection,
regression, scope audit, and known implementation provenance — the C7 SHA — never
C8's own SHA).

---

## Task 12 — Full Regression

Focused first:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_local_worker_provider.py \
  backend/tests/test_cpn_bandwidth_tier_plugin.py \
  backend/tests/test_zoomspec_plugin.py \
  backend/tests/test_local_gpu_pre_cert_gate.py \
  backend/tests/test_local_gpu_certificates_exact.py \
  backend/tests/test_execution_certificate.py \
  backend/tests/test_executor_registry.py \
  backend/tests/test_executor_cutover.py \
  backend/tests/test_cpn_local_cpu_acceptance.py \
  backend/tests/test_pipeline_read_model.py \
  -q
```

Then the full baseline:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Require `0 failed, 0 errors`. Import-boundary checks explicitly:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c \
  "import sys; import app.analysis.local_executor; import app.pipelines.cpn_bandwidth_tier.plugin; import app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin; assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules; print('CONTROL_PLANE_ML_FREE_OK')"
"$PWD/.venv/bin/python" -c "import importlib.util as u; assert u.find_spec('torch') is None; assert u.find_spec('ultralytics') is None; print('VENV_NO_ML_OK')"
```

**STOP `REGRESSION_FAILED`** on any failed/error.

---

## Task 13 — Scope Audit

Expected production files ONLY:

```text
backend/app/analysis/local_executor.py
backend/app/pipelines/cpn_bandwidth_tier/definition.py
backend/app/pipelines/cpn_bandwidth_tier/plugin.py
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/plugin.py
backend/app/pipelines/execution_certificates.json          (C7)
backend/tests/test_local_gpu_certificates_exact.py         (C7)
backend/tests/... (other focused tests)
scripts/bhq3_*.py (acceptance tooling, C5)
docs/superpowers/plans/2026-09-14-bhq-3-local-gpu-production-qualification.md
docs/superpowers/acceptance/2026-09-14-bhq-3-local-gpu-production-qualification.md
```

Forbidden (must be empty in the diff):

```bash
git diff --name-only c809eb02a5286737819136f9391d13949e8a117b..HEAD \
  | grep -E "preprocessing|detector|ahlp|frn|postprocess|app/db|migrations|remote_execution|dataset_experiments|app/evaluation|frontend|label_spaces|\.pt$|\.yaml$|\.json$" \
  | grep -v "execution_certificates.json" || echo "SCOPE_OK"
```

**STOP `SCOPE_VIOLATION`** if any frozen scientific / remote / DB / frontend / asset
file appears. If implementation appears to require changing a frozen scientific
file, STOP and classify instead of widening scope.

---

## Task 14 — Two-Stage Acceptance Evidence Document

**File:** `docs/superpowers/acceptance/2026-09-14-bhq-3-local-gpu-production-qualification.md`
(created in C6, finalized in C8; same path).

### 14.1 C6 — PRE-CERT QUALIFICATION EVIDENCE (Tasks 1–9 facts only)

Must contain only facts that already exist before C7 certificate issuance:

```text
GPU identity (RTX 5090, 32607 MiB, compute cap 12.0, driver 580.105.08, CUDA 12.8)
runtime_ref (local:autodl_primary:gpu:7b958347b5af) + generation material
runtime environment versions (python/torch/ultralytics/numpy/scipy)
precision-semantics conclusion (Task 1: answer B, effective dtype evidence)
asset hashes (cpn + zoomspec manifest + logical assets)
SpaceNet provenance (test stems 2/0 paths, sample metadata)
pre-cert fail-closed evidence (Task 7 + Task 5 boundary)
temporary-certificate hardware acceptance evidence (Task 8, temp cert never persisted)
direct-science-oracle-vs-worker parity (Task 9)
pre-cert latency/resource evidence (externally observable only; else "unavailable")
```

- `evidence_ref` identifiers `m9_2_bhq3_cpn_local_gpu_acceptance` and
  `m9_2_bhq3_zoomspec_local_gpu_acceptance` are defined here and then reused
  verbatim by C7.
- C6 must NOT reference post-cert runs, final projections, regression results, or
  the final accepted HEAD.

**Commit C6:** `docs: add bhq-3 local gpu pre-cert qualification evidence`

### 14.2 C8 — FINAL BHQ-3 ACCEPTANCE EVIDENCE (finalization)

A Git commit cannot reliably contain its own final SHA in its own file content.
Therefore the C8 document **MUST NOT** require
`final accepted BHQ-3 HEAD = <C8's own SHA>` inside itself. C8 records concrete,
already-known implementation provenance instead (assuming Tasks 11–13 introduce no
commits):

```text
sealed production baseline                    = c809eb02a5286737819136f9391d13949e8a117b
BHQ-3 plan SHA                                = <C0b plan commit SHA, known when C8 is written>
certificate commit SHA                        = <C7 SHA, known when C8 is written>
implementation_head_before_final_evidence     = <C7 SHA, known when C8 is written>
```

C8 then adds the post-cert facts (all known before C8 is committed):

```text
production certificate tuples (Task 10)
post-cert real AnalysisRun ids + results + DetectionResult counts (Task 11)
final executor projections (local_gpu in executors_supported; recommended_executor
  = deployment-qualified projection for configured providers)
final full regression result (0 failed / 0 errors)
final scope audit (SCOPE_OK)
recovery limitation statement:
  "BHQ-3 certifies the normal local_gpu execution path. local_gpu crash/startup
   recovery is not qualified here and is deferred to BHQ-5."
limitations (mixed precision; generation identity is material, not exhaustive)
```

Then define:

```text
C8 = final evidence commit
```

After C8 is committed, the **final external Git verification** runs (NOT part of
the C8 file content):

```bash
git rev-parse HEAD
git status --short
git diff --check
git log --oneline --decorate -n 12
```

The resulting C8 SHA is the **FINAL BHQ-3 SEAL SHA** and is reported in the
execution report / reviewer audit, NOT self-referenced inside the C8 document.

The final BHQ-3 seal is based on **C8**, not C6.

**Commit C8:** `docs: finalize bhq-3 local gpu acceptance evidence`

---

## Verification Commands (index)

```bash
cd /root/autodl-tmp/WISA-bhq-local-gpu

# preflight
git branch --show-current; git rev-parse HEAD; git status --short
nvidia-smi
cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.current

# task 2 focused
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_local_worker_provider.py -q

# tasks 3-4 focused
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_cpn_bandwidth_tier_plugin.py backend/tests/test_zoomspec_plugin.py -q

# task 7 pre-cert gate (must be green BEFORE task 10)
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_local_gpu_pre_cert_gate.py -q

# cgroup memory gate (MANDATORY before every real model case)
# [superseded by Amendment A1: use scripts/bhq3_memory_gate.py require_memory_admission()]
headroom=$(( $(cat /sys/fs/cgroup/memory.max) - $(cat /sys/fs/cgroup/memory.current) ))
[ "$headroom" -ge $((4*1024*1024*1024)) ] || { echo BHQ_3_BLOCKED_BY_CGROUP_MEMORY; exit 1; }

# task 1/6/8/9/11 acceptance tooling (operator-run, dedicated work root)
mkdir -p /tmp/bhq3_work
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_precision_dtype_probe.py
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_local_gpu_runtime_identity.py
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_direct_science_oracle.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_acceptance.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_production_acceptance.py

# task 12 full regression
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q

# task 13 scope
git diff --name-only c809eb02a5286737819136f9391d13949e8a117b..HEAD

# task 14 / C8 — FINAL external Git verification AFTER C8 is committed
# (yields the FINAL BHQ-3 SEAL SHA; never self-referenced inside the C8 document)
git rev-parse HEAD
git status --short
git diff --check
git log --oneline --decorate -n 12
```

---

## Self-Review (mandatory before committing the plan / after implementation)

1. C6/C7/C8 ordering has no future-evidence cycle: C6 = pre-cert facts only,
   C7 = certificates referencing C6 IDs, C8 = post-cert finalization; C6 never
   references post-cert facts. ✅
2. No certificate exists before C6 pre-cert evidence (Task 7 gate + C6 → C7). ✅
3. Task 9 is self-contained/reproducible via the committed
   `scripts/bhq3_direct_science_oracle.py`; it does not depend on `/tmp/bhq2_cuda`. ✅
4. Resource claims are actually observable: controller wall time +
   `/proc/<pid>/status` RSS + external `nvidia-smi`; child torch allocator peaks
   are explicitly NOT claimed; unavailable is recorded honestly. ✅
5. Cgroup memory gate is explicit (`>= 4 GiB`) and wired into Tasks 8 and 11 with
   STOP `BHQ_3_BLOCKED_BY_CGROUP_MEMORY`. ✅
6. Every acceptance script has a commit owner (all in C5); Task 5 tests fold into
   C4. ✅
7. Every task has a deterministic commit boundary (C1–C8). ✅
8. `executors_supported` invariant (`local_gpu ∈`) is asserted; `recommended_executor`
   is asserted as the deployment-qualified projection (never unconditionally
   `None`; `None` only with the remote-disabled condition stated). ✅
9. local_gpu recovery limitation recorded and explicitly deferred to BHQ-5; no
   recovery code change. ✅
10. Precision conclusion preserved (`float16` = CUDA mixed-precision deployment
    mode; no science change). ✅
11. Every M9.2 local_gpu/certificate invariant checked against the authoritative
    spec (§5.6/5.8/5.9, §8.2/8.4, §11.1–11.4, §18). ✅
12. Plan never allows plugin self-certification (Task 7 gate + Task 10
    platform-owned certificates). ✅
13. No frozen scientific code change planned (scope audit Task 13). ✅
14. No remote/Phase-G change planned. ✅
15. Sealed branch `feature/m9-2-implementation @ c809eb0` remains untouched; no
    production implementation has occurred in this pass. ✅
16. No placeholders/TODOs: runtime_ref, precision, file paths, test names, and
    commands are all concrete. ✅
17. C7 owns BOTH `execution_certificates.json` AND
    `backend/tests/test_local_gpu_certificates_exact.py`; Task 10 wording, Task 13
    scope, and the decomposition table agree exactly; no "certificate file only"
    statement remains. ✅
18. C8 does not self-reference its own SHA; it records known provenance (sealed
    baseline, plan SHA, C7 SHA) and the FINAL BHQ-3 SEAL SHA is obtained only via
    external Git verification AFTER C8 is committed. ✅
19. Post-C7 failure cannot be mistaken for completed qualification: candidate
    certificates are unsealed/non-mergeable, C8 is not created, and no completion
    claim is made (Task 10.1 + STOP section). ✅
20. New tooling/tests all have deterministic commit owners (C4/C5/C7); every task
    has a commit boundary. ✅
21. Only the plan document changed; `feature/m9-2-implementation` remains exactly
    at `c809eb02a5286737819136f9391d13949e8a117b`. ✅

---

## Task Decomposition Summary

| Commit | Message | Scope |
|---|---|---|
| C0 | `docs: add BHQ-3 local GPU qualification plan` | initial plan (`feature/bhq-local-gpu`) |
| C0b | `docs: harden BHQ-3 local GPU qualification evidence and acceptance ordering` | this corrective plan pass |
| C1 | `feat: add local_gpu CUDA health probe to local inference provider` | `local_executor.py` + provider tests |
| C2 | `feat: declare and gate local_gpu capability for cpn_bandwidth_tier` | CPN definition/plugin + tests |
| C3 | `feat: declare and gate local_gpu capability for zoomspec pipeline` | ZoomSpec definition/plugin + tests |
| C4 | `test: add local_gpu pre-certificate fail-closed gate` | Task 5 boundary tests + Task 7 gate (`test_local_gpu_pre_cert_gate.py`) |
| C5 | `test: add real local_gpu hardware acceptance harness` | acceptance test + ALL scripts (`bhq3_precision_dtype_probe.py`, `bhq3_local_gpu_runtime_identity.py`, `bhq3_direct_science_oracle.py`, `bhq3_local_gpu_acceptance.py`, `bhq3_local_gpu_production_acceptance.py`) |
| C6 | `docs: add bhq-3 local gpu pre-cert qualification evidence` | pre-cert acceptance doc (Tasks 1–9 facts only) |
| C7 | `feat: certify cpn and zoomspec local_gpu execution` | certificate store + exact certificate regression test only (`execution_certificates.json`, `backend/tests/test_local_gpu_certificates_exact.py`) |
| C8 | `docs: finalize bhq-3 local gpu acceptance evidence` | post-cert finalization of the same doc (known provenance; not its own SHA) |

No other production file may change. The final BHQ-3 seal is based on C8. The
**FINAL BHQ-3 SEAL SHA** is the C8 commit SHA obtained by external Git
verification after C8 is committed; it is reported in the execution report /
reviewer audit and never self-referenced inside the C8 document.

---

# Amendment A1 — Cache-Aware Memory Admission

**Status:** approved direction; implemented in acceptance-only tooling (not
production). This amendment preserves the original sealed plan text above,
which is annotated `superseded by Amendment A1` at its Task 8 step 5, Task 11
step 2, global constraint 19, STOP-list entry, and verification-index gate.

## A1.1 Provenance

```text
Original sealed plan SHA:                     589bf642b08276e6146cde4d1beb68e02cb39287
Implementation checkpoint before amendment:   c0362993b57f5f4bc9ee379472eec195eb839c16
(C1–C5 accepted; no C6/C7/C8; certificates unchanged)
```

## A1.2 Observed defect

The original pre-launch gate `memory.max - memory.current >= 4 GiB` produces a
**false negative** in a cache-dominated cgroup: clean, reclaimable file page
cache is accounted in `memory.current`, so the raw gate cannot pass while the
cache fills the limit, even though the workload's true (anonymous)
memory is tiny and there is no OOM/pressure.

## A1.3 Measured evidence (BHQ_MEMORY_RECLAIM_UNAVAILABLE report)

```text
memory.max       ≈ 90 GiB
memory.current   ≈ 87 GiB
anon             ≈ 1.4–1.5 GiB
file             ≈ 85.3 GiB   (active_file ≈ 78.4 GiB)
dirty/writeback  ≈ 0
unevictable      = 0
oom/oom_kill     = 0
max events       = 0
PSI current      ≈ 0
memory.reclaim   unavailable  (Linux 5.15)
cgroup2          read-only     (no cgroup-local reclaim possible)
host MemAvailable ≈ 652 GiB
```

## A1.4 Replacement gate (fail-closed, two paths)

Path A is unchanged raw admission; Path B is a **strictly guarded**
cache-dominant admission. Any missing/unparseable required field, or any failed
condition, blocks with `BHQ_3_BLOCKED_BY_CGROUP_MEMORY`.

### A1.4.1 Accounting model (total-minus-explicitly-clean)

To avoid silently omitting future/other kernel-accounted memory, the amendment
does NOT use an additive whitelist. It charges everything except explicitly
clean file cache into a conservative `committed_floor`:

```python
clean_file_cache = max(file - shmem - file_dirty - file_writeback, 0)
committed_floor  = max(memory.current - clean_file_cache, 0)
raw_headroom       = memory.max - memory.current
effective_headroom = memory.max - committed_floor
clean_cache_ratio  = clean_file_cache / memory.current   (0.0 if current == 0)
```

Notes: `memory.stat:file` includes tmpfs/shared memory, so `shmem` is NOT
treated as clean cache; dirty/writeback pages are NOT treated as immediately
reclaimable; `slab_reclaimable` is deliberately NOT subtracted; all
kernel/unknown accounting stays inside `committed_floor`.

### A1.4.2 Path A

```text
raw_headroom >= 4 GiB  →  mode = raw
```

### A1.4.3 Path B (only when Path A fails)

Require ALL (thresholds in the plan/tool config):

```text
 1. finite memory.max (not "max")
 2. committed_floor <= 16 GiB
 3. effective_headroom >= 6 GiB
 4. clean_file_cache >= 50% of memory.current
 5. file_dirty + file_writeback <= 512 MiB
 6. unevictable <= 256 MiB
 7. shmem <= 2 GiB
 8. memory.low == 0
 9. memory.min == 0
10. memory.events.max == 0
11. memory.events.oom == 0
12. memory.events.oom_kill == 0
13. memory.pressure some avg10 <= 5.0     (PSI percentage)
14. memory.pressure full avg10 <= 1.0     (PSI percentage)
15. host MemAvailable >= 64 GiB
```

`memory.events.high` is **not** a blocker; it is expected when `memory.high`
causes reclaim/throttling. PSI `avg10` values are stall **percentages**, not
seconds. This cache-guarded path is host/workload-qualified BHQ acceptance
logic, NOT a general production memory scheduler.

## A1.5 Live monitor / abort conditions

A pre-launch baseline snapshot is taken; the existing ~0.2 s acceptance poll
cadence samples cgroup state during the run. Abort the case (and the remaining
canary sequence) on ANY:

```text
Δoom > 0
OR Δoom_kill > 0
OR Δmemory.events.max > 0
OR PSI full avg10 > 10.0 for >= 3 consecutive samples
OR PSI some avg10 > 25.0 for >= 3 consecutive samples
OR committed_floor rises > 6 GiB above pre-launch baseline
OR effective_headroom < 4 GiB
OR (memory.current > memory.max - 256 MiB AND PSI full is rising)
```

Expected and NOT failures: `file`/`active_file` decreasing, `pgscan`/`pgsteal`
increasing, `memory.events.high` increasing, and `raw_headroom` remaining below
4 GiB during a `cache_guarded` run.

## A1.6 Guarded canary + driver order

Path B admission is only ever used for a staged canary, one real case per fresh
process, smallest-to-largest, re-evaluating admission from a fresh snapshot
before every case:

```text
CPN stem 2 → CPN stem 0 → ZoomSpec stem 2 → ZoomSpec stem 0
```

Any abort stops the remaining sequence. After every successful case: require no
`max`/`oom`/`oom_kill` delta and persist the memory evidence. The Task 9
direct-science oracle is a real CUDA workload and is guarded under the same
policy (parent controller → admission → one oracle child → ~0.2 s monitoring →
SIGTERM the exact child on abort). No scientific code changes.

## A1.7 Tooling (acceptance-only)

```text
scripts/bhq3_memory_gate.py          (new: pure evaluate() + reader + monitor)
scripts/bhq3_common.py               (require_memory_admission; raw helper kept)
scripts/bhq3_acceptance_core.py      (gate + live monitor + abort + evidence)
scripts/bhq3_direct_science_oracle.py (guarded parent/child execution)
backend/tests/test_bhq3_memory_gate.py (behavior tests)
```

`require_headroom_gib()` is retained as a raw-only compatibility helper with
unchanged semantics. No `backend/app/**` production file changes.

**Amendment commits:** A1-DOC (`docs: amend BHQ-3 memory admission gate
(cache-aware)`) then A1-TOOLING (`test: add cache-aware BHQ-3 memory admission
guard`).
