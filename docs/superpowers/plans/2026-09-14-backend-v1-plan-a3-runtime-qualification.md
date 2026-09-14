# Backend V1 Plan A3 Runtime Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the operator-facing per-installation runtime qualification path — `runtime doctor` → runtime identity → small qualification → immutable evidence → explicit `certificate install` → exact `ExecutionCertificate` — so that a plugin/runtime/executor becomes certified only after explicit qualification evidence plus an explicit operator-owned install step. Wire it to the existing certificate store so A2 `certified` and A1 exact authority pick it up on the next control-plane startup with no policy change.

**Architecture:** A new torch-free, portable `backend/app/runtime_qualification/` package owns the doctor report, the scheme-versioned runtime-identity derivation/validation, the qualification-evidence schema and Windows-safe atomic storage, the qualification runner framework, and the operator-owned certificate install/store seam. A single `backend/app/cli.py` (stdlib `argparse`) exposes `wisa runtime doctor`, `wisa qualify`, `wisa certificate install`, `wisa certificate list`. No existing authority is redesigned: `RuntimeDescriptor`, `ExecutionCertificate`, `ExecutionCertificateStore`, `ExecutorRegistry`, A1 recovery, and A2 Auto policy are consumed unchanged. The only authority for runtime identity remains `provider.runtime_ref`; the identity module is a scheme-versioned derivation/validation helper, never a second identity system, and it must reproduce existing sealed runtime identities exactly rather than invalidate them.

**Tech Stack:** Python 3.12, stdlib `argparse`/`hashlib`/`json`/`os`/`pathlib`/`tempfile`/`subprocess`, Pydantic v2, pytest (control-plane venv is ML-free).

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

## Global Constraints

- **GPU REQUIRED: NO** for every task. No CUDA, no AutoDL, no server, no real model inference. All A3 control-plane behavior is testable with deterministic/injected probes and SQLite/filesystem only. Real GPU/remote qualification campaigns belong to Plans B/C.
- **No redesign of the authority model.** `PluginDefinition`, `ModelRelease`, `ExecutorRegistry`, `ExecutionCertificate`, `RuntimeDescriptor`, A1 recovery, and A2 Auto policy are consumed unchanged. A3 supplies/changes only the certificate store contents behind `ExecutionCertificateStore`.
- **One runtime-identity authority, scheme-versioned.** `provider.runtime_ref` (deployment-owned) remains authoritative. Identity schemes are versioned labels (`bhq3_gpu_v1`, `local_cpu_v1`, `legacy_opaque`) that describe how a generation is derived; the scheme label does not appear in the `runtime_ref` string. A3 never overrides a provider's `runtime_ref` and never retroactively invalidates sealed repo-default runtime refs.
- **Windows-safe storage.** A raw `runtime_ref` contains `:` and is never used as a filesystem directory component. Evidence storage uses a hashed storage key (see below).
- **Exact certificate semantics preserved.** The 7-field key `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, runtime_ref)` and exact matching are not weakened. No generic "any local_gpu/CUDA/Windows" certificates.
- **No plugin self-certification.** Certificates are produced only by the platform CLI from validated qualification evidence on explicit operator action, after re-resolving the CURRENT live deployment authority. Qualification passing alone never installs a certificate.
- **Fail closed.** Runtime/plugin/release/executor mismatch, provider/technical-capability absence, failed/ineligible/empty qualification, stale/malformed/tampered evidence, and hash mismatch all block installation.
- **Evidence sufficiency.** Evidence must contain at least one result; `passed` is derived, never trusted; the qualification type must be install-eligible for the executor.
- **SHA = integrity, not authenticity.** Evidence and certificate hashes are local integrity fingerprints; A3 does not claim external signer authenticity and introduces no PKI.
- **No DB migration.** Operator-owned state is local files (matching the existing file-based certificate store). Qualification evidence lives under `<data_root>/qualification/…` and is never committed.
- **No hot reload.** Installing a certificate updates the durable store; it becomes effective on the NEXT control-plane startup / explicit registry rebuild. A3 does not implement live reload and does not claim a running process sees it immediately.
- **Portable.** Windows and Linux, CPU-only, no cgroup/`/proc` dependency defining identity. Optional GPU diagnostics may use `nvidia-smi` when present; its absence must not break a CPU runtime doctor.
- **Public/private boundary preserved.** CLI output is operator-facing; no new HTTP API is added; browser APIs never expose interpreter paths, `environment_ref`, qualification filesystem paths, or certificate internals.
- **No frontend, no science/model code, no A3/B-C scope leakage.**
- Every production task is RED → GREEN and uses the existing local control-plane venv on Windows.

### Runtime identity contract (single authority, scheme-versioned)

`runtime_ref` is an operator-owned, per-installation immutable generation identity for one execution runtime. Its forms:

```text
local_cpu / local_gpu:  local:<family>:<kind>:<generation>
                        kind in {cpu, gpu}; generation = first 12 hex of a
                        scheme-defined canonical material hash
remote_gpu:             remote:<profile_name>:<required_remote_runtime_commit>
```

Identity derivation is **scheme-versioned**. The scheme label is recorded in evidence/report but is not part of the `runtime_ref` string.

