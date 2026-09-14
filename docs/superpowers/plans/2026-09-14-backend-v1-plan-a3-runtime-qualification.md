# Backend V1 Plan A3 Runtime Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the operator-facing per-installation runtime qualification path — `runtime doctor` → runtime identity → small qualification → immutable evidence → explicit `certificate install` → exact `ExecutionCertificate` — so that a plugin/runtime/executor becomes certified only after explicit qualification evidence plus an explicit operator-owned install step. Wire it to the existing certificate store so A2 `certified` and A1 exact authority pick it up with no policy change.

**Architecture:** A new torch-free, portable `backend/app/runtime_qualification/` package owns the doctor report, the deterministic runtime-identity derivation/validation, the qualification-evidence schema and atomic storage, the qualification runner framework, and the operator-owned certificate install/store seam. A single `backend/app/cli.py` (stdlib `argparse`) exposes `wisa runtime doctor`, `wisa qualify`, `wisa certificate install`, `wisa certificate list`. No existing authority is redesigned: `RuntimeDescriptor`, `ExecutionCertificate`, `ExecutionCertificateStore`, `ExecutorRegistry`, A1 recovery, and A2 Auto policy are consumed unchanged. The only authority for runtime identity remains `provider.runtime_ref`; the identity module is a deterministic derivation/validation helper, never a second identity system.

**Tech Stack:** Python 3.12, stdlib `argparse`/`hashlib`/`json`/`pathlib`/`tempfile`/`subprocess`, Pydantic v2, pytest (control-plane venv is ML-free).

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

## Global Constraints

- **GPU REQUIRED: NO** for every task. No CUDA, no AutoDL, no server, no real model inference. All A3 control-plane behavior is testable with deterministic/injected probes and SQLite/filesystem only.
- **No redesign of the authority model.** `PluginDefinition`, `ModelRelease`, `ExecutorRegistry`, `ExecutionCertificate`, `RuntimeDescriptor`, A1 recovery, and A2 Auto policy are consumed unchanged. A3 supplies/changes only the certificate store contents behind `ExecutionCertificateStore`.
- **One runtime-identity authority.** `provider.runtime_ref` (deployment-owned) remains authoritative; A3 derives/validates it from generation material and never overrides it. No competing identity model.
- **Exact certificate semantics preserved.** The 7-field key `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, runtime_ref)` and exact matching are not weakened. No generic "any local_gpu/CUDA/Windows" certificates.
- **No plugin self-certification.** Certificates are produced only by the platform CLI from validated qualification evidence on explicit operator action. Qualification passing alone never installs a certificate.
- **Fail closed.** Runtime/plugin/release/executor mismatch, failed qualification, stale/malformed/tampered evidence, and hash mismatch all block installation.
- **SHA = integrity, not authenticity.** Evidence and certificate hashes are local integrity fingerprints; A3 does not claim external signer authenticity and introduces no PKI.
- **No DB migration.** Operator-owned state is local files (matching the existing file-based certificate store). Qualification evidence lives under `<data_root>/qualification/…` and is never committed.
- **Portable.** Windows and Linux, CPU-only, no cgroup/`/proc` dependency defining identity. Optional GPU diagnostics may use `nvidia-smi` when present; its absence must not break a CPU runtime doctor.
- **Public/private boundary preserved.** CLI output is operator-facing; no new HTTP API is added; browser APIs never expose interpreter paths, `environment_ref`, qualification filesystem paths, or certificate internals.
- **No frontend, no science/model code, no A3/B-C scope leakage.** Real GPU/remote qualification campaigns belong to Plans B/C.
- Every production task is RED → GREEN and uses the existing local control-plane venv on Windows.

### Runtime identity contract (single authority)

`runtime_ref` is an operator-owned, per-installation immutable generation identity for one execution runtime. Its two sanctioned forms:

```text
local_cpu / local_gpu:  local:<family>:<kind>:<generation>
                        kind in {cpu, gpu}; generation = first 12 hex of
                        sha256(canonical generation material)
remote_gpu:             remote:<profile_name>:<required_remote_runtime_commit>
```

