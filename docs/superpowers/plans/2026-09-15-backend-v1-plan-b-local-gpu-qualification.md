# Backend V1 Plan B Local GPU Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-approved Backend V1 `local_gpu` runtime qualification operational and execute the full local-GPU qualification campaign on the current AutoDL server: scheme-aware `bhq3_gpu_v1` identity that reproduces `local:autodl_primary:gpu:7b958347b5af`, an explicit `local_gpu` qualification type, the real dataset → one-model (CPN ×16, c=1) and same-dataset → two-model (ZoomSpec ×16) workflows, genuine local_gpu crash/recovery, and a combined concurrency=2 + 40-execution endurance campaign — with auditable evidence, no executor substitution, no generic certificates, and no final Backend seal.

**Architecture:** Plan A (A1 recovery hardening, A2 Auto execution) and A3 (runtime doctor/identity/evidence/install/CLI) are already implemented at the accepted Plan-B base. Plan B does **not** redesign them. It adds (B0) a scheme-aware, ML-free-control-plane GPU identity collector that closes the A3 preflight defect where `local_gpu` always reported `identity_status=unavailable`; (B1) a `local_gpu_cuda_v1` qualification type + platform-owned `LocalGpuTargetProbe`/`LocalGpuQualificationRunner` reusing the same sealed seams (`provider.probe()`, `resolve_local_assets`, `verify_assets`, runtime identity validation) with no inference; (B2) acceptance-only, in-process operator configuration (never global profile mutation); (B3) the operational `doctor → qualify → certificate install` flow, whose expected outcome on this server is `already_certified` because the repo-default store already holds exact `local_gpu` certificates for the sealed runtime. H2–H5 are real-GPU acceptance campaigns driven through the exiting production seams (`create_app` → `DatasetExperiment` → coordinator → `LocalInferenceWorkerProvider` → ML interpreter worker → `AnalysisResultWriter` → benchmark worker), with acceptance-only tooling and gated tests; no production semantics change is introduced by the campaign tasks. `BACKEND_V1_SEAL_SHA` is not created and the GPU is not shut down.

**Tech Stack:** Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2 / SQLite, pytest (control-plane `.venv` is ML-free), stdlib `argparse`/`hashlib`/`json`/`subprocess`, the existing BHQ-3/Amendment-A1 acceptance tooling (`scripts/bhq3_common.py`, `scripts/bhq3_memory_gate.py`), and the configured ML interpreter `/root/miniconda3/bin/python` (torch 2.8.0+cu128, ultralytics 8.4.114, CUDA 12.8).

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

**Isolation:** branch `feature/backend-v1-plan-b-local-gpu`, worktree `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu`, base HEAD `640b358324a41a79f43496cd86fe2f4e827e181d`. The main worktree, `feature/backend-v1-final-qualification`, `feature/bhq-local-gpu`, `feature/m9-2-implementation`, `main`, and `feature/v1-core` are never modified.

## Global Constraints

1. **Portability / `nvidia-smi` boundary (explicit).** Production `app/` must never import `torch`/`ultralytics`; the control-plane `.venv` stays ML-free (`torch: None`, `ultralytics: None`). The following are acceptance-only and live only in `scripts/`: `/proc`-based PID identity/RSS telemetry, cgroup v2 memory/pressure/events reads, GPU utilization/memory sampling, crash-injection tooling, and the Amendment-A1 memory gate/monitor. `nvidia-smi` must NOT be a dependency of Auto policy, core executor selection, `local_cpu` runtime identity, or generic application startup (this preserves Windows/non-NVIDIA portability). `nvidia-smi` MAY be used by the fixed platform-owned `bhq3_gpu_v1` local_gpu identity/doctor probe (the sealed canonical material includes NVIDIA `driver_version`) and by operator-facing best-effort GPU diagnostics (as A3 already allows). The `local_gpu` `bhq3_gpu_v1` scheme is explicitly NVIDIA/CUDA-specific; absence or failure of `nvidia-smi` does NOT degrade to a generic identity — it produces `RUNTIME_IDENTITY_UNAVAILABLE`. The fixed command is platform-owned, takes no user-supplied shell input, and is invoked with `shell=False`. No universal `nvidia-smi` portability is claimed across non-NVIDIA systems.
2. **One runtime identity authority, scheme-versioned.** `provider.runtime_ref` remains the single authority. `identity.py` stays a pure derivation/validation helper; it holds no historical runtime hash literal and never branches on the `runtime_ref` string.
3. **CPU identity behavior unchanged.** `local_cpu_v1` material fields, canonicalization, and derivation are not modified. The GPU work is additive.
4. **BHQ3 payload fields exactly:** `python, torch, torch_cuda, ultralytics, numpy, scipy, device_name, compute_capability, driver_version, cuda_available`. Never add `platform_system`, `architecture`, GPU UUID, hostname, or absolute interpreter path. Never remove or rename a field.
5. **No generic local_gpu certificate.** No "any CUDA GPU" / widened tuple. Certification remains the exact 7-field key `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, runtime_ref)`.
6. **No executor substitution / no fallback.** A frozen `local_gpu` run is never relaunched as another executor.
7. **Qualification ≠ model acceptance.** `wisa qualify` establishes runtime identity + release/asset authority only and runs no inference. Real model execution evidence is H2/H3/H4/H5.
8. **No production change in H2–H5** beyond B0/B1 (and B0/B1 are the only production files touched). H2–H5 add acceptance tooling and tests only.
9. **No DB migration, no schema change, no frozen-science change, no frontend change, no training.**
10. **Historical state untouched.** `platform.db`, all G6/BHQ-3 DBs (`/tmp/bhq3_work`, `task12fc-*`, etc.), and the six repo-default certificates are read-only. Plan B uses dedicated qualification DBs/work roots.
11. **Preflight debt `OMP_NUM_THREADS=0` / `MKL_NUM_THREADS=0`.** This is a bounded environment-warning issue (libgomp emits `Invalid value for environment variable OMP_NUM_THREADS`), not a correctness blocker. It is fixed only by an **acceptance-only, in-process scoped override** in the Plan-B launcher (set valid values before `create_app`); global shell profiles are never edited. The override does not alter any `bhq3_gpu_v1` identity field.
12. **Memory safety uses the cache-aware Amendment-A1 reasoning**, never a raw `memory.current + 4 GiB` rule. Never `drop_caches`; never kill unrelated processes.
13. **Fault injection only targets verified qualification PIDs** (`/proc/<pid>/cmdline` contains `app.analysis.local_inference_worker <exact run_id>` or `app.dataset_experiments.worker <exact experiment_id> --coordinator-token <exact token>`). Never signal OpenCode, Jupyter, autopanel, the interactive backend, or system services.
14. **GPU execution budget** is `H2 16 + H3 16 + H4 ~2–4 + H5 40 = ~74–76`. Any real GPU execution outside this table must be individually justified in the evidence.
15. **GPU shutdown boundary.** Plan B ends with the GPU still on. Plan C (remote_gpu/two-host) is next, then H5.5-S, operator cold switch, H5.5-C, Plan D. Plan B never claims Backend V1 complete and never creates `BACKEND_V1_SEAL_SHA`.
16. **No secrets committed.** No SSH keys, tokens, or host credentials. Operator interpreter/asset absolute paths are deployment config, not secrets, and stay out of portable production defaults.
17. **Every production task is RED → GREEN**, run with the control-plane venv from the repository root.
18. **Missing identity material fails closed** (`RUNTIME_IDENTITY_UNAVAILABLE`); never substitute a changing `"unknown"` and then mint a supposedly exact certificate.
19. **Exact GPU quiescence rule (single definition, referenced by H2/H3/H4/H5/B-final).** Let `gpu_memory_baseline_mib` be the pre-campaign quiescent `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` value (measured; currently 0 MiB but the code always uses the measured value). After a run/experiment/campaign becomes terminal, the qualification-owned compute-app PID set (from `nvidia-smi --query-compute-apps=pid,used_memory`) must be empty, and within `GPU_QUIESCENT_TIMEOUT_S = 60` seconds `memory.used <= gpu_memory_baseline_mib + GPU_QUIESCENT_MARGIN_MIB` with `GPU_QUIESCENT_MARGIN_MIB = 64`. If compute-apps are empty AND `memory.used` stays `> gpu_memory_baseline_mib + 64 MiB` for 60 s, record `GPU_MEMORY_NOT_QUIESCENT` and abort the current campaign. GPU memory alone never infers a worker leak: PID/compute-app state is always checked too. The vague phrases "~0", "near baseline", and "near zero" are replaced by this rule.

---

## Authoritative environment baseline (Plan-B preflight, authoritative)

```text
GPU               NVIDIA GeForce RTX 5090
driver            580.105.08
GPU memory        32607 MiB
CUDA compat       13.0 (driver)
compute cap       12.0

ML interpreter    /root/miniconda3/bin/python
python            3.12.3
torch             2.8.0+cu128
torch.version.cuda 12.8
ultralytics       8.4.114
numpy             2.3.2
scipy             1.18.0
torch.cuda.is_available() True

BHQ3 material     {"compute_capability":"12.0","cuda_available":true,
                   "device_name":"NVIDIA GeForce RTX 5090",
                   "driver_version":"580.105.08","numpy":"2.3.2",
                   "python":"3.12.3","scipy":"1.18.0","torch":"2.8.0+cu128",
                   "torch_cuda":"12.8","ultralytics":"8.4.114"}
derived generation        7b958347b5af
derived runtime_ref       local:autodl_primary:gpu:7b958347b5af
historical sealed ref     local:autodl_primary:gpu:7b958347b5af   MATCH

control plane     /root/autodl-tmp/Wideband-Intelligent-Signal-Analysis-Platform/.venv/bin/python
                  python 3.12.3, pytest 9.1.1, fastapi 0.141.1, torch None, ultralytics None
CUDA health       PASS (FP16 64x64 matmul, memory 0 -> 0 MiB)

SpaceNet root     /root/autodl-tmp/SpaceNet_Dataset/advanced
                  test 2500 (.bin/.json), train 7500

golden assets (all SHA verified)
  detector_checkpoint    eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311
    /root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt
  ls_stft_normalization  9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f
    /root/autodl-tmp/release/support/normalization_ls_stft.json
  frn_checkpoint         da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67
    /root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt
  frozen_config          030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec
    /root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml

resource baseline host RAM 754 GiB total / 678 GiB available
                  cgroup memory.max 96636764160 (~90 GiB), memory.current ~22.8 GiB
                  anon ~1.16 GiB, file ~23.2 GiB (cache-dominated)
                  disk /root/autodl-tmp (/dev/md0) 53 GiB free
```