```text
bhq3_gpu_v1   # existing sealed local_gpu (backward compatible, MUST reproduce it)
  material keys (EXACT, no additions):
    python, torch, torch_cuda, ultralytics, numpy, scipy,
    device_name, compute_capability, driver_version, cuda_available
  canonical: json.dumps(material, sort_keys=True, separators=(",", ":"))
  generation: sha256(canonical.encode("utf-8")).hexdigest()[:12]
  # must reproduce local:autodl_primary:gpu:7b958347b5af

local_cpu_v1  # NEW operator-installable local_cpu scheme
  material keys:
    python, platform_system, architecture,
    numpy, scipy   (optional; null when absent)
  # GPU-only fields (device_name, compute_capability, driver_version,
  # cuda_available, torch/torch_cuda) are EXCLUDED so CPU identity never rotates
  # on GPU-only state change.
  canonical/generation: same canonical form and [:12] rule as above.

legacy_opaque  # existing repo-default runtime refs whose historical derivation
               # is not reproducible by any A3 scheme (e.g. the sealed CPU ref).
  # Treated as an already platform-certified opaque identity: remains valid
  # exactly as today; A3 never re-derives or invalidates it.
```

- **Existing sealed refs MUST remain valid:** `local:autodl_primary:cpu:a1237f8faae7` (repo-default CPU) and `local:autodl_primary:gpu:7b958347b5af` (repo-default GPU, `bhq3_gpu_v1`) continue to authorize their existing repo certificates. A3 does not retroactively invalidate repo defaults merely because their generation predates A3.
- **New operator installs require a derivable scheme.** A new A3 operator certificate must target a `runtime_ref` derivable by an A3 scheme (`bhq3_gpu_v1` for GPU, `local_cpu_v1` for CPU). Installing a new operator certificate against an opaque `legacy_opaque` ref is not permitted unless a scheme reproduces it exactly.
- **Invalidated by:** any change to a scheme material field → new generation → new `runtime_ref` → the old exact certificate no longer matches.
- **Intentionally excluded:** absolute interpreter path, hostname, MAC/CPU-serial/disk-UUID, temp paths, wall-clock time, unrelated environment variables. The operator-owned `family` label provides per-installation distinctness without machine-serial noise.
- **Missing identity material** (a scheme field cannot be collected) → `identity_status = unavailable` → qualification cannot pass and no certificate installs. Never substitute a changing `"unknown"` string and then produce a supposedly exact certificate.
- **Authority:** the derived `runtime_ref` is compared against the deployment-configured `provider.runtime_ref`; a mismatch is reported and fails closed. A3 never rewrites the provider's `runtime_ref`.

### Runtime family source (resolved, not deferred)

A new deployment setting `WSP_RUNTIME_FAMILY` → `Settings.runtime_family: str | None = None`.

- Operator-owned logical installation/runtime family (e.g. `autodl_primary`); NOT a hostname/serial/UUID.
- Validated as a safe logical identifier (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$`).
- Used only in local `runtime_ref` derivation.
- If a configured `local_*_runtime_ref` embeds a family, it MUST equal `runtime_family`; disagreement fails closed.
- Absence does NOT retroactively invalidate legacy repo-default certificates.
- NEW A3 qualification that derives a new local `runtime_ref` requires a resolved `runtime_family`.
- `runtime doctor` reports `configured_runtime_ref`, `derived_runtime_ref` (when derivable), `runtime_family`, `identity_scheme`, and `identity_status ∈ {match, mismatch, legacy_opaque, not_configured, unavailable}`. Doctor never rewrites `WSP_LOCAL_*_RUNTIME_REF`.

### Certificate store layout (operator-owned, no migration, exact uniqueness preserved)

```text
repo defaults (unchanged, read-only):   backend/app/pipelines/execution_certificates.json
operator-owned installed store:         <data_root>/runtime_certificates.json   ({"certificates": [...]} )
```

- At startup the control plane loads repo defaults + operator store and merges them, then enforces the EXISTING `ExecutionCertificateStore` exact-uniqueness rule: any duplicate exact 7-field key (repo↔operator or within operator) → **fail closed**. A3 does NOT silently dedupe or choose one.
- Install idempotency is resolved BEFORE producing a duplicate entry:
  - same exact operator certificate already stored → `already_installed` (no rewrite);
  - repo-default certificate already certifies the same exact 7-field key → `already_certified` (no operator entry added);
  - same operator key but conflicting certificate metadata/`evidence_ref` → fail closed.
- Installed certificates are written only by the platform CLI.
- **Effective on next control-plane startup / explicit registry rebuild.** No hot reload in V1.

### Qualification evidence layout (Windows-safe, under the data root, never committed)

```text
runtime_storage_key = sha256(runtime_ref.encode("utf-8")).hexdigest()   # lowercase hex

<data_root>/
  qualification/
    <executor>/
      <runtime_storage_key>/
        evidence.json
        doctor.json      (operator report, optional)
```

- `runtime_storage_key` is lowercase hex and Windows/Linux filesystem-safe; the raw `runtime_ref` appears only inside the JSON payload.
- The hashed directory key is a **storage locator only**, never execution identity.
- When loading from canonical storage, the loader MUST verify `sha256(evidence.runtime_ref) == <containing storage key>`.
- The `<executor>` segment is strictly validated (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$`).
- All resolved paths must remain under `<data_root>/qualification`.
- Evidence is versioned, hash-bearing, atomically written, and structurally validated before any install.

### CLI surface (stdlib argparse; `wisa` console script + `python -m app.cli`)

```text
wisa runtime doctor
wisa qualify --plugin <id> [--plugin-version <v>] --executor <local_cpu|local_gpu|remote_gpu> [--model-release <id>]
wisa certificate install --from <evidence_dir>
wisa certificate list
```