- **Generation material (local):** `python_version`, `platform_system`, `architecture`, `torch_version`, `torch_cuda_version`, `ultralytics_version`, `numpy_version`, `scipy_version`, `device_name`, `compute_capability`, `driver_version`, `cuda_available` (absent ML packages are represented as `null`).
- **Invalidated by:** any change to a material field → new generation → new `runtime_ref` → the old exact certificate no longer matches.
- **Intentionally excluded:** absolute interpreter path, hostname, MAC/CPU-serial/disk-UUID, temp paths, wall-clock time, unrelated environment variables. The operator-owned `family` label provides per-installation distinctness without machine-serial noise.
- **Authority:** the derived `runtime_ref` is compared against the deployment-configured `provider.runtime_ref`; a mismatch is reported and fails closed. A3 never rewrites the provider's `runtime_ref`.

### Certificate store layout (operator-owned, no migration)

```text
repo defaults (unchanged, read-only):   backend/app/pipelines/execution_certificates.json
operator-owned installed store:         <data_root>/runtime_certificates.json   ({"certificates": [...]} )
```

At startup the control plane loads repo defaults + operator store and merges them by exact 7-field key (duplicate identical key → single entry; conflicting duplicate → fail closed). Installed certificates are written only by the platform CLI. Old-runtime certificates remain stored but no longer match after a runtime upgrade.

### Qualification evidence layout (under the data root, never committed)

```text
<data_root>/qualification/<executor>/<runtime_ref>/evidence.json
<data_root>/qualification/<executor>/<runtime_ref>/doctor.json      (operator report, optional)
```

Evidence is versioned, hash-bearing, atomically written, and structurally validated before any install. Path segments are strictly validated (no separators, no `.`/`..`, no control/NUL characters, no absolute paths).

### CLI surface (stdlib argparse; `wisa` console script + `python -m app.cli`)

```text
wisa runtime doctor
wisa qualify --plugin <id> [--plugin-version <v>] --model-release <id> --executor <local_cpu|local_gpu|remote_gpu>
wisa certificate install --from <evidence_dir>
wisa certificate list
```

Required: `runtime doctor`, `qualify`, `certificate install`, `certificate list`. No generic management framework beyond these.

---

## Module layout (subject to verification during Task 1 inspection)

```text
backend/app/runtime_qualification/__init__.py
backend/app/runtime_qualification/doctor.py       # portable runtime report + injectable probes
backend/app/runtime_qualification/identity.py     # deterministic runtime identity derivation/validation
backend/app/runtime_qualification/evidence.py     # evidence schema + atomic storage + validation
backend/app/runtime_qualification/qualification.py# runner framework (protocol + local_cpu runner + GPU-deferred)
backend/app/runtime_qualification/install.py      # evidence→certificate + operator-owned store
backend/app/cli.py                                # argparse CLI
backend/pyproject.toml                            # add [project.scripts] wisa = "app.cli:main"
backend/app/main.py                               # merge operator store in _build_certificate_store
.gitignore                                        # ignore data/qualification/ and operator certificate store
```

---

## Task 1: Runtime doctor report model + portable probes

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/__init__.py` (empty package marker)
- Create: `backend/app/runtime_qualification/doctor.py`
- Test: `backend/tests/test_runtime_doctor.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class InterpreterReport:
    available: bool
    version: str | None
    executable_id: str | None            # PRIVATE (operator-only); never serialized to HTTP

@dataclass(frozen=True)
class GpuReport:
    applicable: bool
    available: bool
    device_name: str | None
    compute_capability: str | None
    cuda_runtime: str | None
    driver_version: str | None
    reason: str | None

@dataclass(frozen=True)
class ProviderReport:
    executor: str
    device_type: str
    precision: str
    runtime_ref: str
    configured: bool
    interpreter: InterpreterReport
    gpu: GpuReport