The recorded-BHQ3 fixture in `backend/tests/test_runtime_identity.py::BHQ3_MATERIAL` is authoritative for B0 and must reproduce `7b958347b5af`.

---

## Deferred GPU entry work carried from the preflight

The accepted A3 code contains:

```python
def collect_identity_material(python_path):   # collects LOCAL_CPU_V1_FIELDS only
```

Because `local_gpu` derivation feeds this CPU-only material into `bhq3_gpu_v1`, the doctor produced:

```text
identity_scheme = bhq3_gpu_v1
identity_status = unavailable        # RUNTIME_IDENTITY_INVALID swallowed
derived_runtime_ref = null
```

even though the real environment independently reproduces `7b958347b5af`. This is **not** a redesign of A3; B0 is the first deferred GPU-specific implementation item.

---

## GPU execution budget

| Milestone | Executions | Notes |
|---|---:|---|
| B0 identity collector (gated test) | 0 | material collection is a read-only probe, not model inference |
| B1 local_gpu qualification | 0 | runtime identity + CUDA health + assets only; no inference |
| B2 operator config | 0 | read-only |
| B3 certificate/evidence flow | 0 | runtime qualification only; no inference |
| H2 CPN ×16 (c=1) | 16 | also concurrency=1 baseline and H3 Experiment A |
| H3 ZoomSpec ×16 | 16 | same frozen membership as H2 |
| H4 genuine live worker kills | 2–4 | all other H4 cases are CPU/DB-only |
| H5 concurrency=2 + endurance | 40 | 16 + 16 + 8 cycles of the same 16 stems |
| **local_gpu total** | **~74–76** | |
| Plan C remote_gpu | separate | not in this plan |

No separate concurrency experiment; no repeated single-recording health campaign (BHQ-3 owns that).

---

## Shared Plan-B paths and constants (used by B2–H5)

```text
PLAN_B_ROOT            /root/autodl-tmp/plan_b_qual
evidence root          /root/autodl-tmp/plan_b_qual/evidence
b2/b3 root             /root/autodl-tmp/plan_b_qual/b3
h2h3 root              /root/autodl-tmp/plan_b_qual/h2h3
h4 root                /root/autodl-tmp/plan_b_qual/h4
h5 root                /root/autodl-tmp/plan_b_qual/h5

runtime family         autodl_primary
ML interpreter         /root/miniconda3/bin/python
GPU runtime_ref        local:autodl_primary:gpu:7b958347b5af
SpaceNet root          /root/autodl-tmp/SpaceNet_Dataset/advanced
dataset                SpaceNet / test / spacenet_14
H2/H3/H5 stems         0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280   (16)
CPN plugin             cpn_bandwidth_tier / 1.0.0 / golden / 7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb
ZoomSpec plugin         zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0 / golden / 16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08
evaluation protocol    benchmark default physical TF protocol (do not override)
```

No secret is stored anywhere; all values are deployment configuration.

---

# Task B0 — Scheme-aware GPU identity collector

**GPU REQUIRED: NO for the RED/GREEN unit tests (deterministic fixture). A separate opt-in gated test runs against the real ML interpreter when CUDA is present.**

**Files**
- Modify: `backend/app/runtime_qualification/identity.py`
- Modify: `backend/app/runtime_qualification/qualification.py` (caller)
- Modify: `backend/app/cli.py` (caller)
- Modify: `backend/tests/test_runtime_identity.py`
- Modify: `backend/tests/test_cli.py`
- Modify: `backend/tests/test_runtime_qualification_integration.py`
- Test: `backend/tests/test_runtime_identity_gpu_collector.py` (new)

**Interfaces**

```python
# identity.py
_LOCAL_CPU_IDENTITY_PROBE_SCRIPT = """... existing 7-field CPU probe body ..."""
**Required GPU identity collection order (fail closed; any failure → `RUNTIME_IDENTITY_UNAVAILABLE`, no material returned):**

```text
1. import required fixed packages (numpy, scipy, torch, ultralytics)
2. cuda_available = torch.cuda.is_available()
3. require cuda_available is True
4. require torch.cuda.device_count() >= 1
5. use exact device index 0
6. collect device_name + compute_capability from device 0 properties
7. run the fixed driver query for device 0
8. require driver query returncode == 0
9. require exactly one non-empty driver-version line, and it must not be "unknown"
10. assemble the exact 10-field BHQ3 payload
```

The exact recorded BHQ3 fixture MUST still reproduce `7b958347b5af`. `"unknown"` can never enter the canonical material.

```python
# identity.py
_LOCAL_CPU_IDENTITY_PROBE_SCRIPT = """... existing 7-field CPU probe body ..."""

# Fixed platform-owned torch/CUDA probe, executed INSIDE the configured ML
# interpreter. It never invents a value: on any failure it reports ok=False.
_BHQ3_GPU_TORCH_PROBE_SCRIPT = """
import json, sys
import numpy, scipy, torch, ultralytics

result = {"ok": False, "reason": None, "material": None}
try:
    if not torch.cuda.is_available():
        result["reason"] = "cuda unavailable"
    elif torch.cuda.device_count() < 1:
        result["reason"] = "no cuda device"
    else:
        props = torch.cuda.get_device_properties(0)
        result["ok"] = True
        result["material"] = {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "ultralytics": ultralytics.__version__,
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "device_name": props.name,
            "compute_capability": "%d.%d" % (props.major, props.minor),
            "cuda_available": True,
            "device_count": int(torch.cuda.device_count()),
        }
except Exception as exc:
    result["reason"] = type(exc).__name__
print(json.dumps(result))
"""

# Fixed platform-owned driver query, locked to device 0 so multi-GPU hosts still
# yield exactly one line. No user-supplied shell input; invoked with shell=False.
_BHQ3_GPU_DRIVER_QUERY = (
    "nvidia-smi", "-i", "0",
    "--query-gpu=driver_version", "--format=csv,noheader",
)

def assemble_bhq3_gpu_material(probe_material: dict, driver_version: str) -> dict: ...
    # PURE, fail-closed assembly (unit-testable without CUDA). Rejects:
    #   probe_material["cuda_available"] is not exactly True
    #   int(probe_material["device_count"]) < 1
    #   missing/empty/non-string device_name or compute_capability
    #   missing/empty/non-string python/torch/torch_cuda/ultralytics/numpy/scipy
    #   driver_version empty, multi-line, whitespace-only, or == "unknown"
    # Any rejection -> RUNTIME_IDENTITY_UNAVAILABLE; no partial material.
    # Returns exactly BHQ3_GPU_V1_FIELDS (drops the internal device_count).

def collect_local_cpu_identity_material(python_path, *, runner=None) -> dict: ...
    # unchanged CPU behavior: runs _LOCAL_CPU_IDENTITY_PROBE_SCRIPT inside the ML
    # interpreter, requires set(payload) == set(LOCAL_CPU_V1_FIELDS).

def collect_bhq3_gpu_identity_material(python_path, *, runner=None) -> dict: ...
    # 1. interpreter must exist else RUNTIME_IDENTITY_UNAVAILABLE
    # 2. run _BHQ3_GPU_TORCH_PROBE_SCRIPT via runner(..., shell=False); require
    #    returncode == 0 and last JSON line {"ok": True, "material": {...}}
    #    else RUNTIME_IDENTITY_UNAVAILABLE
    # 3. run _BHQ3_GPU_DRIVER_QUERY via runner(..., shell=False); require
    #    returncode == 0 and stdout.strip() is exactly one non-empty line
    #    else RUNTIME_IDENTITY_UNAVAILABLE
    # 4. return assemble_bhq3_gpu_material(probe_material, driver_line)

def collect_identity_material(python_path, *, scheme: str, runner=None) -> dict: ...
    # dispatch ONLY on `scheme`; unknown scheme -> RUNTIME_IDENTITY_INVALID.
    # BHQ3_GPU_V1 -> collect_bhq3_gpu_identity_material
    # LOCAL_CPU_V1 -> collect_local_cpu_identity_material
```

`runner` is the injectable subprocess seam (`Callable[..., subprocess.CompletedProcess]`, default `subprocess.run`); tests pass a deterministic fake. Both GPU subprocess calls use `shell=False`; no user-supplied code or input is ever executed. `assemble_bhq3_gpu_material` is the single place that decides whether a complete exact material exists, so `"unknown"`, empty/absent device data, and malformed driver output can never be hashed into an identity. The `scheme` argument is required (no CPU default), so CPU material can never silently reach a GPU scheme; `derive_generation_for_scheme` retains its exact-field check as the second line of defense.

**Caller updates (behavior-preserving)**
- `qualification.py::LocalCpuTargetProbe._material` → `collect_identity_material(self._settings.local_cpu_python_path, scheme=LOCAL_CPU_V1)`.
- `cli.py::_make_identity_resolver._derive` → `collect_identity_material(python_path, scheme=scheme)`.
- Existing test callers using `collect_identity_material(Path(sys.executable))` pass `scheme=LOCAL_CPU_V1`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_identity_gpu_collector.py`:

```text
1. test_bhq3_gpu_collector_reproduces_recorded_identity
   - fake runner: call 1 (torch probe) -> {"ok": True, "material": <9 raw fields>},
     call 2 (driver query) -> returncode 0, stdout "580.105.08\n"
   - collect_identity_material(Path("/ml/python"), scheme=BHQ3_GPU_V1, runner=fake)
     == BHQ3_MATERIAL (exactly the 10 canonical fields; internal device_count dropped)
   - derive_generation_for_scheme(scheme=BHQ3_GPU_V1, material=...) == "7b958347b5af"
2. test_dispatch_never_feeds_cpu_material_to_gpu_scheme
   - fake CPU runner (returns the 7-field CPU payload) + scheme=BHQ3_GPU_V1
     raises RUNTIME_IDENTITY_INVALID (field-set mismatch) — never a silent derive
3. test_dispatch_never_feeds_gpu_material_to_cpu_scheme
   - fake GPU runner + scheme=LOCAL_CPU_V1 raises RUNTIME_IDENTITY_INVALID
4. test_unknown_scheme_rejected
   - collect_identity_material(..., scheme="nope") raises RUNTIME_IDENTITY_INVALID
5. test_assemble_bhq3_gpu_material_fails_closed
   - cuda_available is False -> RUNTIME_IDENTITY_UNAVAILABLE
   - device_count == 0 -> RUNTIME_IDENTITY_UNAVAILABLE
   - device_name missing/empty -> RUNTIME_IDENTITY_UNAVAILABLE
   - compute_capability missing/empty -> RUNTIME_IDENTITY_UNAVAILABLE
   - any version field missing/None -> RUNTIME_IDENTITY_UNAVAILABLE
   - driver_version == "" / "   " / "a\nb" / "unknown" -> RUNTIME_IDENTITY_UNAVAILABLE
   - assert NO material and NO generation is ever produced on rejection
6. test_gpu_collector_fails_closed_on_probe_failure
   - missing interpreter -> RUNTIME_IDENTITY_UNAVAILABLE
   - torch probe runner returncode != 0 -> RUNTIME_IDENTITY_UNAVAILABLE
   - torch probe stdout {"ok": False, ...} -> RUNTIME_IDENTITY_UNAVAILABLE
   - torch probe malformed/absent JSON -> RUNTIME_IDENTITY_UNAVAILABLE
7. test_gpu_collector_fails_closed_on_driver_failure
   - driver query runner returncode != 0 -> RUNTIME_IDENTITY_UNAVAILABLE
   - driver stdout empty/whitespace -> RUNTIME_IDENTITY_UNAVAILABLE
   - driver stdout two lines (multi-GPU/malformed) -> RUNTIME_IDENTITY_UNAVAILABLE
   - driver stdout "unknown" -> RUNTIME_IDENTITY_UNAVAILABLE
8. test_gpu_probe_script_is_platform_owned_and_bounded
   - the torch probe script imports only stdlib + numpy/scipy/torch/ultralytics and
     declares exactly the 10 raw fields (including internal device_count)
   - the only external command is the fixed _BHQ3_GPU_DRIVER_QUERY tuple, invoked
     with shell=False
   - source-scan asserts the GPU path contains no `"unknown"` fallback and no
     user-controlled input
```

Add an opt-in real-interpreter test (same file):

```text
9. test_integration_bhq3_gpu_collector_real_interpreter  (gated)
   - skip unless WSP_PLAN_B_GPU_IDENTITY=1 and /root/miniconda3/bin/python exists
   - collect_bhq3_gpu_identity_material(Path("/root/miniconda3/bin/python"))
   - assert generation == "7b958347b5af" and runtime_ref ==
     "local:autodl_primary:gpu:7b958347b5af"
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_identity_gpu_collector.py -q
```

Expected RED cause: `ImportError`/`AttributeError` — `collect_identity_material` has no `scheme` parameter; `collect_bhq3_gpu_identity_material` does not exist. Also confirm the defect is real with:

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c \
"from app.runtime_qualification import identity as i; \
from pathlib import Path; \
m=i.collect_identity_material(Path('/root/miniconda3/bin/python')); print(sorted(m))"
```

Expected: prints the 7 CPU keys (the bug) before implementation.

- [ ] **Step 3: Minimal implementation**

Add the two collectors + dispatcher + the pure `assemble_bhq3_gpu_material` validator + the fixed `_BHQ3_GPU_DRIVER_QUERY` to `identity.py`; update the three callers; update the existing CPU-scheme test call sites. Do not alter `canonical_material_bytes`, `derive_generation`, `derive_local_runtime_ref`, `derive_generation_for_scheme`, `BHQ3_GPU_V1_FIELDS`, `LOCAL_CPU_V1_FIELDS`, `resolve_identity_scheme`, `validate_runtime_ref_against_material`, or `validate_configured_local_runtime_ref`.

- [ ] **Step 4: Run GREEN**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_identity_gpu_collector.py \
  backend/tests/test_runtime_identity.py \
  backend/tests/test_runtime_doctor.py \
  backend/tests/test_runtime_qualification.py \
  backend/tests/test_runtime_qualification_integration.py \
  backend/tests/test_cli.py -q
```

Expected: `0 failed`, `0 errors`.

- [ ] **Step 5: Opt-in real-interpreter identity proof**

```bash
WSP_PLAN_B_GPU_IDENTITY=1 PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_identity_gpu_collector.py -q
```

Expected: `7b958347b5af` reproduced, `RUNTIME_IDENTITY_OK`-equivalent assertion passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime_qualification/identity.py \
  backend/app/runtime_qualification/qualification.py \
  backend/app/cli.py \
  backend/tests/test_runtime_identity_gpu_collector.py \
  backend/tests/test_runtime_identity.py \
  backend/tests/test_cli.py \
  backend/tests/test_runtime_qualification_integration.py
git commit -m "feat: add scheme-aware gpu runtime identity collection"
```

**Review checkpoint (B0):** control-plane `.venv` still reports `torch: None`, `ultralytics: None`; the recorded BHQ3 fixture reproduces `7b958347b5af`; CUDA-unavailable, zero-device, and driver-query failures produce `RUNTIME_IDENTITY_UNAVAILABLE` (never `"unknown"`, never a derived generation); CPU identity derivation output is bit-identical to the pre-change output for a fixed CPU material dict; `identity.py` still contains no historical hash literal.

---

# Task B1 — local_gpu qualification type + platform-owned runner

**GPU REQUIRED: NO for RED/GREEN (deterministic fakes). The real local_gpu qualification is exercised in B3.**

**Files**
- Modify: `backend/app/runtime_qualification/qualification.py`
- Modify: `backend/app/cli.py`
- Modify: `backend/tests/test_runtime_qualification.py`
- Modify: `backend/tests/test_cli.py`
- Modify: `backend/tests/test_certificate_install.py`

**Interfaces**

```python
# qualification.py
LOCAL_GPU_CUDA_V1 = "local_gpu_cuda_v1"

INSTALL_ELIGIBLE_TYPES = {
    "local_cpu": (LOCAL_CPU_SMOKE_V1,),
    "local_gpu": (LOCAL_GPU_CUDA_V1,),   # B1 replaces the A3 empty tuple
    "remote_gpu": (),                    # still none (Plan C)
}

class LocalGpuTargetProbe:
    """Platform-owned PRODUCTION probe for local_gpu. Generic; no plugin-id branch.
    Runs NO inference. Reuses existing seams; mirrors LocalCpuTargetProbe order."""
    def __init__(self, *, settings, definition, provider, model_release_store,
                 material_probe=None, now=None) -> None: ...
    def __call__(self, target: QualificationTarget) -> None:
        # 1. target.executor == "local_gpu" and provider is registered
        # 2. provider.name == "local_gpu"
        # 3. provider.probe() succeeds (interpreter + work root + real CUDA FP16 probe)
        # 4. provider.runtime_ref == target.runtime_ref
        # 5. provider.runtime_descriptor().to_metadata() == target.runtime_descriptor
        #    and descriptor.executor == "local_gpu", device_type == "cuda"
        # 6. exact technical capability (local_gpu/cuda/float16) declared by the definition
        # 7. runtime_family configured; parse(target.runtime_ref) -> (family, kind) with
        #    kind == "gpu" and family == settings.runtime_family
        # 8. material = collect_identity_material(local_gpu_python_path, scheme=BHQ3_GPU_V1)
        #    validate_runtime_ref_against_material(kind="gpu", scheme=BHQ3_GPU_V1, material)
        # 9. plugin id/version == current definition
        # 10. model release resolves exactly; local asset mapping resolves and
        #     verify_assets(...) passes for the exact AssetManifest SHA
        # 11. release-less plugin must carry no release identity
        # raises PlatformError("QUALIFICATION_PROBE_FAILED", <reason>) on ANY failure

class LocalGpuQualificationRunner:
    qualification_type = LOCAL_GPU_CUDA_V1
    identity_scheme = BHQ3_GPU_V1
    def __init__(self, target_probe=None) -> None: ...
    def run(self, *, target) -> tuple[QualificationResult, ...]:
        # no probe -> (QualificationResult("probe", False, "QUALIFICATION_PROBE_UNAVAILABLE"),)
        # probe failure -> (QualificationResult("probe", False, <message>),)
        # success -> (QualificationResult("probe", True),)

def build_default_target_probe(*, target, settings, pipeline_registry,
                               model_release_store, executor_registry, now=None):
    # executor == "local_cpu" -> LocalCpuTargetProbe
    # executor == "local_gpu" -> LocalGpuTargetProbe
    # executor == "remote_gpu" -> ValueError (no local probe)

def select_runner(*, executor, target_probe=None):
    # "local_cpu" -> LocalCpuQualificationRunner
    # "local_gpu" -> LocalGpuQualificationRunner
    # else        -> DeferredGpuQualificationRunner
```

`LocalGpuTargetProbe._material` uses an injectable `material_probe` (defaults to the B0 GPU collector against `settings.local_gpu_python_path`), so unit tests never need CUDA.

**CLI (`cli.py::_cmd_qualify`):** build the real probe via `build_default_target_probe` for `local_cpu` **and** `local_gpu`; only `remote_gpu` uses `DeferredGpuQualificationRunner`. `wisa qualify --executor local_gpu` must never default to a permanently-failing probe.

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_runtime_qualification.py`:

```text
1. test_local_gpu_runner_passes_with_passing_probe -> passed == True, non-empty results
2. test_local_gpu_runner_without_probe -> passed == False, "QUALIFICATION_PROBE_UNAVAILABLE"
3. test_local_gpu_runner_probe_failure -> passed == False, no exception
4. test_local_gpu_install_eligible -> qualification_type_install_eligible(executor="local_gpu", type="local_gpu_cuda_v1") is True;
   "gpu_deferred" and unknown types are False
5. test_local_gpu_target_probe_enforces_every_check (injectable fakes):
   provider missing; provider.name != local_gpu; provider.probe() fails;
   runtime_ref mismatch; descriptor mismatch; capability absent; runtime_family None;
   wrong kind; identity material mismatch; plugin/version mismatch; release mismatch;
   asset manifest SHA mismatch; release-less plugin carrying a release -> each raises
   QUALIFICATION_PROBE_FAILED; full success returns None; no inference runs
6. test_build_default_target_probe_selects_local_gpu (architecture) -> isinstance LocalGpuTargetProbe
7. test_select_runner_local_gpu -> LocalGpuQualificationRunner, identity_scheme == "bhq3_gpu_v1"
```

Add to `backend/tests/test_cli.py`:

```text
8. test_qualify_local_gpu_builds_real_probe (architecture) and writes passing evidence
   with an injected probe; exit 0
9. test_qualify_local_gpu_evidence_identity_scheme == "bhq3_gpu_v1"
```

Add to `backend/tests/test_certificate_install.py`:

```text
10. test_install_accepts_local_gpu_cuda_v1 evidence against matching LiveAuthority
    (fake live authority with provider present + technical capability) and returns an
    exact ExecutionCertificate; a generic/widened tuple is impossible because the
    certificate is built from evidence.runtime_descriptor + exact 7-field key
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_qualification.py \
  backend/tests/test_cli.py \
  backend/tests/test_certificate_install.py -q
```

Expected RED cause: `ImportError: cannot import name 'LOCAL_GPU_CUDA_V1'` / `LocalGpuTargetProbe`; and `qualification_type_install_eligible(executor="local_gpu", ...)` is currently `False`.

- [ ] **Step 3: Minimal implementation**

Add the constant, eligibility entry, `LocalGpuTargetProbe`, `LocalGpuQualificationRunner`, and dispatch in `build_default_target_probe`/`select_runner`; wire the CLI. Do not modify `LocalCpuTargetProbe`, `LocalCpuQualificationRunner`, `DeferredGpuQualificationRunner`, `run_qualification`, or `evidence.py`.

- [ ] **Step 4: Run GREEN**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_qualification.py \
  backend/tests/test_runtime_qualification_integration.py \
  backend/tests/test_cli.py \
  backend/tests/test_certificate_install.py \
  backend/tests/test_runtime_doctor.py -q
```

Expected: `0 failed`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/qualification.py backend/app/cli.py \
  backend/tests/test_runtime_qualification.py backend/tests/test_cli.py \
  backend/tests/test_certificate_install.py
git commit -m "feat: add local_gpu runtime qualification"
```

**Review checkpoint (B1):** no generic certificate path exists (certificate tuple is derived only from exact evidence identity); control plane ML-free; `local_cpu` qualification tests unchanged and green; `select_runner("remote_gpu")` still returns the deferred runner.

---

# Task B2 — Server operator configuration (acceptance-only)

**GPU REQUIRED: NO.**

**Files**
- New: `scripts/plan_b_common.py`
- New: `scripts/plan_b_env.py`
- New: `backend/tests/test_plan_b_env.py`

**Interfaces (`scripts/plan_b_common.py`)**

```python
REPO            = Path(__file__).resolve().parents[1]
BACKEND         = REPO / "backend"
PLAN_B_ROOT     = Path("/root/autodl-tmp/plan_b_qual")
EVIDENCE_DIR    = PLAN_B_ROOT / "evidence"
RUNTIME_FAMILY  = "autodl_primary"
ML_PYTHON       = Path("/root/miniconda3/bin/python")
GPU_RUNTIME_REF = "local:autodl_primary:gpu:7b958347b5af"
SPACENET_ROOT   = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")
DATASET_NAME    = "SpaceNet"; DATASET_SPLIT = "test"; DATASET_LABEL_SPACE = "spacenet_14"
H2_STEMS        = ("0","1","2","3","9","11","12","15","32","42","79","80","83","99","109","280")
ASSET_PATHS     = { "detector_checkpoint": Path(...), "ls_stft_normalization": Path(...),
                    "frn_checkpoint": Path(...), "frozen_config": Path(...) }

def plan_b_asset_map(plugin_id: str) -> dict:
    # {"<plugin_id>/<version>/<manifest_sha256>": {logical: absolute path}}
    # built from the committed manifest via load_pipeline_asset_manifest, never hardcoded

# The exact expected Plan-B WSP environment-key set (9 keys; no fixed numeric count
# is asserted elsewhere):
PLAN_B_WSP_KEYS = (
    "WSP_RUNTIME_FAMILY",              # autodl_primary
    "WSP_LOCAL_GPU_PYTHON_PATH",       # /root/miniconda3/bin/python
    "WSP_LOCAL_GPU_RUNTIME_REF",       # local:autodl_primary:gpu:7b958347b5af
    "WSP_LOCAL_INFERENCE_WORK_ROOT",   # <root>/work
    "WSP_LOCAL_ASSET_PATHS_JSON",      # union of CPN+ZoomSpec namespaced maps
    "WSP_PROJECT_ROOT",
    "WSP_DATA_ROOT",                   # <root>/data
    "WSP_LABEL_SPACE_ROOT",
    "WSP_DATABASE_URL",                # sqlite:///<root>/qual.db
)

def bootstrap_env_before_app_import(root: Path) -> None:
    # set exactly PLAN_B_WSP_KEYS in os.environ, IN THIS PROCESS ONLY
    # (no profile mutation), plus the acceptance-only scoped thread override
    # (Global Constraint 11): OMP_NUM_THREADS="1", MKL_NUM_THREADS="1"
    # must be called before importing app.main / create_app so spawned coordinator
    # and worker subprocesses (JobManager copies os.environ) inherit it.

def build_settings(root: Path):
    # returns Settings() AFTER bootstrap_env_before_app_import(root)

def verify_configuration() -> dict:
    # read-only: imports Settings, reports masked presence for exactly
    # PLAN_B_WSP_KEYS, derives the gpu runtime identity via
    # identity.collect_identity_material(scheme=BHQ3_GPU_V1), asserts interpreter
    # exists, work root/data root exist or are creatable, asset namespaces resolve,
    # and derived == GPU_RUNTIME_REF. Returns a JSON-serializable report; never
    # prints secrets.
```

**Interfaces (`scripts/plan_b_env.py`)** — operator entry point:

```text
python scripts/plan_b_env.py --verify        # prints the B2 verification report (JSON)
python scripts/plan_b_env.py --init          # creates PLAN_B_ROOT/{evidence,b3,h2h3,h4,h5}/{data,work}
```

Rules: does **not** write to `~/.bashrc`/`~/.profile`/`/etc/environment`; does **not** modify any existing server configuration; does **not** commit absolute paths into portable production defaults (all paths live only in this acceptance tooling).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_plan_b_env.py`:

```text
1. test_plan_b_asset_map_matches_manifests: namespaces equal the committed CPN and
   ZoomSpec manifest SHAs; every logical asset name present; absolute paths
2. test_bootstrap_env_sets_only_plan_b_keys: after bootstrap, exactly PLAN_B_WSP_KEYS
   are set (assert the explicit enumerated key set, not a numeric count):
   WSP_RUNTIME_FAMILY, WSP_LOCAL_GPU_PYTHON_PATH, WSP_LOCAL_GPU_RUNTIME_REF,
   WSP_LOCAL_INFERENCE_WORK_ROOT, WSP_LOCAL_ASSET_PATHS_JSON, WSP_PROJECT_ROOT,
   WSP_DATA_ROOT, WSP_LABEL_SPACE_ROOT, WSP_DATABASE_URL;
   runtime_family == "autodl_primary", gpu ref == sealed ref, OMP/MKL overrides are
   valid positive integers, and pre-existing unrelated env keys are untouched
3. test_bootstrap_env_does_not_touch_profiles: source-scan plan_b_common.py contains no
   ".bashrc"/".profile"/"/etc/environment" write
4. test_verify_configuration_reports_identity: derived generation == 7b958347b5af
   (uses the real ML interpreter only when available; otherwise assert the derivation
   is computed from collect_bhq3_gpu_identity_material and skip when CUDA absent)
5. test_no_secrets_in_plan_b_tooling: source-scan contains no "ssh"/"passphrase"/
   "BEGIN OPENSSH"/"token" literal
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_env.py -q
```

Expected RED cause: `ModuleNotFoundError: No module named 'plan_b_common'` (scripts is not on `sys.path`).

- [ ] **Step 3: Minimal implementation**

Implement `scripts/plan_b_common.py`, `scripts/plan_b_env.py`, and the test. `plan_b_common` inserts `BACKEND` and `SCRIPTS` on `sys.path` and may reuse `scripts/bhq3_common.py`/`scripts/bhq3_memory_gate.py` for the A1 gate.

- [ ] **Step 4: Run GREEN + operator proof**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_env.py -q
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_env.py --init
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_env.py --verify
```

Expected: tests pass; verification report shows `runtime_family=autodl_primary`, `configured_runtime_ref == derived_runtime_ref == local:autodl_primary:gpu:7b958347b5af`, interpreter present, asset namespaces resolved, no secrets. Write the report to `/root/autodl-tmp/plan_b_qual/evidence/b2_config_verification.json`.

- [ ] **Step 5: Commit**