- `--model-release` is OPTIONAL and mirrors existing platform resolution (release-required default resolution; release-less → `None`; supplying it for a release-less plugin → `MODEL_RELEASE_MISMATCH`).
- Required commands: `runtime doctor`, `qualify`, `certificate install`, `certificate list`. No generic management framework beyond these.

### `.gitignore` scope clarification

- The default `Settings.data_root` is project-root `/data`, so the repo `.gitignore` protects the DEFAULT repo-local locations: `/data/qualification/` and `/data/runtime_certificates.json`.
- A custom external `data_root` is outside this repository; the repo `.gitignore` does not (and cannot) dynamically protect arbitrary external data roots.

---

## Module layout (subject to verification during Task 1 inspection)

```text
backend/app/runtime_qualification/__init__.py
backend/app/runtime_qualification/doctor.py       # portable runtime report + injectable probes + identity status
backend/app/runtime_qualification/identity.py     # scheme-versioned runtime identity derivation/validation
backend/app/runtime_qualification/evidence.py     # evidence schema + Windows-safe atomic storage + validation
backend/app/runtime_qualification/qualification.py# runner framework (mandatory target probe; install-eligibility)
backend/app/runtime_qualification/install.py      # live-authority validated evidence→certificate + operator store
backend/app/cli.py                                # argparse CLI
backend/app/core/config.py                        # add runtime_family setting
backend/pyproject.toml                            # add [project.scripts] wisa = "app.cli:main"
backend/app/main.py                               # merge operator store in _build_certificate_store
.gitignore                                        # ignore /data/qualification/ and /data/runtime_certificates.json
```

---

## Task 1: Runtime doctor report model + portable probes + runtime family setting

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/__init__.py` (empty package marker)
- Create: `backend/app/runtime_qualification/doctor.py`
- Modify: `backend/app/core/config.py` (add `runtime_family: str | None = None`, alias `WSP_RUNTIME_FAMILY`)
- Test: `backend/tests/test_runtime_doctor.py`

**Interfaces:**
```python
# config.py
runtime_family: str | None = None            # WSP_RUNTIME_FAMILY (operator-owned logical family)

# doctor.py
@dataclass(frozen=True)
class InterpreterReport:
    available: bool
    version: str | None
    executable_id: str | None                # PRIVATE (operator-only); never serialized to HTTP

@dataclass(frozen=True)
class GpuReport:
    applicable: bool
    available: bool
    device_name: str | None
    compute_capability: str | None
    cuda_runtime: str | None
    driver_version: str | None
    reason: str | None                        # doctor diagnostic only; never identity material

@dataclass(frozen=True)
class ProviderReport:
    executor: str
    device_type: str
    precision: str
    runtime_ref: str
    configured: bool
    identity_scheme: str                      # "bhq3_gpu_v1" | "local_cpu_v1" | "legacy_opaque" | "unavailable"
    identity_status: str                      # "match"|"mismatch"|"legacy_opaque"|"not_configured"|"unavailable"
    configured_runtime_ref: str | None
    derived_runtime_ref: str | None
    interpreter: InterpreterReport
    gpu: GpuReport

@dataclass(frozen=True)
class RuntimeDoctorReport:
    schema_version: int
    created_at: str
    runtime_family: str | None
    control_plane_python: str
    platform_system: str
    architecture: str
    providers: tuple[ProviderReport, ...]
    notes: tuple[str, ...]
    def to_operator_json(self) -> dict: ...   # operator-only (may include executable_id)

class InterpreterProbe(Protocol):
    def inspect(self, python_path: Path | None) -> InterpreterReport: ...

class GpuProbe(Protocol):
    def inspect(self) -> GpuReport: ...

def build_runtime_doctor_report(
    *, settings, provider_specs, interpreter_probe, gpu_probe,
    identity_resolver=None, clock=..., host_platform=...
) -> RuntimeDoctorReport: ...
```

- `provider_specs` is a tuple of `(executor, python_path, runtime_ref, device_type, precision, identity_scheme)` supplied by the caller (from `Settings`/providers) — no plugin-id branching.
- `identity_resolver` is an injected callable returning `(configured_runtime_ref, derived_runtime_ref, identity_status)` for a provider; the default returns `not_configured`/`legacy_opaque` without any GPU call. `identity_status = unavailable` when a scheme's required material cannot be collected.
- Doctor diagnostics (`GpuReport`) are distinct from install-eligible identity material; `nvidia-smi` is only a diagnostic on non-CUDA hosts.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_doctor.py` covering:
1. CPU-only report: one `local_cpu` provider, `device_type="cpu"`, GPU report `applicable=False`; report contains `platform_system`, `architecture`, `control_plane_python`, `runtime_family`.
2. Configured provider: a `local_gpu` spec yields a `ProviderReport` with its `identity_scheme="bhq3_gpu_v1"` and `identity_status` from the injected `identity_resolver`.
3. Identity status values surface verbatim (`match`, `mismatch`, `legacy_opaque`, `not_configured`, `unavailable`).
4. `runtime_family` from `Settings` is reported; `identity_status="not_configured"` when no family and no configured ref.
5. Doctor never calls a GPU probe for a pure `local_cpu` report and imports no cgroup/`/proc`-only path.
6. `runtime_family` setting validation: an unsafe value (with `/`, whitespace, `..`) is rejected.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "D:\LGFiles\Wideband Signal Analysis Platform\Wideband-Intelligent-Signal-Analysis-Platform\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_doctor.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification'`.

- [ ] **Step 3: Minimal implementation**

