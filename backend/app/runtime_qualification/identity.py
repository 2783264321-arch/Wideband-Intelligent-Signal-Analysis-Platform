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

_IDENTITY_PROBE_SCRIPT = """
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


def collect_identity_material(python_path: Path | None) -> dict:
    """Collect identity material inside the configured ML interpreter.

    The control-plane process never imports torch/ultralytics. Absent packages
    are recorded as ``null``.
    """
    if python_path is None:
        raise _unavailable("No ML interpreter is configured for identity material.")
    path = Path(python_path)
    if not path.is_file():
        raise _unavailable("Configured ML interpreter does not exist.")
    try:
        result = subprocess.run(
            [str(path), "-c", _IDENTITY_PROBE_SCRIPT],
            shell=False,
            capture_output=True,
            text=True,
            timeout=_IDENTITY_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _unavailable("Unable to run the configured ML interpreter.") from exc
    if result.returncode != 0:
        raise _unavailable("Configured ML interpreter failed the identity probe.")
    payload = None
    for line in reversed((result.stdout or "").strip().splitlines()):
        try:
            candidate = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if payload is None:
        raise _unavailable("Identity probe returned no valid material.")
    if set(payload) != set(LOCAL_CPU_V1_FIELDS):
        raise _unavailable("Identity probe returned malformed material.")
    return payload