```bash
git add scripts/plan_b_common.py scripts/plan_b_env.py backend/tests/test_plan_b_env.py
git commit -m "test: add plan b server operator configuration"
```

**Review checkpoint (B2):** configuration is proven before any real campaign; no global environment mutation; `OMP_NUM_THREADS`/`MKL_NUM_THREADS` override is scoped and valid.

---

# Task B3 — A3 qualification workflow on the real GPU

**GPU REQUIRED: YES (interpreter + CUDA health probe + asset verification only; NO model inference).**

**Files**
- New: `scripts/plan_b_certificate_flow.py`
- New: `backend/tests/test_plan_b_certificate_flow.py`

**Workflow (exact)**

```text
bootstrap_env (B2) + dedicated DB/work root /root/autodl-tmp/plan_b_qual/b3
  → runtime doctor (in-process; settings from B2)
      assert provider local_gpu: identity_scheme == "bhq3_gpu_v1",
              identity_status == "match",
              derived_runtime_ref == local:autodl_primary:gpu:7b958347b5af,
              runtime_family == "autodl_primary",
              interpreter available, gpu available true
  → wisa qualify --plugin cpn_bandwidth_tier --plugin-version 1.0.0 \
        --executor local_gpu --model-release golden        (real LocalGpuTargetProbe)
  → wisa qualify --plugin zoomspec_yolo26n_aug_combined_frn_v3 --plugin-version 1.0.0 \
        --executor local_gpu --model-release golden
  → wisa certificate install --from <evidence_dir>   (per plugin)
      expected status == "already_certified"
      (repo-default CPN/ZoomSpec local_gpu certificates already exist for the
       sealed runtime_ref; install resolves idempotency BEFORE writing and adds
       NO operator duplicate; duplicate exact-key semantics unchanged)
  → assert the repo-default certificate store file is byte-identical before/after
```

Qualification is invoked with the identical code path as production: `identity_module.collect_identity_material(..., scheme=BHQ3_GPU_V1)` + B1 probe. No `LocalGpuQualificationRunner` fake is used here.

**Negative evidence (all must fail closed, 0 GPU executions)**

Integrity negatives (tampering detected by the evidence hash):

```text
I1 tampered serialized evidence without recomputing evidence_sha256
   (runtime_ref or asset_manifest_sha256 mutated) -> QUALIFICATION_EVIDENCE_INVALID
I2 vacuous/malformed evidence (empty results, wrong schema/keys) -> QUALIFICATION_EVIDENCE_INVALID
```

Live-authority drift negatives (evidence is internally valid and its `evidence_sha256`
is correctly recomputed, but the CURRENT live authority has moved):

```text
D1 runtime authority drift
   evidence.runtime_ref = OLD_RUNTIME_REF, evidence_sha256 recomputed correctly
   CURRENT provider.runtime_ref = NEW_RUNTIME_REF
   -> evidence load succeeds; certificate install fails closed
      (live-authority runtime_ref mismatch); no operator certificate written
D2 manifest authority drift
   evidence.model_release_id unchanged; evidence.asset_manifest_sha256 = OLD_MANIFEST_SHA,
   evidence_sha256 recomputed correctly
   CURRENT resolved ModelRelease.asset_manifest_sha256 = NEW_MANIFEST_SHA
   -> evidence load succeeds; certificate install fails closed
      (asset-manifest authority mismatch); no operator certificate written
D3 wrong executor
   evidence.executor local_cpu against CURRENT local_gpu authority -> rejection
D4 wrong runtime family
   CURRENT WSP_RUNTIME_FAMILY=other_family -> doctor mismatch / LocalGpuTargetProbe failure
   (isolated subprocess; alternate family never persisted)
D5 failed CUDA probe
   provider probe forced false -> LocalGpuTargetProbe raises QUALIFICATION_PROBE_FAILED
D6 plugin self-certification
   source scan proves only install.py writes the operator store
D7 duplicate/conflict
   same exact operator key with conflicting evidence_ref -> fail closed
```

D1/D2 prove the two layers are distinct: **integrity validation != current
deployment authority validation**. The drift evidence is a fault-injection fixture
only; ModelRelease immutability semantics are not weakened.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_plan_b_certificate_flow.py`:

```text
1. test_b3_plan_resolves_expected_paths: B3 root and evidence dirs live under PLAN_B_ROOT
2. test_integrity_tampered_evidence_rejected (I1, I2): tampered/vacuous evidence fails
   integrity validation -> QUALIFICATION_EVIDENCE_INVALID
3. test_authority_drift_runtime_ref_fails_closed (D1): valid stale evidence (SHA recomputed)
   + CURRENT provider runtime_ref drift -> install fails closed; no operator certificate written
4. test_authority_drift_manifest_sha_fails_closed (D2): valid stale evidence (SHA recomputed)
   + CURRENT ModelRelease manifest SHA drift -> install fails closed; no certificate written
5. test_negative_wrong_executor_fails_closed (D3)
6. test_negative_wrong_runtime_family_fails_closed (D4, isolated env)
7. test_failed_cuda_probe_fails_closed (D5)
8. test_only_install_module_writes_operator_store (D6, source scan)
9. test_duplicate_conflict_fails_closed (D7)
10. test_repo_certificates_unchanged_by_install (already_certified writes nothing)
```

The deterministic negatives are CPU-only; the real doctor/qualify run is opt-in:

```text
11. test_b3_real_runtime_doctor_and_qualify  (gated)
   - skip unless WSP_PLAN_B_REAL_GPU=1 and the ML interpreter + CUDA are present
   - run scripts/plan_b_certificate_flow.py --real
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_certificate_flow.py -q
```

Expected RED cause: `ModuleNotFoundError: No module named 'plan_b_certificate_flow'`.

- [ ] **Step 3: Minimal implementation + real run**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_certificate_flow.py -q
WSP_PLAN_B_REAL_GPU=1 PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
  scripts/plan_b_certificate_flow.py --real
```

Expected: doctor `identity_status == "match"`; both `qualify` runs write passing `local_gpu_cuda_v1` evidence with `identity_scheme == "bhq3_gpu_v1"`; both installs report `already_certified`; the repo certificate file hash is unchanged. Artifact: `/root/autodl-tmp/plan_b_qual/evidence/b3_certificate_flow.json`.

- [ ] **Step 4: Commit**

```bash
git add scripts/plan_b_certificate_flow.py backend/tests/test_plan_b_certificate_flow.py
git commit -m "test: add plan b certificate evidence flow"
```

**Review checkpoint (B3):** doctor reports `bhq3_gpu_v1` / `match`; no generic certificate; repo-default certificates untouched; negative cases fail closed.

---

# Task H2 — CPN ×16 DatasetExperiment, concurrency=1

**GPU REQUIRED: YES — exactly 16 real local_gpu executions.**

**Files**
- New: `scripts/plan_b_dataset_core.py` (shared registration + subset + polling + evidence helpers)
- New: `scripts/plan_b_h2_cpn.py`
- New: `backend/tests/test_plan_b_h2_subset.py`

**Exact input set**

```text
dataset            SpaceNet / test / spacenet_14, root /root/autodl-tmp/SpaceNet_Dataset/advanced
stems (frozen)     0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280   (16)
executor           local_gpu
model              cpn_bandwidth_tier / 1.0.0 / golden
asset manifest     7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb
concurrency        max_concurrency = 1
expected executions 16
```

**Dedicated DB / work root**

```text
/root/autodl-tmp/plan_b_qual/h2h3/qual.db
/root/autodl-tmp/plan_b_qual/h2h3/data
/root/autodl-tmp/plan_b_qual/h2h3/work
```

Registration uses existing external-path semantics (`RecordingModel.external_path`, no IQ copy; `GroundTruthModel` rows) for exactly the frozen 16 stems, idempotently; `platform.db` and all historical DBs are untouched.

**Shared core (`scripts/plan_b_dataset_core.py`)**

```text
ensure_dataset(session) -> registers exactly H2_STEMS into the dedicated DB;
                          fails closed if any stem is missing/invalid/zero-GT;
                          verifies the manifest: expected_recordings == 16 and
                          deterministic manifest_order
create_run_experiment(client, plugin_id, plugin_version, release, concurrency) -> experiment_id
run_and_wait(client, experiment_id, deadline_s) -> terminal experiment JSON
assert_h2_acceptance(experiment, items, evaluation, membership) -> evidence dict
  checks: 16 items; all terminal; status == "completed"; executor == "local_gpu";
          each item completed with exactly one Attempt (attempt_number == 1);
          runtime_descriptor_json.executor == "local_gpu",
          device_type == "cuda", precision == "float16",
          environment_label == GPU_RUNTIME_REF;
          model_release_id == "golden"; asset_manifest_sha256 == CPN manifest;
          evaluation.evaluated_recordings == 16, missing == 0, coverage == 1.0,
          comparable == true;
          aggregate_metrics_json["localization"]["ap50"]/["ap50_95"] numeric;
          aggregate_metrics_json["classification_on_matched"] is None (CPN N/A,
          reason label_space_mismatch), ["class_aware"] is None;
          no duplicate launches; zero orphan workers after terminal
```

**Pre-run gate (before the first launch)**

```text
nvidia-smi compute-apps empty (0 GPU processes)
gpu_memory_baseline_mib = measured nvidia-smi memory.used (quiescent pre-campaign)
require_memory_admission() returns an admitted mode (Amendment A1 two-path)
free disk on /root/autodl-tmp >= 5 GiB
```

**Abort conditions (whole campaign)**

```text
cgroup memory.events oom/oom_kill/max delta > 0
dangerous committed-floor growth (monitor trips)
GPU_MEMORY_NOT_QUIESCENT (Global Constraint 19: compute-apps empty and
  memory.used > gpu_memory_baseline_mib + 64 MiB for 60 s)
> 1 live app.analysis.local_inference_worker for max_concurrency == 1
disk free < 2 GiB
any qualification process escapes PLAN_B_ROOT work dirs into an unrelated DB
```