Create the package marker, `doctor.py` (dataclasses + protocol seams + `build_runtime_doctor_report`), and add `runtime_family` to `Settings`. Import only `dataclasses`, `typing`, `datetime`, `platform`, `sys`, `pathlib`, `re`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_doctor.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/__init__.py backend/app/runtime_qualification/doctor.py backend/app/core/config.py backend/tests/test_runtime_doctor.py
git commit -m "feat: add portable runtime doctor report"
```

---

## Task 2: Scheme-versioned runtime identity derivation/validation

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/identity.py`
- Test: `backend/tests/test_runtime_identity.py`

**Interfaces:**
```python
BHQ3_GPU_V1 = "bhq3_gpu_v1"
LOCAL_CPU_V1 = "local_cpu_v1"
LEGACY_OPAQUE = "legacy_opaque"

BHQ3_GPU_V1_FIELDS = ("python", "torch", "torch_cuda", "ultralytics", "numpy",
                      "scipy", "device_name", "compute_capability",
                      "driver_version", "cuda_available")
LOCAL_CPU_V1_FIELDS = ("python", "platform_system", "architecture", "numpy", "scipy")

def canonical_material_bytes(material: dict) -> bytes: ...     # json.dumps(sort_keys, separators)
def derive_generation(material: dict) -> str: ...              # sha256(canonical).hexdigest()[:12]
def derive_local_runtime_ref(*, family: str, kind: str, generation: str) -> str: ...
def derive_generation_for_scheme(*, scheme: str, material: dict) -> str: ...
    # bhq3_gpu_v1: EXACTLY BHQ3_GPU_V1_FIELDS (extra keys -> RUNTIME_IDENTITY_INVALID)
    # local_cpu_v1: EXACTLY LOCAL_CPU_V1_FIELDS (GPU-only keys -> RUNTIME_IDENTITY_INVALID)
def scheme_for_executor(*, executor: str, runtime_ref: str) -> str: ...
    # local_gpu -> bhq3_gpu_v1; local_cpu -> local_cpu_v1 if derivable else legacy_opaque
def validate_runtime_ref_against_material(*, runtime_ref, family, kind, scheme, material) -> None: ...
    # RUNTIME_IDENTITY_MISMATCH on generation/family/kind drift; RUNTIME_IDENTITY_INVALID on malformed/extra fields
```

- `canonical_material_bytes` mirrors the existing BHQ-3 script exactly: `json.dumps(material, sort_keys=True, separators=(",", ":"))` UTF-8.
- `bhq3_gpu_v1` reproduces the sealed generation `7b958347b5af` from the exact recorded BHQ-3 material; no extra fields may enter that canonical payload.
- `local_cpu_v1` excludes GPU-only + torch fields so CPU identity never rotates on GPU-only state change.
- `legacy_opaque` refs (repo defaults with no reproducible scheme) are returned as-is and are never re-derived or invalidated.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_identity.py` covering:
1. **BHQ3 compatibility:** a recorded BHQ-3 GPU material dict → `derive_generation_for_scheme(scheme="bhq3_gpu_v1", material=...)` == `"7b958347b5af"` and `derive_local_runtime_ref(family="autodl_primary", kind="gpu", generation=...)` == `"local:autodl_primary:gpu:7b958347b5af"`.
2. **No extra fields in V1:** `bhq3_gpu_v1` with an extra key (e.g. `python_version`) raises `RUNTIME_IDENTITY_INVALID`.
3. **CPU stability:** identical `local_cpu_v1` material → identical ref; changing GPU-only state (`device_name`/`compute_capability`/`driver_version`/`cuda_available`) does not change `local_cpu_v1` generation because those keys are rejected/absent; a test builds the CPU material twice with differing GPU observation dicts and asserts an unchanged CPU ref.
4. **CPU material change rotates:** changing `python`/`platform_system`/`architecture`/`numpy`/`scipy` changes the CPU ref.
5. **Legacy opaque:** the sealed CPU ref `local:autodl_primary:cpu:a1237f8faae7` is classified `legacy_opaque` by `scheme_for_executor` and is not re-derived.
6. **`validate_runtime_ref_against_material`** raises `RUNTIME_IDENTITY_MISMATCH` on generation drift, `RUNTIME_IDENTITY_INVALID` on a malformed ref or extra material fields.
7. Canonical form is deterministic and key-order independent.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_identity.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.identity'`.

- [ ] **Step 3: Minimal implementation**

Create `identity.py` importing `dataclasses`, `hashlib`, `json`, `re`, `typing`, and `app.core.errors.PlatformError`. Error codes `RUNTIME_IDENTITY_MISMATCH` / `RUNTIME_IDENTITY_INVALID`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_identity.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/identity.py backend/tests/test_runtime_identity.py
git commit -m "feat: add scheme-versioned runtime identity"
```

---

## Task 3: Qualification evidence schema + Windows-safe atomic storage

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/evidence.py`
- Test: `backend/tests/test_runtime_evidence.py`