@dataclass(frozen=True)
class RuntimeDoctorReport:
    schema_version: int
    created_at: str
    runtime_family: str
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
    *, settings, provider_specs, interpreter_probe, gpu_probe, clock=..., host_platform=...
) -> RuntimeDoctorReport: ...
```

- `provider_specs` is a small tuple of `(executor, python_path, runtime_ref, device_type, precision)` supplied by the caller (from `Settings`/providers) — no plugin-id branching.
- `InterpreterProbe`/`GpuProbe` are injected; the production CPU interpreter probe runs `[python, "-c", "import sys; print(sys.version)"]` under the configured interpreter; the default GPU probe returns `applicable=False, available=False` on non-CUDA hosts and may consult `nvidia-smi` only when available. No cgroup/`/proc` dependency.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_doctor.py` covering:
1. CPU-only report: no configured GPU → one `local_cpu` provider with `device_type="cpu"`, GPU report `applicable=False`; report serializes and contains `platform_system`, `architecture`, `control_plane_python`.
2. Configured provider: a `local_gpu` spec yields a second `ProviderReport` with its `runtime_ref` and a GPU report from the injected probe.
3. Interpreter probe failure: a fake probe returning `available=False` is reported without raising.
4. Deterministic clock/platform: injecting `clock` and `host_platform` yields a stable report (no host-dependent fields leak into assertions).
5. Portability: doctor imports no `cgroup`/`/proc`/`nvidia-smi`-only path and does not require a GPU.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "D:\LGFiles\Wideband Signal Analysis Platform\Wideband-Intelligent-Signal-Analysis-Platform\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_doctor.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification'`.

- [ ] **Step 3: Minimal implementation**

Create the package marker and `doctor.py` with the dataclasses, protocol seams, and `build_runtime_doctor_report`, importing only `dataclasses`, `typing`, `datetime`, `platform`, `sys`, `pathlib`. No FastAPI, no DB, no network.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_doctor.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/__init__.py backend/app/runtime_qualification/doctor.py backend/tests/test_runtime_doctor.py
git commit -m "feat: add portable runtime doctor report"
```

---

## Task 2: Deterministic runtime identity derivation/validation

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/identity.py`
- Test: `backend/tests/test_runtime_identity.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class RuntimeIdentityMaterial:
    python_version: str
    platform_system: str
    architecture: str
    torch_version: str | None = None
    torch_cuda_version: str | None = None
    ultralytics_version: str | None = None
    numpy_version: str | None = None
    scipy_version: str | None = None
    device_name: str | None = None
    compute_capability: str | None = None
    driver_version: str | None = None
    cuda_available: bool = False

def canonical_material_bytes(material: RuntimeIdentityMaterial) -> bytes: ...
def derive_generation(material: RuntimeIdentityMaterial) -> str: ...  # 12 lowercase hex
def derive_local_runtime_ref(*, family: str, kind: str, material: RuntimeIdentityMaterial) -> str: ...
def validate_runtime_ref_against_material(
    *, runtime_ref: str, kind: str, family: str, material: RuntimeIdentityMaterial
) -> None: ...   # raises RUNTIME_IDENTITY_MISMATCH on drift; RUNTIME_IDENTITY_INVALID on malformed
```