**Post-run orphan check / GPU quiescence**

```text
no app.analysis.local_inference_worker PIDs; no app.dataset_experiments.worker PIDs
for this experiment; nvidia-smi --query-compute-apps empty
and within 60 s memory.used <= gpu_memory_baseline_mib + 64 MiB (Global Constraint 19)
```

**Acceptance (exact)**

```text
16 intended recordings; all terminal; evaluation completed
evaluated == 16; missing == 0; coverage == 1.0
localization AP metrics present; CPN classification N/A reason preserved
stable recording_manifest_hash across a re-prepare
no duplicate launches; no orphan workers; exact executor/runtime/model provenance
GPU quiescence held after the experiment (Global Constraint 19)
```

**Evidence artifact**

```text
/root/autodl-tmp/plan_b_qual/evidence/h2_cpn.json
```

- [ ] **Step 1: Write failing test**

`backend/tests/test_plan_b_h2_subset.py`:

```text
1. test_h2_stems_exact_and_unique == the 16-tuple, distinct, parseable as SpaceNet test stems
2. test_h2_subset_registration_deterministic (CPU only; temp DB):
   register -> prepare_manifest -> expected_recordings == 16 and manifest_order is
   the canonical order; re-prepare -> identical recording_manifest_hash
3. test_h2_acceptance_asserts_reject_bad_inputs: a fake experiment with 15 items,
   a failed item, coverage 0.9, or classification_applicable True is rejected
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h2_subset.py -q
```

Expected RED cause: `ModuleNotFoundError: No module named 'plan_b_dataset_core'`.

- [ ] **Step 3: Minimal implementation**

Implement `scripts/plan_b_dataset_core.py` and `scripts/plan_b_h2_cpn.py`. The H2 script calls `bootstrap_env_before_app_import(h2h3_root)`, then `create_app`, registers, creates the CPN experiment (manual `local_gpu`, c=1), starts it, polls to terminal, and writes evidence.

- [ ] **Step 4: Run GREEN + real campaign**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h2_subset.py -q
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_h2_cpn.py
```

Expected: 16 completed items, evaluation `coverage == 1.0`, evidence written. Gate/abort/orphan checks enforced. The H2 experiment and its linked evaluation remain in `/root/autodl-tmp/plan_b_qual/h2h3/qual.db` for reuse as H3 Experiment A.

- [ ] **Step 5: Commit**

```bash
git add scripts/plan_b_dataset_core.py scripts/plan_b_h2_cpn.py \
  backend/tests/test_plan_b_h2_subset.py
git commit -m "test: add plan b h2 cpn dataset qualification"
```

**Review checkpoint (H2):** exactly 16 GPU executions; no orphan workers; evaluation coverage 1.0; CPN classification N/A reason `label_space_mismatch` preserved; no historical DB touched; the campaign is not re-run in H3.

---

# Task H3 — ZoomSpec ×16 + same-manifest multi-model comparison

**GPU REQUIRED: YES — exactly 16 real local_gpu executions.**

**Files**
- New: `scripts/plan_b_h3_zoomspec.py`
- New: `backend/tests/test_plan_b_h3_compare.py`

**Exact input set**

```text
dataset            same frozen 16-stem membership as H2 (no new manifest)
Experiment A       H2 CPN local_gpu result (reused, NOT re-run)
Experiment B       zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0 / golden / local_gpu
asset manifest     16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08
concurrency        max_concurrency = 1
expected executions 16
```

**Dedicated DB / work root:** the **same** `/root/autodl-tmp/plan_b_qual/h2h3/` DB used by H2, so A and B share the manifest.

**Required equalities / provenance**

```text
A.recording_manifest_hash == B.recording_manifest_hash
identical membership (16 recording_ids) and identical manifest_order
A.asset_manifest_sha256 == CPN manifest; B.asset_manifest_sha256 == ZoomSpec manifest
A.executor == B.executor == "local_gpu"; both runtime_descriptor_json match the provider
both coverage == 1.0; both comparable == true
```

**Comparison (existing flow, semantics unchanged)**

```text
POST /api/dataset-benchmarks/compare {evaluation_a_id: H2 eval, evaluation_b_id: H3 eval}
  -> comparable == true; reasons == []
  -> localization_ap50 / localization_ap50_95 numeric
  -> class_aware_map50 / class_aware_map50_95 / matched_accuracy deltas null
     (CPN classification is N/A; ZoomSpec is classification-applicable)
per recording: GET /api/dataset-benchmarks/{eval}/items
              POST /api/algorithm-lab/compare {recording_id, run_a_id, run_b_id}
  -> localization + cases present; class metrics N/A for the CPN side only
```

Do not change comparison semantics because CPN classification is not SpaceNet-14 applicable.

**Pre-run gate / abort conditions / orphan check:** identical to H2.

**Acceptance (exact)**

```text
16 intended recordings; all terminal; evaluation completed; coverage == 1.0
A.manifest_hash == B.manifest_hash; membership and order identical
separate exact ModelRelease/AssetManifest provenance
compare(A,B): comparable true; localization deltas numeric; N/A deltas null
per-recording algorithm-lab comparison returns cases without error
no duplicate launches; no orphan workers; exact executor/runtime/model provenance
GPU quiescence held after the experiment (Global Constraint 19)
```

**Evidence artifact:** `/root/autodl-tmp/plan_b_qual/evidence/h3_zoomspec.json` (includes the compare response and a per-recording comparison summary).

- [ ] **Step 1: Write failing test**

`backend/tests/test_plan_b_h3_compare.py`:

```text
1. test_manifest_equality_gate: two fake evaluations with differing
   recording_manifest_hash -> the H3 guard rejects before compare
2. test_compare_deltas_contract: given a CPN-like aggregate
   (classification_on_matched == None, class_aware == None) and a ZoomSpec-like
   aggregate, the extracted deltas have numeric localization_ap50/ap50_95 and
   None class_aware_map50/map50_95/matched_accuracy
3. test_h3_reuses_h2_experiment_a: the H3 driver refuses to create a new CPN
   experiment and resolves Experiment A from the existing DB
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h3_compare.py -q
```

Expected RED cause: `ModuleNotFoundError: No module named 'plan_b_h3_zoomspec'`.

- [ ] **Step 3: Minimal implementation + real campaign**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h3_compare.py -q
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_h3_zoomspec.py
```

Expected: 16 completed items; coverage 1.0; manifest equal to H2; `compare` comparable with `reasons == []`. Evidence written.

- [ ] **Step 4: Commit**

```bash
git add scripts/plan_b_h3_zoomspec.py backend/tests/test_plan_b_h3_compare.py
git commit -m "test: add plan b h3 zoomspec multi-model qualification"
```

**Review checkpoint (H3):** exactly 16 GPU executions; Experiment A is the H2 result (not a new CPN run); the manifest is not rebuilt; comparison semantics unchanged.

---

# Task H4 — local_gpu crash / recovery matrix

**GPU REQUIRED: partial — the deterministic CPU/DB cases use 0 GPU executions; genuine live worker kills use ~2–4.**

**Files**
- New: `scripts/plan_b_h4_recovery.py`
- New: `backend/tests/test_local_gpu_recovery.py` (deterministic, control-plane only)
- New: `backend/tests/test_local_gpu_crash_recovery.py` (gated real GPU)

**Approved recovery matrix (A1 architecture reused; no redesign)**

| Case | Setup | Expected | GPU |
|---|---|---|---|
| H4-A local_gpu running interrupted at startup | seed `running` local_gpu run; `create_app` recovery | `interrupted`/`ANALYSIS_INTERRUPTED`; never permanently running | 1 live |
| H4-A2 local_cpu parity | same | unchanged `ANALYSIS_INTERRUPTED` | 0 |
| H4-A3 remote_gpu untouched | remote pending/running, no config | untouched; no coordinator | 0 |
| H4-B experiment crash mid-item | item1/2 completed, item3 running, item4.. queued; kill worker; restart | item3 → failed (from interrupted run); queued resumes; completed never rerun; attempts preserved | 1 live |
| H4-C launch boundary (no intent) | attempt `launch_requested_at IS NULL`, no worker | relaunch same attempt/run via CAS; no double launch | 0 |
| H4-C launch boundary (intent present, pending, no worker) | marker set; run pending; no PID | fail-closed `pending→interrupted` `ANALYSIS_LAUNCH_AMBIGUOUS`; item failed | 0 |
| H4-C2 provider disappeared | provider removed before launch | deterministic fail-close (P3); no false launch claim; no stall | 0 |
| H4-D repeated recovery | `recover_dataset_experiments` twice | idempotent; no duplicate attempts/runs/tokens | 0 |
| H4-E stale coordinator fencing | old token after rotation | zero-row CAS; `DATASET_EXPERIMENT_FENCE_LOST`; no writes | 0 |
| H4-F retry_failed | `completed_with_failures` | `→running` CAS; failed→queued; new attempt_number; spawn compensation restores | 0 |
| H4-G evaluation recovery | evaluating + orphaned pending+PID eval | normalize→interrupted; restart; no AnalysisRuns | 0 |

Existing A1 tests already cover local_cpu scope: `test_dataset_experiment_local_launch_recovery.py`, `test_local_executor_recovery_boundary.py`, `test_dataset_experiment_restart_recovery.py`, `test_dataset_experiment_evaluating_recovery.py`, `test_dataset_experiment_launch_concurrency.py`. H4 adds the **local_gpu** parameterization and the two genuine live-kill cases; it does not modify A1 code unless a live defect is reproduced (then STOP and record it).

**Dedicated DB / work root**

```text
/root/autodl-tmp/plan_b_qual/h4/qual.db
/root/autodl-tmp/plan_b_qual/h4/data
/root/autodl-tmp/plan_b_qual/h4/work
```

**Safe kill targeting (mandatory)**

```text
verify /proc/<pid>/cmdline contains exactly
  app.analysis.local_inference_worker <exact run_id>
for a coordinator:
  app.dataset_experiments.worker <exact experiment_id> --coordinator-token <exact token>
kill only that verified PID; never kill unrelated processes
SIGKILL for a genuine crash (H4-A/H4-B); never for recovery tests that only need restart
```