**Interfaces:**
```python
EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_FILENAME = "evidence.json"

@dataclass(frozen=True)
class QualificationResult:
    name: str
    passed: bool
    detail: str | None = None

@dataclass(frozen=True)
class QualificationEvidence:
    schema_version: int
    created_at: str
    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    executor: str
    device_type: str
    precision: str
    runtime_ref: str
    runtime_descriptor: dict
    identity_scheme: str
    qualification_type: str
    results: tuple[QualificationResult, ...]
    passed: bool
    input_identity: dict | None
    asset_manifest_sha256: str | None
    evidence_sha256: str

def runtime_storage_key(runtime_ref: str) -> str: ...            # sha256(runtime_ref).hexdigest()
def evidence_payload(evidence: QualificationEvidence) -> dict: ...  # excludes evidence_sha256
def compute_evidence_sha256(evidence: QualificationEvidence) -> str: ...
def validated_passed(results: tuple[QualificationResult, ...]) -> bool: ...  # len>0 and all(passed)
def evidence_directory(data_root: Path, *, executor: str, runtime_ref: str) -> Path: ...
def write_evidence(data_root: Path, evidence: QualificationEvidence) -> Path: ...  # atomic, hash-stamped
def load_evidence(path: Path, *, expected_storage_key: str | None = None) -> QualificationEvidence: ...
def load_evidence_dir(evidence_dir: Path) -> QualificationEvidence: ...  # verifies dir key == sha256(runtime_ref)
```

- `validated_passed(results)` returns `False` for an empty result list; `write_evidence` refuses to persist evidence whose `passed` disagrees with `validated_passed(results)`.
- Storage layout is Windows-safe: `evidence_directory` = `<data_root>/qualification/<executor>/<runtime_storage_key(runtime_ref)>`; raw `runtime_ref` is never a directory component.
- `load_evidence_dir(evidence_dir)` recomputes `sha256(evidence.runtime_ref)` and requires it to equal the containing storage-key directory name.
- `evidence_directory` strictly validates the `executor` segment and asserts the resolved path stays under `<data_root>/qualification`.
- `load_evidence` rejects: non-`EVIDENCE_SCHEMA_VERSION`, missing/unknown required keys, hash mismatch, malformed JSON, empty `results`, and a `passed` field inconsistent with `validated_passed(results)`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_evidence.py` covering:
1. **Windows-safe directory:** `runtime_ref="local:autodl_primary:cpu:abc123def456"` → `evidence_directory(...)` path contains no `:`, is under `<data_root>/qualification/<executor>/`, and its final component equals `sha256(runtime_ref).hexdigest()`.
2. **Round-trip:** `write_evidence` then `load_evidence_dir` returns an equal evidence object with matching `evidence_sha256`.
3. **Storage-key verification:** an evidence file whose `runtime_ref` hashes to a different key than its directory is rejected (`QUALIFICATION_EVIDENCE_INVALID`).
4. **Empty results rejected:** `validated_passed(()) is False`; writing evidence with `results=()` is refused.
5. **Vacuity guard:** a hand-crafted evidence with `results=[]` and `passed=true` fails `load_evidence` (hash/consistency).
6. **Atomic write:** after success the directory has `evidence.json` and no `*.tmp`; a simulated mid-write failure leaves prior evidence intact.
7. **Path safety:** `evidence_directory` rejects `executor="../escape"`, absolute paths, separators, and NUL/control characters.
8. **No secrets:** the schema has no private-key/SSH fields.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_evidence.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.evidence'`.

- [ ] **Step 3: Minimal implementation**

Create `evidence.py` importing `dataclasses`, `hashlib`, `json`, `os`, `re`, `typing`, `datetime`, `pathlib`, `app.core.errors.PlatformError`. Error code `QUALIFICATION_EVIDENCE_INVALID`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_evidence.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/evidence.py backend/tests/test_runtime_evidence.py
git commit -m "feat: add windows-safe qualification evidence storage"
```

---

## Task 4: Qualification runner framework (mandatory target probe; install-eligibility)

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/qualification.py`
- Test: `backend/tests/test_runtime_qualification.py`

**Interfaces:**
```python
LOCAL_CPU_SMOKE_V1 = "local_cpu_smoke_v1"
GPU_DEFERRED = "gpu_deferred"

INSTALL_ELIGIBLE_TYPES = {
    "local_cpu": ("local_cpu_smoke_v1",),
    "local_gpu": (),        # NONE in A3 (Plan B authorizes real GPU types)
    "remote_gpu": (),       # NONE in A3 (Plan C authorizes real remote types)
}

def qualification_type_install_eligible(*, executor: str, qualification_type: str) -> bool: ...

@dataclass(frozen=True)
class QualificationTarget:
    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    executor: str
    runtime_ref: str
    runtime_descriptor: dict

class QualificationRunner(Protocol):
    qualification_type: str
    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]: ...

class LocalCpuQualificationRunner:
    qualification_type = LOCAL_CPU_SMOKE_V1
    def __init__(self, target_probe: Callable[[QualificationTarget], None] | None = None) -> None: ...
    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]:
        # NO target_probe -> (QualificationResult(name="probe", passed=False,
        #                     detail="QUALIFICATION_PROBE_UNAVAILABLE"),)  -> never passes
        # probe raises/returns -> failed result; never an exception

class DeferredGpuQualificationRunner:
    qualification_type = GPU_DEFERRED
    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]:
        # (QualificationResult(name="gpu", passed=False,
        #  detail="GPU_QUALIFICATION_DEFERRED"),)  -> never passes, never install-eligible

def run_qualification(
    *, target: QualificationTarget, runner: QualificationRunner, now=...,
    input_identity: dict | None = None, asset_manifest_sha256: str | None = None,
) -> QualificationEvidence: ...
```

