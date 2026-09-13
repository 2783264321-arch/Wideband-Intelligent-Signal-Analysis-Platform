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

**Acceptance evidence refs (Task 10):**
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

---

## Stop Conditions (fail closed; no certificate after any earlier blocker)

```text
PRECISION_CONTRACT_UNRESOLVED          Task 1 cannot reconcile local_gpu precision with accepted remote_gpu semantics without changing science.
CUDA_PROBE_FAILED                      Task 2 probe cannot detect/prove CUDA readiness in the configured interpreter.
ASSET_MISMATCH                         Task 8/9 asset manifest or bytes differ from the frozen golden manifests.
PRE_CERT_GATE_FAILED                   Task 7: provider+capability without a certificate does NOT report EXECUTION_NOT_CERTIFIED.
REAL_LOCAL_GPU_ACCEPTANCE_FAILED       Task 8: real worker run does not reach completed with persisted DetectionResults.
BHQ2_PARITY_FAILED                     Task 9: gross divergence from BHQ-2 CUDA outputs (counts alone insufficient).
PRODUCTION_PATH_FAILED                 Task 11: post-cert real AnalysisRun fails or falls back to CPU/remote.
REGRESSION_FAILED                      Task 12: any pytest failed/error.
SCOPE_VIOLATION                        Task 13: a frozen scientific / remote / DB / frontend file changed.
```

If a stop triggers: STOP immediately, do not issue/modify any certificate, record
the exact failure in the acceptance doc, and end the run with
`BHQ_3_BLOCKED_BY_<reason>`.

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

---

## Task 6 — Immutable Local-GPU Runtime Identity

**Files**

- No production file change. Recorded in the acceptance doc and used verbatim in
  `execution_certificates.json` (Task 10).
- Acceptance tool: `scripts/bhq3_local_gpu_runtime_identity.py` (prints/verifies).

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
5. `app = create_app(settings)`; then override
   `app.state.executor_registry = registry` (real production seams, temp cert
   injected at runtime).
6. Register the real SpaceNet Recording via external-path semantics (no IQ copy):
   use `app.datasets.spacenet.SpaceNetAdapter` to load `test/<stem>`, then insert
   `RecordingModel` with `external_path` + metadata + `GroundTruthModel` rows.
7. Launch via the real API path: `POST /api/analysis-runs`
   `{recording_id, pipeline_id, executor:"local_gpu", model_release_id:"golden", parameters:{}}`.
8. Poll `GET /api/analysis-runs/{id}` until terminal.
9. Assert: `status == "completed"`, `executor == "local_gpu"`, no error, no
   CPU/remote fallback; persisted `DetectionResult` rows with correct output label
   space; `execution_metadata_json.runtime_descriptor` is the exact tuple.
10. Capture per case: run id, stem, plugin/version/release, runtime descriptor,
    runtime_ref, asset manifest SHA, status, DetectionResult count, class
    histogram, wall time, peak RSS, peak CUDA allocated/reserved.

**Runner:** `scripts/bhq3_local_gpu_acceptance.py` runs the same steps for
`{cpn, zoomspec} × {stem 2, stem 0}` in separate processes (one case at a time),
writes `/tmp/bhq3_work/bhq3_temp_cert_evidence.json`, and prints a summary.

**STOP `REAL_LOCAL_GPU_ACCEPTANCE_FAILED`** if any case is not `completed` with
persisted detections, or if the worker did not run under
`/root/miniconda3/bin/python`.

**Commit C4:** `test: add local_gpu pre-certificate fail-closed gate`
**Commit C5:** `test: add real local_gpu hardware acceptance harness`
(include `scripts/bhq3_precision_dtype_probe.py`,
`scripts/bhq3_local_gpu_runtime_identity.py`,
`scripts/bhq3_local_gpu_acceptance.py`.)

---

## Task 9 — Parity Against BHQ-2

Compare the Task 8 `local_gpu` worker outputs against BHQ-2 frozen-science CUDA
outputs for matching stems:

```text
CPN      stem 2: 13 detections (Narrow 10 / Mid 2 / Wide 1)
CPN      stem 0: 17 detections (Narrow 9 / Mid 3 / Wide 5)
ZoomSpec stem 2: 11 detections
ZoomSpec stem 0: 13 detections
```

Do **not** use counts alone. Compare, per matching stem:

- physical TF boxes (`t_start_s`, `t_end_s`, `f_low_hz`, `f_high_hz`), matched by
  TF IoU;
- class ids and class names;
- confidence;
- ZoomSpec stage counts (`cpn_proposal_count`, `frn_valid_count`,
  `score_threshold_survivor_count`, `post_nms_count`).

Because the production worker and the BHQ-2 path execute the same frozen code, a
gross divergence (count mismatch beyond NMS ties, class/label disagreement,
systematic confidence shift) is a blocker. CPU-vs-GPU bitwise equality is NOT
required.

**STOP `BHQ2_PARITY_FAILED`** on gross divergence.

---

## Task 10 — Certificate Issuance (only after Tasks 7–9 pass)

**File:** `backend/app/pipelines/execution_certificates.json` (the ONLY production
change in this commit — its own reviewable commit).

Append two certificates exactly:

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

`evidence_ref` points to the committed acceptance document (Task 14). The four
existing certificates must remain byte-identical. Add a focused test
(`test_local_gpu_certificates_exact.py`) asserting the two new tuples are present
and the four existing tuples are unchanged.

**Commit C6:** `docs: add bhq-3 local gpu acceptance evidence` (evidence doc first)
**Commit C7:** `feat: certify cpn and zoomspec local_gpu execution` (certificate
file only)

