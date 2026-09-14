"""A3 Task 2: scheme-versioned runtime identity derivation/validation.

The scheme label is recorded in evidence/report but is never part of the
``runtime_ref`` string. This module is a pure derivation/validation helper; the
single authority for runtime identity remains ``provider.runtime_ref``.

No historical runtime hash is hard-coded here and the identity scheme is never
decided by pattern-matching the ``runtime_ref`` string: ``legacy_opaque`` comes
from repo-default certificate provenance supplied by the caller.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

from app.core.errors import PlatformError

BHQ3_GPU_V1 = "bhq3_gpu_v1"
LOCAL_CPU_V1 = "local_cpu_v1"
LEGACY_OPAQUE = "legacy_opaque"
UNAVAILABLE = "unavailable"

BHQ3_GPU_V1_FIELDS = (
    "python",
    "torch",
    "torch_cuda",
    "ultralytics",
    "numpy",
    "scipy",
    "device_name",
    "compute_capability",
    "driver_version",
    "cuda_available",
)

LOCAL_CPU_V1_FIELDS = (
    "python",
    "platform_system",
    "architecture",
    "torch",
    "ultralytics",
    "numpy",
    "scipy",
)

_SCHEME_FIELDS = {
    BHQ3_GPU_V1: BHQ3_GPU_V1_FIELDS,
    LOCAL_CPU_V1: LOCAL_CPU_V1_FIELDS,
}

_LOCAL_REF_RE = re.compile(r"^local:(?P<family>[^:]+):(?P<kind>cpu|gpu):(?P<generation>[0-9a-f]{12})$")

_LOCAL_CPU_IDENTITY_PROBE_SCRIPT = """
import json, platform, sys
def _version(name):
    try:
        module = __import__(name)
    except Exception:
        return None
    return getattr(module, "__version__", None)