- A passing `LocalCpuQualificationRunner` requires a mandatory platform-owned `target_probe` bound to the exact `QualificationTarget` (plugin/version/release/executor/runtime_ref/descriptor). Without a probe the result is `passed=False` with reason `QUALIFICATION_PROBE_UNAVAILABLE`. Tests inject deterministic fakes; A3 tests never run real inference. Core code branches only on `executor`, never `plugin_id`.
- Runner selection is by executor: `local_cpu` → `LocalCpuQualificationRunner`; `local_gpu`/`remote_gpu` → `DeferredGpuQualificationRunner`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_qualification.py` covering:
1. `local_cpu` with a deterministic passing `target_probe` → `passed=True`, non-empty results.
2. `local_cpu` with no probe → `passed=False`, reason `QUALIFICATION_PROBE_UNAVAILABLE` (no exception).
3. A raising `target_probe` → failed result (no exception, `passed=False`).
4. `DeferredGpuQualificationRunner` for `local_gpu`/`remote_gpu` → `passed=False`, `qualification_type="gpu_deferred"`, and `qualification_type_install_eligible(...) is False`.
5. `qualification_type_install_eligible`: `local_cpu`+`local_cpu_smoke_v1` → True; `local_gpu`/`remote_gpu` any type → False; unknown type → False.
6. `run_qualification` echoes the exact `QualificationTarget` identity into evidence and never writes a certificate.
7. Executor-based runner selection only (no plugin-id logic).

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_qualification.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.qualification'`.

- [ ] **Step 3: Minimal implementation**

Create `qualification.py` importing `dataclasses`, `typing`, `datetime`, `app.core.errors`, `app.runtime_qualification.evidence`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_qualification.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/qualification.py backend/tests/test_runtime_qualification.py
git commit -m "feat: add qualification runner framework"
```

---

## Task 5: Certificate install with LIVE authority re-resolution + operator store

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/install.py`
- Modify: `backend/app/main.py` (`_build_certificate_store` merges the operator store with exact-uniqueness)
- Modify: `.gitignore` (ignore `/data/qualification/` and `/data/runtime_certificates.json`)
- Test: `backend/tests/test_certificate_install.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class InstallResult:
    status: str                 # "created" | "already_installed" | "already_certified"
    certificate: ExecutionCertificate

def operator_certificate_path(data_root: Path) -> Path: ...   # <data_root>/runtime_certificates.json
def load_operator_certificates(data_root: Path) -> list[ExecutionCertificate]: ...   # [] if absent
def build_certificate_store(*, repo_path: Path, data_root: Path) -> ExecutionCertificateStore: ...
    # load repo + operator, enforce EXACT uniqueness (duplicate 7-field key -> fail closed); NO silent dedupe
def resolve_live_authority(*, registry, model_release_store, executor_registry,
                           plugin_id, plugin_version, executor,
                           requested_model_release_id: str | None, data_root) -> LiveAuthority: ...
    # resolves exact definition/version, exact ModelRelease (or release-less None),
    # exact provider, provider.runtime_ref, provider.runtime_descriptor(), technical capability
def validate_evidence_for_install(
    *, evidence, authority: LiveAuthority, now=..., max_age_s: int | None = None
) -> ExecutionCertificate: ...
def install_certificate(
    *, data_root: Path, repo_certificate_path: Path, evidence, authority: LiveAuthority
) -> InstallResult: ...
```

`LiveAuthority` (authority-context object) carries the CURRENT deployment facts:
```python
@dataclass(frozen=True)
class LiveAuthority:
    definition: object            # exact PipelineDefinition
    plugin_id: str
    plugin_version: str
    model_release_id: str | None  # exact resolved release id (or None for release-less)
    executor: str
    runtime_ref: str
    runtime_descriptor: dict      # provider.runtime_descriptor().to_metadata()
    certified_capability: bool    # exact technical+certificate capability present
    provider_present: bool
```

- **Live authority is authoritative.** `validate_evidence_for_install` compares evidence against the CURRENT resolved `LiveAuthority` — never trusting identity claims from the evidence JSON. It requires: valid evidence (hash/shape/non-empty/ineligible-type checks), `qualification_type_install_eligible(executor, type)`, `passed is True`, plugin/version == definition, `model_release_id` == resolved release id, `executor`/`device_type`/`precision`/`runtime_ref`/`runtime_descriptor` == live provider, `provider_present`, and (for install) the technical capability present. Optional `max_age_s` staleness bound.
- **Duplicate semantics preserved:** `build_certificate_store` uses the existing `ExecutionCertificateStore` (which rejects duplicate exact keys) — no silent dedupe. `install_certificate` resolves idempotency BEFORE writing: same operator cert → `already_installed`; repo-default already certifies the same exact key → `already_certified`; same operator key conflicting metadata/`evidence_ref` → fail closed.
- **No hot reload:** writing the operator store does not mutate a running `app.state.executor_registry`; the new certificate is effective on the next control-plane startup / explicit registry rebuild.
- Error codes: `QUALIFICATION_EVIDENCE_INVALID`, `EXECUTION_NOT_CERTIFIED`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_certificate_install.py` covering:
1. **Live authority required:** valid-hash evidence but current `runtime_ref` differs → reject; current `runtime_descriptor` differs → reject; different plugin version → reject; different resolved release → reject; provider missing → reject; technical capability missing → reject.
2. **Explicit install:** qualification alone never writes a certificate; `install_certificate` is the only writer.
3. **Happy path:** valid evidence + matching live authority installs an exact certificate; `certified_capability` becomes non-None after a *rebuilt* store.
4. **Idempotency + duplicates:** same operator cert → `already_installed`; repo-default already certifies same key → `already_certified` (no operator entry); same operator key conflicting metadata → fail closed; repo+operator duplicate exact key at `build_certificate_store` → fail closed.
5. **Ineligible/vacuous evidence:** `gpu_deferred`, unknown type, and empty-results evidence cannot install.
6. **Six repo certificates regression:** all six repo defaults still load and remain certifiable exactly as before, including `local:autodl_primary:cpu:a1237f8faae7` and `local:autodl_primary:gpu:7b958347b5af`.
7. **Restart semantics:** an existing in-memory `ExecutorRegistry` is unchanged by a file install; a freshly built registry sees the new certificate.
8. **Path safety / no secrets:** operator path stays under `<data_root>`; certificate holds only the 7 fields + `evidence_ref`.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_certificate_install.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.install'`.