- `canonical_material_bytes` = `json.dumps(material_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` UTF-8, mirroring the existing BHQ-3 script's canonical form (no second identity scheme).
- `derive_generation` = `sha256(canonical_material_bytes).hexdigest()[:12]`.
- `derive_local_runtime_ref` = `f"local:{family}:{kind}:{generation}"`.
- `validate_runtime_ref_against_material` parses `local:<family>:<kind>:<gen>`, requires the configured `family`/`kind` to match, and requires `gen == derive_generation(material)`; otherwise fail closed. `remote_gpu` refs (`remote:<profile>:<commit>`) are not material-derived and are validated by pattern only (Task 1/4 owner).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_identity.py` covering:
1. Stable identity: identical material → identical `runtime_ref` and generation.
2. Material change rotates identity: changing `torch_version` (or `cuda_available`, `compute_capability`) changes the generation and `runtime_ref`.
3. Irrelevant noise does not rotate identity: interpreter absolute path, hostname, timestamp are not part of `RuntimeIdentityMaterial`, so they cannot change the generation (assert the dataclass has no such field and generation is stable under re-derivation).
4. `local:<family>:<kind>:<gen>` form and 12-lowercase-hex generation.
5. `validate_runtime_ref_against_material` raises `RUNTIME_IDENTITY_MISMATCH` on generation drift and `RUNTIME_IDENTITY_INVALID` on a malformed ref.
6. Canonical form is deterministic and key-order independent (dict/field insertion order irrelevant).

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_identity.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.identity'`.

- [ ] **Step 3: Minimal implementation**

Create `identity.py` importing only `dataclasses`, `hashlib`, `json`, `re`, `typing`. Add module constants `RUNTIME_IDENTITY_MISMATCH` / `RUNTIME_IDENTITY_INVALID` used as `PlatformError` codes (import `app.core.errors.PlatformError`).

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_identity.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/identity.py backend/tests/test_runtime_identity.py
git commit -m "feat: add deterministic runtime identity"
```

---

## Task 3: Qualification evidence schema + atomic storage

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
    qualification_type: str
    results: tuple[QualificationResult, ...]
    passed: bool
    input_identity: dict | None
    asset_manifest_sha256: str | None
    evidence_sha256: str

def evidence_payload(evidence: QualificationEvidence) -> dict: ...        # excludes evidence_sha256
def compute_evidence_sha256(evidence: QualificationEvidence) -> str: ...  # sha256 over canonical payload
def evidence_directory(data_root: Path, *, executor: str, runtime_ref: str) -> Path: ...
def write_evidence(data_root: Path, evidence: QualificationEvidence) -> Path: ...  # atomic, hash-stamped
def load_evidence(path: Path) -> QualificationEvidence: ...               # strict: shape + hash + version
```