Order: the evidence commit must exist before/with the certificate commit so the
`evidence_ref` is real.

---

## Task 11 — Post-Certification Real Production AnalysisRun

**No injected certificate.** Use the real production store.

**Driver:** `scripts/bhq3_local_gpu_production_acceptance.py` (committed), or the
`backend/tests/test_local_gpu_acceptance.py` production mode.

1. Build `Settings` with a dedicated DB (`/tmp/bhq3_work/postcert/...`), real
   SpaceNet external-path recording, `WSP_LOCAL_GPU_PYTHON_PATH=/root/miniconda3/bin/python`,
   `WSP_LOCAL_GPU_RUNTIME_REF=local:autodl_primary:gpu:7b958347b5af`,
   `WSP_LOCAL_INFERENCE_WORK_ROOT`, and the namespaced `WSP_LOCAL_ASSET_PATHS_JSON`
   for each golden manifest.
2. `app = create_app(settings)` — builds the real `ExecutorRegistry` from the
   committed certificate file + `build_local_providers`.
3. `POST /api/analysis-runs` with `executor:"local_gpu"` for CPN and ZoomSpec.
4. Require: `status == "completed"`, `executor == "local_gpu"`, separate ML
   interpreter execution, exact runtime descriptor tuple, `model_release_id == "golden"`,
   exact manifest SHA, persisted DetectionResults, no CPU/remote fallback.
5. `GET /api/pipelines`: require `local_gpu` to appear in `executors_supported`
   (and `recommended_executor` stays `None` for CPN/ZoomSpec because
   `recommended_execution` is `remote_gpu`, which is not a local_gpu recommendation).
6. `GET /api/executor-availability?executor=local_gpu` returns available when the
   probe passes.

**STOP `PRODUCTION_PATH_FAILED`** on any failure or fallback.

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
backend/app/pipelines/execution_certificates.json
backend/tests/... (focused tests)
scripts/bhq3_*.py (acceptance tooling)
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

## Task 14 — Acceptance Evidence Document

**File:** `docs/superpowers/acceptance/2026-09-14-bhq-3-local-gpu-production-qualification.md`

Record:

```text
accepted commit (post-cert HEAD)
GPU identity (RTX 5090, 32607 MiB, compute cap 12.0, driver 580.105.08, CUDA 12.8)
runtime_ref (local:autodl_primary:gpu:7b958347b5af) + generation material
runtime environment versions (python/torch/ultralytics/numpy/scipy)
precision-semantics conclusion (Task 1: answer B, effective dtype evidence)
asset hashes (cpn + zoomspec manifest + logical assets)
SpaceNet provenance (test stems 2/0 paths, sample metadata)
pre-cert fail-closed evidence (Task 7)
temporary-certificate acceptance evidence (Task 8, temp cert never persisted)
production certificate tuples (Task 10)
post-cert real AnalysisRun ids + DetectionResult counts (Task 11)
BHQ-2 parity (Task 9)
latency, peak RSS, peak VRAM per case
full pytest result (0 failed / 0 errors)
scope audit (SCOPE_OK)
limitations (mixed precision; generation identity is material, not exhaustive)
```

**Commit C6:** `docs: add bhq-3 local gpu acceptance evidence`

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

# task 1/8/9/11 acceptance tooling (operator-run, dedicated work root)
mkdir -p /tmp/bhq3_work
PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python scripts/bhq3_precision_dtype_probe.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_runtime_identity.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_acceptance.py
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" scripts/bhq3_local_gpu_production_acceptance.py

# task 12 full regression
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q

# task 13 scope
git diff --name-only c809eb02a5286737819136f9391d13949e8a117b..HEAD
```

---

## Self-Review (mandatory before committing the plan / after implementation)

1. Every M9.2 local_gpu/certificate invariant checked against the authoritative
   spec (§5.6/5.8/5.9, §8.2/8.4, §11.1–11.4, §18). ✅
2. The plan never allows plugin self-certification: Task 7 gate + Task 10
   platform-owned certificates only. ✅
3. Certificate issuance occurs strictly after real hardware evidence
   (Tasks 7–9 before Task 10). ✅
4. No frozen scientific code change is planned (scope audit Task 13). ✅
5. No remote/Phase-G change is planned. ✅
6. Precision semantics resolved before certificate creation (Task 1 → answer B
   = `float16`). ✅
7. No placeholders/TODOs: runtime_ref, precision, file paths, test names, and
   commands are all concrete. ✅
8. Exact file paths / test commands / commit boundaries provided. ✅

---

## Task Decomposition Summary

| Commit | Message | Scope |
|---|---|---|
| C0 | `docs: add BHQ-3 local GPU qualification plan` | this plan (already committed on `feature/bhq-local-gpu`) |
| C1 | `feat: add local_gpu CUDA health probe to local inference provider` | `local_executor.py` + provider tests |
| C2 | `feat: declare and gate local_gpu capability for cpn_bandwidth_tier` | CPN definition/plugin + tests |
| C3 | `feat: declare and gate local_gpu capability for zoomspec pipeline` | ZoomSpec definition/plugin + tests |
| C4 | `test: add local_gpu pre-certificate fail-closed gate` | new test only |
| C5 | `test: add real local_gpu hardware acceptance harness` | acceptance tests + `scripts/bhq3_*.py` |
| C6 | `docs: add bhq-3 local gpu acceptance evidence` | acceptance doc |
| C7 | `feat: certify cpn and zoomspec local_gpu execution` | `execution_certificates.json` only |

No other production file may change.