- [ ] **Step 3: Minimal implementation**

Create `install.py` importing `dataclasses`, `json`, `os`, `pathlib`, `app.core.errors`, `app.remote_execution.runtime`, `app.runtime_qualification.evidence`, `app.runtime_qualification.qualification`. Update `main.py` (`_build_certificate_store` → `build_certificate_store(repo_path=…, data_root=settings.data_root)`) and `.gitignore`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_certificate_install.py backend/tests/test_execution_certificate.py backend/tests/test_executor_registry.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/install.py backend/app/main.py .gitignore backend/tests/test_certificate_install.py
git commit -m "feat: add live-authority certificate install"
```

---

## Task 6: CLI wiring

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/cli.py`
- Modify: `backend/pyproject.toml` (`[project.scripts] wisa = "app.cli:main"`)
- Test: `backend/tests/test_cli.py`

**Interfaces:**
```python
def build_parser() -> argparse.ArgumentParser: ...
def main(argv: list[str] | None = None) -> int: ...
```

Subcommands (stdlib `argparse`; also runnable as `python -m app.cli`):

```text
runtime doctor
qualify --plugin <id> [--plugin-version <v>] --executor <local_cpu|local_gpu|remote_gpu> [--model-release <id>]
certificate install --from <evidence_dir>
certificate list
```

- `--model-release` is OPTIONAL. For a release-required plugin: explicit id → resolve that exact release; omitted → resolve the platform default via `ModelReleaseStore`. For a release-less plugin: omitted → `model_release_id=None`; supplied → `MODEL_RELEASE_MISMATCH`. Evidence stores the exact resolved id.
- `certificate install --from <dir>` must: load and verify evidence; build current settings; resolve `pipeline_registry`/`model_release_store`/`executor_registry`; resolve exact plugin/version, exact release, exact provider; build `LiveAuthority`; then `validate_evidence_for_install` + `install_certificate`. It never synthesizes authority from evidence fields alone.
- Install success output MUST state that the certificate is effective on the next control-plane restart / registry rebuild (no hot reload).
- `main` returns an int exit code; `0` on success, non-zero on `PlatformError` printed as `CODE: message` on stderr (no tracebacks). All construction is injectable so tests never need a real GPU or a running server.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_cli.py` covering:
1. `build_parser` exposes the four subcommands; `--model-release` is optional on `qualify`.
2. `runtime doctor` prints a structured report and exits 0 on a CPU-only fake environment.
3. `qualify --executor local_cpu` (release-less plugin, `--model-release` omitted) writes `passed` evidence and exits 0; `--executor local_cpu --model-release golden` on a release-less plugin exits non-zero `MODEL_RELEASE_MISMATCH`.
4. `qualify` with a release-required plugin and omitted `--model-release` records the resolved default release id.
5. `qualify --executor local_gpu` writes deferred (not-passed) evidence and exits non-zero.
6. `certificate install --from <dir>` on valid evidence+matching authority installs and prints the restart note; on tampered evidence or live-authority mismatch exits non-zero and does not mutate the store.
7. `certificate list` lists installed certificates (empty when none).
8. Unknown subcommand/flags exit non-zero with usage (no traceback).

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_cli.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Minimal implementation**

Create `cli.py` importing `argparse`, `json`, `sys`, `pathlib`, `app.core.config.Settings`, `app.core.errors.PlatformError`, `app.pipelines.registry.create_pipeline_registry`, and the `runtime_qualification` modules. Add `[project.scripts]` to `pyproject.toml`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_cli.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/cli.py backend/pyproject.toml backend/tests/test_cli.py
git commit -m "feat: add runtime qualification cli"
```

---

## Task 7: A1/A2 integration + live-authority fail-closed matrix

**GPU REQUIRED: NO**

**Files:**
- Test: `backend/tests/test_runtime_qualification_integration.py`

**Interfaces:** none (verification + guard tests; no production change expected).

- [ ] **Step 1: Write the integration/guard tests**

Covering:
1. **A2 sees the installed certificate after rebuild:** install for an exact tuple, rebuild the registry, `certified_capability` becomes non-None and the A2 Auto `certified` flag flips — with no A2 policy change.
2. **A2 policy untouched:** the Auto decision for a fixed input is identical before/after a certificate install that does not match the requested executor.
3. **Six repo certificates remain loadable:** the merged store loads all repo defaults unchanged and each remains `certified_capability`-visible.
4. **Legacy CPU ref not re-derived:** `local:autodl_primary:cpu:a1237f8faae7` remains valid and is classified `legacy_opaque`.
5. **A1 exact authority preserved:** `validate_frozen_execution_authority` still fails a mismatched descriptor; runtime rotation (new `runtime_ref`) invalidates the old certificate and the executor becomes uncertified until re-qualified.
6. **Certificate/provider disappearance fails closed.**
7. **Plugin cannot self-certify:** source scan of `app/` proves only `install.py` writes the operator certificate store.
8. **No A3 internals leak to HTTP:** `GET /api/executor-selection`, `/api/pipelines`, `/api/executor-availability` responses contain no `environment_ref`, interpreter paths, or qualification filesystem paths.