- Storage is atomic: write to `evidence.json.tmp` in the same directory, `flush` + `os.fsync`, then `os.replace`.
- `evidence_directory` strictly validates `executor` and `runtime_ref` path segments (`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$`, no `..`, no `/`/`\`, no NUL/control) and asserts the resolved directory stays under `<data_root>/qualification`.
- `load_evidence` rejects: non-`EVIDENCE_SCHEMA_VERSION`, missing/unknown required keys, hash mismatch, malformed JSON, and pass/fail inconsistency (`passed` must equal all-`results.passed`).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_evidence.py` covering:
1. Round-trip: `write_evidence` then `load_evidence` returns an equal evidence object; `evidence_sha256` matches `compute_evidence_sha256`.
2. Atomic write: after a successful write the directory contains `evidence.json` and no `*.tmp`; a simulated failure during write leaves the previous evidence intact.
3. Hash verification: tampering with `device_name`/`passed` in the JSON makes `load_evidence` raise `REMOTE_RESULT_INVALID`-style `EVIDENCE_INVALID` (use `PlatformError` code `QUALIFICATION_EVIDENCE_INVALID`).
4. Path safety: `evidence_directory` rejects `executor="../escape"`, `runtime_ref="a/b"`, absolute paths, and NUL/control characters.
5. Schema/version: unknown `schema_version` and unexpected extra keys are rejected (fail closed).
6. No secrets: evidence fields never include private key material or SSH configuration (assert the schema has no such fields).

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_evidence.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.evidence'`.

- [ ] **Step 3: Minimal implementation**

Create `evidence.py` importing `dataclasses`, `hashlib`, `json`, `os`, `re`, `typing`, `datetime`, `pathlib`, and `app.core.errors.PlatformError`. Error code: `QUALIFICATION_EVIDENCE_INVALID`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_evidence.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/evidence.py backend/tests/test_runtime_evidence.py
git commit -m "feat: add qualification evidence storage"
```

---

## Task 4: Qualification runner framework (+ local_cpu runner; GPU deferred hooks)

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/qualification.py`
- Test: `backend/tests/test_runtime_qualification.py`

**Interfaces:**
```python
class QualificationRunner(Protocol):
    qualification_type: str
    def run(self, *, definition, executor: str, device_type: str, precision: str,
            runtime_ref: str) -> tuple[QualificationResult, ...]: ...

class LocalCpuQualificationRunner:
    qualification_type = "local_cpu_smoke"
    def __init__(self, pipeline_probe: Callable[[], None] | None = None) -> None: ...
    def run(self, *, definition, executor, device_type, precision, runtime_ref): ...
        # interpreter-agnostic, bounded, deterministic CPU smoke (no GPU); a failing
        # pipeline_probe yields a failed result, never an exception

class DeferredGpuQualificationRunner:
    qualification_type = "gpu_deferred"
    def run(self, *, definition, executor, device_type, precision, runtime_ref):
        # returns a single NOT-passed result with reason "GPU_QUALIFICATION_DEFERRED"

def run_qualification(
    *, plugin_id, plugin_version, model_release_id, executor, runner,
    runtime_descriptor: dict, runtime_ref: str, input_identity: dict | None = None,
    asset_manifest_sha256: str | None = None, now=...,
) -> QualificationEvidence: ...
```

- Runner selection is by **executor** only (`local_cpu` → `LocalCpuQualificationRunner`; `local_gpu`/`remote_gpu` → `DeferredGpuQualificationRunner` until Plans B/C). No plugin-id branching.
- `run_qualification` never installs a certificate; it only produces evidence with `passed = all(results.passed)`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_runtime_qualification.py` covering:
1. `local_cpu` runner produces passing results and `passed=True` with a deterministic probe.
2. A failing `pipeline_probe` yields a failed result and `passed=False` (no exception).
3. `DeferredGpuQualificationRunner` yields `passed=False` with reason `GPU_QUALIFICATION_DEFERRED` for `local_gpu`/`remote_gpu`.
4. Executor-based runner selection (`local_cpu` vs `local_gpu`) — no plugin-id branch.
5. `run_qualification` emits a fully-populated `QualificationEvidence` (identity fields echo the inputs) and never writes a certificate.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_runtime_qualification.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.qualification'`.

- [ ] **Step 3: Minimal implementation**

Create `qualification.py` importing `dataclasses`, `typing`, `datetime`, `app.core.errors`, and `app.runtime_qualification.evidence`.

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

## Task 5: Certificate install + operator-owned certificate store

**GPU REQUIRED: NO**

**Files:**
- Create: `backend/app/runtime_qualification/install.py`
- Modify: `backend/app/main.py` (`_build_certificate_store` merges the operator store)
- Modify: `.gitignore` (ignore `data/qualification/` and `<data_root>/runtime_certificates.json`)
- Test: `backend/tests/test_certificate_install.py`

**Interfaces:**
```python
def operator_certificate_path(data_root: Path) -> Path: ...          # <data_root>/runtime_certificates.json
def load_operator_certificates(data_root: Path) -> list[ExecutionCertificate]: ...  # optional; [] if absent
def build_certificate_store(*, repo_path: Path, data_root: Path) -> ExecutionCertificateStore: ...
    # merge repo defaults + operator store by exact 7-field key; conflicting duplicate -> fail closed
def validate_evidence_for_install(
    *, evidence, definition, provider, model_release_id: str | None,
    now=..., max_age_s: int | None = None,
) -> ExecutionCertificate: ...
def install_certificate(*, data_root: Path, evidence) -> str: ...    # "created" | "already_installed"
```

- `validate_evidence_for_install` requires: valid evidence (hash/shape), `passed is True`, `plugin_id/plugin_version` == definition, `model_release_id` == resolved release id, `executor` == provider name, `device_type/precision` == `provider.runtime_descriptor()`, `runtime_ref` == `provider.runtime_ref`, `runtime_descriptor` == descriptor `to_metadata()`; optional `max_age_s` staleness bound. Any mismatch or failed/stale/malformed evidence → `QUALIFICATION_EVIDENCE_INVALID` / `EXECUTION_NOT_CERTIFIED` (fail closed).
- `install_certificate` re-runs `validate_evidence_for_install`, then writes the operator store atomically (temp + fsync + replace). Installing the same evidence twice returns `"already_installed"` (idempotent, no rewrite). No destructive replacement of a differing existing cert.
- `main.py` `_build_certificate_store()` becomes `build_certificate_store(repo_path=..., data_root=settings.data_root)`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_certificate_install.py` covering:
1. Explicit install required: qualification alone never writes a certificate (assert store unchanged before `install_certificate`).
2. Happy path: valid evidence installs an exact certificate; `ExecutorRegistry.certified_capability(...)` becomes non-None for that exact tuple.
3. Idempotency: installing the same evidence twice returns `"already_installed"` and leaves one entry.
4. Fail closed on: failed qualification, runtime identity mismatch, plugin/version mismatch, release mismatch, executor mismatch, tampered evidence (hash), malformed evidence, stale evidence (with `max_age_s`).
5. Store merge: `build_certificate_store` includes repo defaults + operator store; a conflicting duplicate key raises.
6. Path safety: `operator_certificate_path` is under `<data_root>`; a malicious evidence dir cannot escape.
7. No secret/private leakage: the written certificate contains only the 7 exact fields + `evidence_ref`.

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_certificate_install.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.runtime_qualification.install'`.

- [ ] **Step 3: Minimal implementation**

Create `install.py` importing `dataclasses`, `json`, `os`, `pathlib`, `app.core.errors`, `app.remote_execution.runtime`, `app.runtime_qualification.evidence`. Update `main.py` and `.gitignore`.

- [ ] **Step 4: Run GREEN**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_certificate_install.py backend/tests/test_execution_certificate.py backend/tests/test_executor_registry.py -q
```

Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime_qualification/install.py backend/app/main.py .gitignore backend/tests/test_certificate_install.py
git commit -m "feat: add operator certificate install and store"
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
qualify --plugin <id> [--plugin-version <v>] --model-release <id> --executor <local_cpu|local_gpu|remote_gpu>
certificate install --from <evidence_dir>
certificate list
```

- `main` returns an int exit code; `0` on success, non-zero on `PlatformError`. Errors are printed as `CODE: message` to stderr; no tracebacks.
- All CLI construction is injectable (settings/provider/probe overrides) so tests never need a real GPU or a running server.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_cli.py` covering:
1. `build_parser` exposes the four subcommands with the documented flags.
2. `runtime doctor` prints a structured report and exits 0 on a CPU-only fake environment.
3. `qualify --executor local_cpu` writes evidence under the data root and exits 0; `--executor local_gpu` writes deferred (not-passed) evidence and exits non-zero.
4. `certificate install --from <dir>` on valid evidence installs; on tampered evidence exits non-zero with a `QUALIFICATION_EVIDENCE_INVALID`-style message and does not mutate the store.
5. `certificate list` lists installed certificates (empty when none).
6. Unknown subcommand/flags exit non-zero with a usage message (no traceback).

- [ ] **Step 2: Run and confirm RED**

```powershell
& "...\.venv\Scripts\python.exe" -m pytest backend/tests/test_cli.py -q
```

Expected RED: `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Minimal implementation**

Create `cli.py` importing `argparse`, `json`, `sys`, `pathlib`, `app.core.config.Settings`, `app.core.errors.PlatformError`, and the `runtime_qualification` modules. Add `[project.scripts]` to `pyproject.toml`.

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

## Task 7: A1/A2 integration + fail-closed matrix

**GPU REQUIRED: NO**

**Files:**
- Test: `backend/tests/test_runtime_qualification_integration.py`

**Interfaces:** none (verification + guard tests; no production change expected).

- [ ] **Step 1: Write the integration/guard tests**

Covering:
1. **A2 sees the installed certificate:** after `install_certificate` for an exact tuple, `ExecutorRegistry.certified_capability(definition, release_id, executor)` becomes non-None; before install it is `None`; `resolve_auto_execution`/Auto `certified` flag flips accordingly — with **no A2 policy change**.
2. **A2 policy untouched:** the Auto decision for a fixed input is identical before/after a certificate install that does not match the requested executor.
3. **A1 exact authority preserved:** `validate_frozen_execution_authority` still fails for a mismatched descriptor; a runtime rotation (new `runtime_ref`) invalidates the old certificate and the executor becomes uncertified until re-qualified.
4. **Certificate disappearance fails closed:** removing the installed certificate reverts `certified_capability` to `None`.
5. **Provider disappearance fails closed:** unchanged A1 behavior.
6. **Plugin cannot self-certify:** there is no production function that installs/synthesizes a certificate from plugin code paths; only `install_certificate` (operator CLI) writes the store — asserted by source scan of `app/` for certificate-store writers.
7. **No A3 internals leak to HTTP:** `GET /api/executor-selection`, `/api/pipelines`, `/api/executor-availability` responses contain no `environment_ref`, interpreter paths, or qualification filesystem paths after install.

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

Assert that `app.runtime_qualification.*` and `app.cli` sources contain none of `cgroup`, `/proc`, `bhq3_memory_gate`, `AutoDL`; that GPU diagnostics are optional (absence of `nvidia-smi` does not raise); and that the control plane remains ML-free.

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

No source commit is created by this verification task (only the baseline worktree, which is removed). Record the differential result in the A3 acceptance evidence.

---

## Plan self-review record

Corrective self-review before commit:

1. **No real GPU required.** Every task is labeled `GPU REQUIRED: NO`; all A3 behavior is exercised with deterministic/injected probes and filesystem/SQLite only. Real GPU/remote qualification is explicitly deferred to Plans B/C.
2. **One runtime-identity authority.** `provider.runtime_ref` remains authoritative; `identity.py` only derives/validates the local `runtime_ref` from generation material and never overrides the provider. No competing identity model.
3. **Exact certificate semantics preserved.** The 7-field key and exact matching are unchanged; no generic certificates.
4. **Plugin cannot self-certify.** Only the operator CLI's `install_certificate` writes the certificate store; Task 7 asserts no plugin-code path writes certificates.
5. **Install requires explicit operator action.** Qualification produces evidence only; `wisa certificate install --from <dir>` is a separate explicit step.
6. **Evidence validation is fail-closed.** Shape, schema version, hash, pass state, staleness, and the exact identity tuple are all validated; tampered/malformed/stale evidence is rejected.
7. **SHA = integrity only.** Evidence/certificate hashes are local integrity fingerprints; no PKI/signer-authenticity claim.
8. **Windows/Linux portable.** No cgroup/`/proc` dependency defines identity; optional `nvidia-smi` is best-effort and its absence is safe.
9. **A2 policy unchanged.** Only the certificate store contents change; the Auto resolver/registry signature is untouched.
10. **A1 authority unchanged.** `FROZEN_AUTHORITY_ITEM_CODES`, `validate_frozen_execution_authority`, and exact runtime matching are untouched; drift/disappearance fail closed.
11. **No frontend scope.** No HTTP API added; CLI output is operator-only.
12. **No A3/B-C scope leakage.** No real GPU execution, no SSH/remote campaign, no endurance/two-host acceptance in A3.
13. **No unnecessary PKI.** Local operator trust only.
14. **No secret/private path leakage to public APIs.** Interpreter paths/`environment_ref`/qualification paths stay operator-only.
15. **Executable via one later Master Prompt.** Tasks are ordered, each with files/interfaces/RED/command/expected RED/minimal implementation/GREEN/expected/commit; no unfinished markers.