material = {
    "python": sys.version.split()[0],
    "platform_system": platform.system(),
    "architecture": platform.machine(),
    "torch": _version("torch"),
    "ultralytics": _version("ultralytics"),
    "numpy": _version("numpy"),
    "scipy": _version("scipy"),
}
print(json.dumps(material))
"""

# Fixed platform-owned torch/CUDA probe, executed INSIDE the configured ML
# interpreter. It NEVER invents a value: on any failure it reports ok=False so
# the collector fails closed with RUNTIME_IDENTITY_UNAVAILABLE. There is no
# "unknown" fallback: missing exact material can never be hashed into an identity.
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

# Raw torch-probe material fields: BHQ3_GPU_V1_FIELDS with driver_version replaced
# by the internal device_count validation field.
_BHQ3_GPU_PROBE_FIELDS = (
    "python",
    "torch",
    "torch_cuda",
    "ultralytics",
    "numpy",
    "scipy",
    "device_name",
    "compute_capability",
    "cuda_available",
    "device_count",
)

# Fixed platform-owned driver query, locked to device 0 so multi-GPU hosts still
# yield exactly one line. No user-supplied shell input; invoked with shell=False.
_BHQ3_GPU_DRIVER_QUERY = (
    "nvidia-smi",
    "-i",
    "0",
    "--query-gpu=driver_version",
    "--format=csv,noheader",
)

_IDENTITY_PROBE_TIMEOUT_S = 120


def _invalid(message: str) -> PlatformError:
    return PlatformError("RUNTIME_IDENTITY_INVALID", message)


def _mismatch(message: str) -> PlatformError:
    return PlatformError("RUNTIME_IDENTITY_MISMATCH", message)


def _unavailable(message: str) -> PlatformError:
    return PlatformError("RUNTIME_IDENTITY_UNAVAILABLE", message)


def canonical_material_bytes(material: dict) -> bytes:
    return json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_generation(material: dict) -> str:
    return hashlib.sha256(canonical_material_bytes(material)).hexdigest()[:12]


def derive_local_runtime_ref(*, family: str, kind: str, generation: str) -> str:
    return f"local:{family}:{kind}:{generation}"


def derive_generation_for_scheme(*, scheme: str, material: dict) -> str:
    fields = _SCHEME_FIELDS.get(scheme)
    if fields is None:
        raise _invalid(f"Unknown runtime identity scheme '{scheme}'.")
    if not isinstance(material, dict):
        raise _invalid("Runtime identity material must be an object.")
    if set(material) != set(fields):
        raise _invalid(
            f"Runtime identity material fields do not match scheme '{scheme}'."
        )
    return derive_generation(material)


def parse_local_runtime_ref(runtime_ref: str) -> tuple[str, str] | None:
    if not isinstance(runtime_ref, str):
        return None
    match = _LOCAL_REF_RE.fullmatch(runtime_ref)
    if match is None:
        return None
    return match.group("family"), match.group("kind")


_EXPECTED_LOCAL_KIND = {
    "local_cpu": "cpu",
    "local_gpu": "gpu",
}


def validate_configured_local_runtime_ref(
    *, executor: str, runtime_ref: str, runtime_family: str | None
) -> None:
    """Fail closed when a configured local runtime ref disagrees with the authority.

    ``runtime_family is None`` preserves backward compatibility: legacy
    repo-default runtime refs are NOT invalidated merely for a missing family.
    When a family IS configured, the ref family must match it and the ref kind
    must match the executor's expected kind. The ref is never rewritten.
    """
    if runtime_family is None:
        return
    parsed = parse_local_runtime_ref(runtime_ref)
    if parsed is None:
        raise PlatformError(
            "RUNTIME_FAMILY_MISMATCH",
            f"Configured {executor} runtime_ref is malformed.",
        )
    family, kind = parsed
    if family != runtime_family:
        raise PlatformError(
            "RUNTIME_FAMILY_MISMATCH",
            f"Configured {executor} runtime_ref family '{family}' does not match "
            f"WSP_RUNTIME_FAMILY '{runtime_family}'.",
        )
    expected_kind = _EXPECTED_LOCAL_KIND.get(executor)
    if expected_kind is not None and kind != expected_kind:
        raise PlatformError(
            "RUNTIME_FAMILY_MISMATCH",
            f"Configured {executor} runtime_ref kind '{kind}' is not '{expected_kind}'.",
        )


def resolve_identity_scheme(
    *,
    executor: str,
    runtime_ref: str | None,
    qualification_context: str | None,
    repo_default_runtime_refs: frozenset[str],
    material_available: bool,
) -> str:
    """Resolve the identity scheme from caller-supplied authority.

    ``legacy_opaque`` is returned only when the ref is already represented by
    repo-default certificate provenance (``repo_default_runtime_refs``) AND no
    new A3 derivation is being performed. The decision never inspects the shape
    of the ``runtime_ref`` string.
    """
    if executor == "local_gpu":
        return BHQ3_GPU_V1 if material_available else UNAVAILABLE
    if executor == "local_cpu":
        if qualification_context is not None:
            return LOCAL_CPU_V1 if material_available else UNAVAILABLE
        if runtime_ref is not None and runtime_ref in repo_default_runtime_refs:
            return LEGACY_OPAQUE
        return UNAVAILABLE
    return UNAVAILABLE


def validate_runtime_ref_against_material(
    *,
    runtime_ref: str,
    family: str,
    kind: str,
    scheme: str,
    material: dict,
) -> None:
    parsed = parse_local_runtime_ref(runtime_ref)
    if parsed is None:
        raise _invalid("runtime_ref is not a well-formed local runtime reference.")
    ref_family, ref_kind = parsed
    generation = derive_generation_for_scheme(scheme=scheme, material=material)
    if ref_family != family or ref_kind != kind:
        raise _mismatch("runtime_ref family/kind does not match the declared identity.")
    expected = derive_local_runtime_ref(family=family, kind=kind, generation=generation)
    if runtime_ref != expected:
        raise _mismatch("runtime_ref does not reproduce from the supplied identity material.")


def _run_probe(python_path: Path | None, script: str, *, runner=None):
    if python_path is None:
        raise _unavailable("No ML interpreter is configured for identity material.")
    path = Path(python_path)
    if not path.is_file():
        raise _unavailable("Configured ML interpreter does not exist.")
    run = runner or subprocess.run
    try:
        result = run(
            [str(path), "-c", script],
            shell=False,
            capture_output=True,
            text=True,
            timeout=_IDENTITY_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _unavailable("Unable to run the configured ML interpreter.") from exc
    if result.returncode != 0:
        raise _unavailable("Configured ML interpreter failed the identity probe.")
    return result


def _last_json_object(stdout: str) -> dict:
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            candidate = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(candidate, dict):
            return candidate
    raise _unavailable("Identity probe returned no valid material.")


def collect_local_cpu_identity_material(python_path: Path | None, *, runner=None) -> dict:
    """Collect ``local_cpu_v1`` material inside the configured ML interpreter.

    The control-plane process never imports torch/ultralytics. Absent packages
    are recorded as ``null``.
    """
    result = _run_probe(python_path, _LOCAL_CPU_IDENTITY_PROBE_SCRIPT, runner=runner)
    payload = _last_json_object(result.stdout)
    if set(payload) != set(LOCAL_CPU_V1_FIELDS):
        raise _invalid("Identity probe returned material for the wrong scheme.")
    return payload


def assemble_bhq3_gpu_material(probe_material: dict, driver_version: str) -> dict:
    """Pure, fail-closed assembly of the canonical ``bhq3_gpu_v1`` material.

    Drops the internal ``device_count`` and adds the exact ``driver_version``.
    Any missing/invalid field, an unavailable CUDA device, or an empty,
    multi-line, whitespace-only or ``"unknown"`` driver value fails closed with
    ``RUNTIME_IDENTITY_UNAVAILABLE``; no partial material is ever returned.
    """
    if not isinstance(probe_material, dict):
        raise _unavailable("GPU identity probe material is not an object.")
    if probe_material.get("cuda_available") is not True:
        raise _unavailable("CUDA is not available for the GPU identity material.")
    try:
        device_count = int(probe_material.get("device_count"))
    except (TypeError, ValueError):
        raise _unavailable("GPU identity device_count is missing or invalid.")
    if device_count < 1:
        raise _unavailable("GPU identity device_count is less than one.")
    if not isinstance(driver_version, str):
        raise _unavailable("GPU driver version is not a string.")
    driver = driver_version.strip()
    if "\n" in driver or "\r" in driver:
        raise _unavailable("GPU driver query returned multiple lines.")
    if not driver or driver.lower() == "unknown":
        raise _unavailable("GPU driver version is missing or unknown.")
    material: dict = {"driver_version": driver}
    for field in BHQ3_GPU_V1_FIELDS:
        if field in ("driver_version", "cuda_available"):
            continue
        value = probe_material.get(field)
        if not isinstance(value, str) or not value:
            raise _unavailable(f"GPU identity field '{field}' is missing or invalid.")
        material[field] = value
    material["cuda_available"] = True
    if set(material) != set(BHQ3_GPU_V1_FIELDS):
        raise _unavailable("GPU identity material does not match the bhq3_gpu_v1 fields.")
    return material


def collect_bhq3_gpu_identity_material(python_path: Path | None, *, runner=None) -> dict:
    """Collect ``bhq3_gpu_v1`` material inside the configured ML interpreter.

    Execution order (fail closed at every step):
      1. interpreter exists
      2. fixed torch/CUDA probe runs (require returncode 0 and ``ok`` true)
      3. probe material has EXACTLY the raw probe fields (cross-scheme rejection)
      4. fixed device-0 driver query runs (returncode 0, exactly one non-empty line)
      5. pure assembly drops ``device_count`` and adds ``driver_version``
    """
    result = _run_probe(python_path, _BHQ3_GPU_TORCH_PROBE_SCRIPT, runner=runner)
    payload = _last_json_object(result.stdout)
    if payload.get("ok") is not True:
        reason = payload.get("reason") or "GPU identity probe failed."
        raise _unavailable(str(reason))
    probe_material = payload.get("material")
    if not isinstance(probe_material, dict):
        raise _unavailable("GPU identity probe returned no material.")
    if set(probe_material) != set(_BHQ3_GPU_PROBE_FIELDS):
        raise _invalid("GPU identity probe material does not match the bhq3_gpu_v1 probe fields.")

    run = runner or subprocess.run
    try:
        driver_result = run(
            list(_BHQ3_GPU_DRIVER_QUERY),
            shell=False,
            capture_output=True,
            text=True,
            timeout=_IDENTITY_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _unavailable("Unable to run the GPU driver query.") from exc
    if driver_result.returncode != 0:
        raise _unavailable("GPU driver query exited nonzero.")
    driver_lines = [line for line in (driver_result.stdout or "").strip().splitlines() if line.strip()]
    if len(driver_lines) != 1:
        raise _unavailable("GPU driver query did not return exactly one line.")
    return assemble_bhq3_gpu_material(probe_material, driver_lines[0])


def collect_identity_material(python_path: Path | None, *, scheme: str, runner=None) -> dict:
    """Scheme-aware identity material collection (single entry point).

    The scheme is required (no CPU default) so CPU material can never silently
    reach a GPU scheme. An unknown scheme, or material whose field set does not
    match the scheme, fails closed with ``RUNTIME_IDENTITY_INVALID``.
    """
    if scheme == BHQ3_GPU_V1:
        material = collect_bhq3_gpu_identity_material(python_path, runner=runner)
    elif scheme == LOCAL_CPU_V1:
        material = collect_local_cpu_identity_material(python_path, runner=runner)
    else:
        raise _invalid(f"Unknown runtime identity scheme '{scheme}'.")
    if set(material) != set(_SCHEME_FIELDS[scheme]):
        raise _invalid(f"Collected material does not match scheme '{scheme}'.")
    return material