- [ ] **Step 2: Run (gate GREEN)**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_qualification_integration.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_runtime_qualification_integration.py
git commit -m "test: guard runtime qualification integration with a1 and a2"
```

---

## Task 8: Portability + full local regression

**GPU REQUIRED: NO**

**Verification task — introduces no production code.**

- [ ] **Step 1: Portability sweep**

Assert that `app.runtime_qualification.*` and `app.cli` sources contain none of `cgroup`, `/proc`, `bhq3_memory_gate`, `AutoDL`; that GPU diagnostics are optional (absence of `nvidia-smi` does not raise); that no raw `runtime_ref` is used as a directory component (only `runtime_storage_key`); and that the control plane remains ML-free:

```powershell
& "...\.venv\Scripts\python.exe" -c "import importlib.util as u; print('torch:', u.find_spec('torch')); print('ultralytics:', u.find_spec('ultralytics'))"
```

Expected: `torch: None`, `ultralytics: None`.

- [ ] **Step 2: Focused A3 regression**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_doctor.py backend/tests/test_runtime_identity.py backend/tests/test_runtime_evidence.py backend/tests/test_runtime_qualification.py backend/tests/test_certificate_install.py backend/tests/test_cli.py backend/tests/test_runtime_qualification_integration.py backend/tests/test_execution_certificate.py backend/tests/test_executor_registry.py backend/tests/test_execution_authority.py backend/tests/test_analysis_auto_execution.py backend/tests/test_dataset_experiment_auto_creation.py backend/tests/test_executor_selection_api.py -q
```

Expected: `0 failed`, `0 errors`.

- [ ] **Step 3: Windows full-suite differential gate**

Capture a fresh baseline from the A3 base (`24f5357f23c5f31fa46e755f279d1119ce213e33`) in a temporary detached worktree and compare against the A3 candidate using the same control-plane venv:

```text
problem node = FAILED node id OR ERROR node id
candidate_new_problems = candidate_problem_set - base_problem_set
require candidate_new_problems == empty
```

Record for both exact base and candidate: `passed`, `skipped`, `failed`, `errors`, `warnings`, `runtime`, and the full problem node-ID lists. Windows-only POSIX environmental failures are never "fixed" by changing production code. Remove the temporary worktree after capture.

- [ ] **Step 4: Commit**

No source commit is created by this verification task. Record the differential result in the A3 acceptance evidence.

---

## Plan self-review record

Corrective self-review before commit:

1. **Raw `runtime_ref` is never a Windows directory component.** Evidence storage uses `runtime_storage_key = sha256(runtime_ref).hexdigest()`; the raw ref lives only in JSON; the loader verifies the containing key equals `sha256(runtime_ref)`.
2. **Existing BHQ3 GPU ref is backward compatible.** A `bhq3_gpu_v1` scheme reproduces the exact BHQ-3 canonical material → generation `7b958347b5af`; no extra fields enter the V1 payload; a compatibility test proves it.
3. **Existing repo-default certificates are not retroactively invalidated.** Repo defaults (including the sealed CPU ref `a1237f8faae7`) remain valid; unknown-derivation refs are treated as `legacy_opaque`, never re-derived.
4. **CPU runtime identity ignores GPU-only noise.** `local_cpu_v1` excludes GPU/torch fields; a GPU-only state change leaves the CPU ref unchanged.
5. **Runtime family has one explicit source.** `WSP_RUNTIME_FAMILY` → `Settings.runtime_family`, validated, matched against any configured `local_*_runtime_ref`, disagreement fails closed; absence does not invalidate legacy certs.
6. **Install re-resolves CURRENT live authority.** `install_certificate`/`validate_evidence_for_install` take a `LiveAuthority` resolved from the current registry/settings; evidence identity claims are never trusted alone.
7. **Evidence has at least one result.** `validated_passed` requires non-empty results; vacuous `results=[]` cannot be written or loaded as passing.
8. **Qualification type is install-eligible.** A closed eligibility map authorizes only `local_cpu_smoke_v1` for `local_cpu`; `local_gpu`/`remote_gpu` have none in A3; unknown/deferred types cannot install.
9. **GPU-deferred evidence cannot install.** `DeferredGpuQualificationRunner` never passes and `gpu_deferred` is not install-eligible.
10. **Release-less plugins work.** `--model-release` is optional; release-required default resolution; release-less → `None`; supplying it for a release-less plugin → `MODEL_RELEASE_MISMATCH`.
11. **Duplicate certificate semantics remain fail-closed.** Merged repo+operator store preserves the existing exact-uniqueness rejection (no silent dedupe); install idempotency (`already_installed`/`already_certified`) is resolved before writing; conflicting operator metadata fails closed.
12. **File install requires restart/registry rebuild to become live.** No hot reload; tests prove an existing in-memory registry is unchanged and a rebuilt registry sees the new certificate.
13. **No GPU/CUDA/server required.** Every task is `GPU REQUIRED: NO`; GPU diagnostics are optional and never define identity; real GPU/remote qualification is deferred to Plans B/C.
14. **Only the plan document changed.** No production code, tests, config, or certificates were modified in this pass.

Additional invariants preserved: no PKI (SHA = integrity only); no plugin self-certification; no DB migration; no frontend; A1 authority unchanged; A2 policy unchanged; no A3/B-C scope leakage; public APIs expose no qualification internals; `.gitignore` protects the default `/data` locations only.