**Pre-run gate:** same as H2 for the two live-kill cases only. The deterministic cases run with 0 GPU and still require the A1 admission check before any subprocess spawn.

**Abort conditions:** same as H2, plus "any signal delivered to a PID whose argv identity was not verified" is a hard abort.

**Post-run orphan check:** zero `app.analysis.local_inference_worker` and `app.dataset_experiments.worker` PIDs for the H4 DB, and GPU quiescence (Global Constraint 19) after any live-kill case.

**Acceptance (exact)**

```text
completed work not rerun
running victim terminalized / fails as designed
queued work resumes where required
attempt history preserved (no deleted/shifted attempt_number)
no duplicate run for any item
no stale coordinator write after fencing
no orphan GPU workers after quiescence
```

**Evidence artifact:** `/root/autodl-tmp/plan_b_qual/evidence/h4_recovery.json`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_local_gpu_recovery.py` (deterministic; mirror the existing A1 local_cpu tests parameterized to `local_gpu`):

```text
test_local_gpu_running_run_interrupted_at_startup
test_local_gpu_pending_no_intent_repairs_first_launch
test_local_gpu_pending_with_intent_fails_closed_ambiguous
test_local_gpu_provider_disappearance_fails_closed
test_local_gpu_repeated_recovery_is_idempotent
test_local_gpu_stale_coordinator_fencing_zero_row_cas
test_local_gpu_retry_failed_requeues_only_failed
test_local_gpu_evaluation_recovery_no_analysis_runs
```

`backend/tests/test_local_gpu_crash_recovery.py` (gated):

```text
test_real_local_gpu_worker_kill_startup_recovery   (skip unless WSP_PLAN_B_REAL_GPU=1)
test_real_local_gpu_experiment_mid_item_crash      (skip unless WSP_PLAN_B_REAL_GPU=1)
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_local_gpu_recovery.py backend/tests/test_local_gpu_crash_recovery.py -q
```

Expected RED cause: the new modules do not exist (tests collect as errors / the local_gpu parameterization is absent).

- [ ] **Step 3: Minimal implementation + real kills**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_local_gpu_recovery.py -q
WSP_PLAN_B_REAL_GPU=1 PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
  scripts/plan_b_h4_recovery.py --live
```

Expected: all deterministic cases green with 0 GPU; the two live cases kill exactly the verified qualification worker and recovery matches the matrix. Evidence written.

- [ ] **Step 4: Commit**

```bash
git add scripts/plan_b_h4_recovery.py \
  backend/tests/test_local_gpu_recovery.py \
  backend/tests/test_local_gpu_crash_recovery.py
git commit -m "test: add plan b h4 local_gpu recovery qualification"
```

**Review checkpoint (H4):** ~2–4 real GPU executions only; every signal targets a verified PID; A1 code unchanged; no orphan workers.

---

# Task H5 — concurrency=2 + endurance 40 (combined)

**GPU REQUIRED: YES — exactly 40 real CPN local_gpu executions at max_concurrency=2.**

**Files**
- New: `scripts/plan_b_h5_concurrency_endurance.py`
- New: `backend/tests/test_plan_b_h5_contract.py`

**Exact workload**

```text
plugin             cpn_bandwidth_tier / 1.0.0 / golden
executor           local_gpu
max_concurrency    2
workload           16 + 16 + 8 = 40 executions over the frozen 16-stem pool
                   (cycle 1 = 16 stems; cycle 2 = 16 stems; cycle 3 = first 8 stems)
expected executions 40
```

**Dedicated DB / work root**

```text
/root/autodl-tmp/plan_b_qual/h5/qual.db
/root/autodl-tmp/plan_b_qual/h5/data
/root/autodl-tmp/plan_b_qual/h5/work
```

Three sequential DatasetExperiments in one dedicated DB (16, 16, 8 items) — this is the concurrency + endurance workload; there is no separate 8-item concurrency experiment.

**Concurrency observation cadence (first controlled section, at least the first 16 executions)**

```text
concurrency sampling interval <= 0.5 s (preferred 0.25–0.5 s)
at each sample capture:
  - qualification-owned app.analysis.local_inference_worker PID set + verified
    /proc/<pid>/cmdline run_id
  - nvidia-smi --query-compute-apps=pid,used_memory PIDs
  - DB running/pending run + attempt ownership state
  - timestamp
```

**Concurrency section must prove**

```text
max observed qualification worker count <= 2 (over ALL high-frequency samples)
no sample contains > 2 qualification compute workers
no DB evidence of > 2 simultaneously-owned live executions
every observed GPU compute PID maps to an expected qualification worker
all intended items terminal; exactly one logical AnalysisRun per intended execution
no duplicate attempt ownership outside intentional retry
no orphan workers after each experiment
```

If any high-frequency sample shows > 2 qualification workers:

```text
CONCURRENCY_BOUND_EXCEEDED -> abort H5
```

**Endurance section (remaining executions) continues monitoring to 40.** Expensive
resource-trend telemetry may drop to ~5 s once the concurrency contract is
established; lightweight PID/compute-app checks continue at the high-frequency
cadence whenever cheap, and the `<= 2` worker assertion is never weakened.

**Observation cadence (split)**

```text
concurrency proof (first section): PID / compute-app / DB ownership sampling <= 0.5 s
endurance resource trend: ~5 s plus before/after each experiment
  worker PID set/lifetimes; /proc/<pid>/status VmHWM
  nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu
  nvidia-smi --query-compute-apps=pid,used_memory
  cgroup memory.current, committed_floor, effective_headroom, memory.stat,
    memory.pressure, memory.events
  DB status/attempt/run counts
```

**Pre-run gate:** identical to H2 (0 GPU processes, Amendment-A1 admission, disk >= 5 GiB).

**Abort conditions**

```text
memory.events max/oom/oom_kill delta > 0
persistent one-way committed-floor growth (monitor trips)
> 2 qualification workers at any high-frequency sample -> CONCURRENCY_BOUND_EXCEEDED
GPU_MEMORY_NOT_QUIESCENT (Global Constraint 19: compute-apps empty and
  memory.used > gpu_memory_baseline_mib + 64 MiB for 60 s) -> abort
disk free < 2 GiB
qualification process escape (unexpected DB/PID)
```

No cache-clearing hacks; no `drop_caches`; no killing unrelated processes; page cache is never treated as process RSS.

**Acceptance (exact)**

```text
all intended items terminal; 0 failed
max simultaneously live local_inference_worker PIDs <= 2 (high-frequency samples)
no high-frequency sample exceeded 2 qualification compute workers
exactly one logical AnalysisRun per intended execution
no duplicate attempts outside intentional retry/crash cases
0 orphan qualification worker PIDs; 0 remaining qualification GPU compute processes
memory.events.max delta == 0; oom == 0; oom_kill == 0
0 SQLite "database is locked" failures
all evaluations coverage == 1.0
GPU quiescence (Global Constraint 19) held after every experiment and at campaign end
leak analysis across cycles: page cache vs committed process memory vs worker RSS vs
  GPU memory separated; no one-way growth trend
```

**Evidence artifact:** `/root/autodl-tmp/plan_b_qual/evidence/h5_concurrency_endurance.json` (per-experiment summary, the high-frequency concurrency samples summarized with a verifiable max count, and the raw high-frequency sample artifact alongside it, e.g. `h5_concurrency_samples.jsonl`).

- [ ] **Step 1: Write failing test**

`backend/tests/test_plan_b_h5_contract.py`:

```text
1. test_h5_cycles_partition_40: 16 + 16 + 8 == 40 and cycle 3 is the first 8 H5_STEMS
2. test_h5_concurrency_bound_check: a fake high-frequency sample with 3 live workers
   is rejected (CONCURRENCY_BOUND_EXCEEDED); 2 is accepted; 1 is accepted; the
   max over a sample sequence is what is asserted
3. test_h5_quiescence_rule: compute-apps empty + memory.used baseline+63 MiB accepted;
   compute-apps empty + baseline+65 MiB for > 60 s -> GPU_MEMORY_NOT_QUIESCENT
4. test_h5_terminal_acceptance_rejects_failures: failed item, coverage < 1.0,
   or oom delta > 0 is rejected
```

- [ ] **Step 2: Run and confirm RED**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h5_contract.py -q
```

Expected RED cause: `ModuleNotFoundError: No module named 'plan_b_h5_concurrency_endurance'`.

- [ ] **Step 3: Minimal implementation + real campaign**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_plan_b_h5_contract.py -q
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
  scripts/plan_b_h5_concurrency_endurance.py
```

Expected: 40 completed executions, `max_live_workers <= 2`, zero oom/oom_kill, coverage 1.0 per experiment, no orphans. Evidence written.

- [ ] **Step 4: Commit**

```bash
git add scripts/plan_b_h5_concurrency_endurance.py backend/tests/test_plan_b_h5_contract.py
git commit -m "test: add plan b h5 concurrency endurance qualification"
```

**Review checkpoint (H5):** exactly 40 GPU executions; concurrency bound proven; no lock failures; no orphan workers; resource trend auditable.

---

# Task B-final — Plan-B evidence synthesis, regression, audit

**GPU REQUIRED: NO (no new executions).**

**Files**
- New: `docs/superpowers/acceptance/2026-09-15-backend-v1-plan-b-local-gpu-qualification.md`
- New: `scripts/plan_b_final_audit.py`

**Evidence synthesis (document)**

```text
environment + GPU + ML runtime + control plane (from the preflight baseline)
BHQ3 identity: material, generation 7b958347b5af, match
runtime doctor: bhq3_gpu_v1 / match
B3 qualification evidence + already_certified idempotency
H2 CPN ×16 (c=1; also H3 Experiment A): evaluation coverage 1.0, AP present, CPN N/A
H3 ZoomSpec ×16: manifest equality, compare comparable, deltas contract
H4 recovery matrix results + the two genuine crash cases
H5 40-run concurrency/endurance: max live workers, oom deltas 0, leak analysis
resource/orphan audit
local_gpu real GPU execution budget accounting (~74–76)
known limitations: local_gpu crash recovery scope; mixed-precision meaning of "float16";
  OMP/MKL scoped override; Plan C not yet done
explicit statement: this is the Plan-B local-GPU qualification result, NOT the
  Backend V1 seal; the GPU remains ON for Plan C
```

**Focused regression**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_runtime_identity.py \
  backend/tests/test_runtime_identity_gpu_collector.py \
  backend/tests/test_runtime_doctor.py \
  backend/tests/test_runtime_qualification.py \
  backend/tests/test_runtime_qualification_integration.py \
  backend/tests/test_certificate_install.py \
  backend/tests/test_cli.py \
  backend/tests/test_execution_certificate.py \
  backend/tests/test_executor_registry.py \
  backend/tests/test_local_worker_provider.py \
  backend/tests/test_local_gpu_pre_cert_gate.py \
  backend/tests/test_local_gpu_certificates_exact.py \
  backend/tests/test_local_gpu_recovery.py \
  backend/tests/test_local_gpu_acceptance.py \
  backend/tests/test_cpn_bandwidth_tier_plugin.py \
  backend/tests/test_zoomspec_plugin.py \
  backend/tests/test_dataset_experiment_local_launch_recovery.py \
  backend/tests/test_local_executor_recovery_boundary.py \
  backend/tests/test_dataset_experiment_restart_recovery.py \
  backend/tests/test_dataset_experiment_evaluating_recovery.py \
  backend/tests/test_dataset_experiment_launch_concurrency.py \
  backend/tests/test_plan_b_env.py \
  backend/tests/test_plan_b_certificate_flow.py \
  backend/tests/test_plan_b_h2_subset.py \
  backend/tests/test_plan_b_h3_compare.py \
  backend/tests/test_plan_b_h5_contract.py -q
```

Expected: `0 failed`, `0 errors`.

**Full regression**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

Expected: `0 failed`, `0 errors` (hardware-gated tests skip unless their opt-in env vars are set). Record passed/skipped/failed/errors.

**Control-plane ML-free boundary**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -c \
"import sys; import app.analysis.local_executor; import app.runtime_qualification.identity; import app.cli; assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules; print('CONTROL_PLANE_ML_FREE_OK')"
"$PWD/.venv/bin/python" -c "import importlib.util as u; assert u.find_spec('torch') is None; assert u.find_spec('ultralytics') is None; print('VENV_NO_ML_OK')"
```

**Resource / orphan audit (`scripts/plan_b_final_audit.py`)**

```text
no app.analysis.local_inference_worker PIDs
no app.dataset_experiments.worker PIDs for Plan-B DBs
nvidia-smi --query-compute-apps empty; memory.used <= gpu_memory_baseline_mib + 64 MiB
  (Global Constraint 19; no GPU_MEMORY_NOT_QUIESCENT)
cgroup memory.events oom/oom_kill/max delta == 0 across the campaign window
all Plan-B evidence JSON present under /root/autodl-tmp/plan_b_qual/evidence
historical DBs and repo certificates hash-unchanged
no required state exists only in /tmp (persist under /root/autodl-tmp/plan_b_qual)
```

- [ ] **Step 1: Run focused + full regression and the audit**

```bash
PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" scripts/plan_b_final_audit.py
```

- [ ] **Step 2: Write the acceptance evidence document** with all synthesized results.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/acceptance/2026-09-15-backend-v1-plan-b-local-gpu-qualification.md \
  scripts/plan_b_final_audit.py
git commit -m "docs: add plan b local gpu qualification evidence"
```

**Review checkpoint (B-final):** regression green; audit clean; GPU still ON; `BACKEND_V1_SEAL_SHA` NOT created; document states Plan C is next.

---

## Plan self-review

1. **GPU collector reproduces `7b958347b5af`.** B0 derives the recorded BHQ3 fixture exactly and the opt-in real-interpreter test asserts the sealed ref. ✅
2. **CPU identity behavior unchanged.** `LOCAL_CPU_V1_FIELDS`, canonicalization, and derivation are untouched; only the call site now passes `scheme=LOCAL_CPU_V1`. ✅
3. **Control plane stays ML-free.** B0/B1 import no torch; the modules still only spawn the ML interpreter; B-final asserts `torch`/`ultralytics` absent. ✅
4. **Runtime family authority remains exact.** The GPU probe requires `family == settings.runtime_family` and `kind == "gpu"`; wrong family is rejected (N3). ✅
5. **No generic local_gpu certificate.** The certificate remains the exact 7-field key built from exact evidence identity; no widened tuple, no plugin self-certification. ✅
6. **Qualification evidence binds exact runtime/release/manifest.** `evidence.runtime_ref`, `runtime_descriptor`, `model_release_id`, and `asset_manifest_sha256` are validated against `LiveAuthority` at install. ✅
7. **Repo-default exact cert idempotency preserved.** B3 expects `already_certified` and asserts the store file is byte-identical after install; duplicate exact-key semantics unchanged. ✅
8. **H2 CPN is run only once.** H2 is the single CPN ×16 campaign; no separate dataset campaign or concurrency baseline exists. ✅
9. **H3 reuses H2 Experiment A.** H3 only adds the ZoomSpec experiment and reuses the existing CPN experiment/evaluation from the same DB. ✅
10. **H5 uses the combined 40-run concurrency/endurance campaign.** 16+16+8 at c=2; no separate 8-item concurrency experiment. ✅
11. **Real crash GPU budget is only ~2–4.** H4-A and H4-B are the only live kills; all other H4 cases are CPU/DB-only. ✅
12. **Total real local_gpu budget remains ~74–76.** 16 + 16 + 2–4 + 40. ✅
13. **No historical DB is modified.** All campaigns use dedicated DBs under `/root/autodl-tmp/plan_b_qual`; historical DBs are read-only. ✅
14. **No training.** No training/eval-training path is invoked. ✅
15. **No frontend.** No frontend files or endpoints change. ✅
16. **Plan C remains next while GPU stays ON.** The plan ends with the GPU on and Plan C/H5.5-S next; no shutdown. ✅
17. **No final Backend seal.** `BACKEND_V1_SEAL_SHA` is not created and not implied. ✅
18. **Agent-reported server measurements are distinguished from source-defined acceptance.** The preflight baseline is labeled as the current measured baseline; acceptance assertions are defined in code/plan, not in prose claims. ✅
19. **No global package/environment mutation.** No package install/upgrade; only an in-process, scoped `OMP_NUM_THREADS`/`MKL_NUM_THREADS` override in acceptance tooling. ✅
20. **No secrets committed.** Tooling uses interpreter/asset paths only; no keys/tokens; tests assert no secret literals. ✅

Additional invariants preserved: no A1/A2/A3 redesign; no executor substitution/fallback; no DB migration; no frozen-science change; exact certificate semantics; public API exposes no qualification internals; Plan B does not create `BACKEND_V1_SEAL_SHA` and does not shut down the GPU.

### Corrective-pass self-review (final)

1. **No `"unknown"` can enter BHQ3 canonical material.** The GPU path has no `"unknown"` fallback; `assemble_bhq3_gpu_material` rejects `"unknown"`, empty, multi-line, or whitespace-only driver output and any missing/invalid field with `RUNTIME_IDENTITY_UNAVAILABLE`. ✅
2. **CUDA unavailable cannot produce GPU identity.** The torch probe reports `ok=False` for `cuda_available` False or `device_count < 1`, and the assembler requires `cuda_available is True` and `device_count >= 1`; no material is derived. ✅
3. **Driver query failure produces unavailable.** Nonzero returncode, missing `nvidia-smi`, empty output, multi-line output, and `"unknown"` all fail closed via the collector/assembler. ✅
4. **`nvidia-smi` portability boundary is internally consistent.** Constraint 1 permits the fixed platform-owned `bhq3_gpu_v1` identity/doctor probe (and best-effort diagnostics) while forbidding `nvidia-smi` in Auto policy, core executor selection, `local_cpu` identity, and generic startup; `/proc`/cgroup/telemetry stay in `scripts/`. ✅
5. **CPU identity remains unchanged.** `LOCAL_CPU_V1_FIELDS`, canonicalization, and derivation are untouched; only the call site passes `scheme=LOCAL_CPU_V1`. ✅
6. **H5 concurrency proof cannot rely on 5-second-only sampling.** The first controlled section samples PID/compute-app/DB ownership at `<= 0.5 s` (preferred 0.25–0.5 s); the `<= 2` assertion is over all high-frequency samples, with `CONCURRENCY_BOUND_EXCEEDED` on any sample > 2. ✅
7. **Quiescent GPU memory rule has an exact threshold.** Global Constraint 19 defines measured `gpu_memory_baseline_mib`, empty compute-apps, `memory.used <= baseline + 64 MiB` within 60 s, else `GPU_MEMORY_NOT_QUIESCENT`; referenced by H2/H3/H4/H5/B-final. ✅
8. **B3 separately tests integrity tampering and valid-but-stale authority drift.** I1/I2 cover tampering/vacuity; D1/D2 build internally valid, correctly re-hashed stale evidence and assert install fails closed against current `LiveAuthority`, writing no certificate; D3–D7 cover the remaining negatives. ✅
9. **B2 environment-key description is exact.** The plan enumerates the 9 `PLAN_B_WSP_KEYS` and no longer asserts a numeric count. ✅
10. **Only the Plan-B plan document changed.** No production code, tests, certificates, or GPU execution. ✅

**Fault-injection STOP rules (recorded):** if a live recovery defect is reproduced in A1 code, STOP, do not widen scope, and record it; if any real GPU execution outside the budget is needed, justify it individually; if a memory/gpu/disk abort condition trips, STOP the campaign and write the partial evidence with the abort reason.
