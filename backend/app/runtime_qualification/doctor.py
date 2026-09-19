"""A3 Task 1: portable runtime doctor report model + injectable probes.

Torch-free and portable. The module never imports CUDA/torch, has no Linux-only
container-filesystem dependency, and never rewrites a configured
``WSP_LOCAL_*_RUNTIME_REF``. Optional GPU diagnostics are attempted only for CUDA
device types and are never identity material.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import platform as _platform
import json
import os
import shutil
import subprocess
import sys
from typing import Callable, Protocol, Sequence

from app.core.config import Settings

SCHEMA_VERSION = 1

IDENTITY_MATCH = "match"
IDENTITY_MISMATCH = "mismatch"
IDENTITY_LEGACY_OPAQUE = "legacy_opaque"
IDENTITY_NOT_CONFIGURED = "not_configured"
IDENTITY_UNAVAILABLE = "unavailable"

IDENTITY_STATUSES = (
    IDENTITY_MATCH,
    IDENTITY_MISMATCH,
    IDENTITY_LEGACY_OPAQUE,
    IDENTITY_NOT_CONFIGURED,
    IDENTITY_UNAVAILABLE,
)


@dataclass(frozen=True)
class InterpreterReport:
    available: bool
    version: str | None
    executable_id: str | None  # PRIVATE (operator-only); never serialized to HTTP


@dataclass(frozen=True)
class GpuReport:
    applicable: bool
    available: bool
    device_name: str | None
    compute_capability: str | None
    cuda_runtime: str | None
    driver_version: str | None
    reason: str | None  # doctor diagnostic only; never identity material


@dataclass(frozen=True)
class ProviderSpec:
    executor: str
    python_path: Path | None
    runtime_ref: str | None
    device_type: str
    precision: str
    identity_scheme: str = IDENTITY_UNAVAILABLE


@dataclass(frozen=True)
class DiscoveredRuntime:
    """One candidate local interpreter and the ML stack actually importable on it.

    Diagnostic-only (operator help), never identity material: discovery helps an
    operator point ``WSP_LOCAL_CPU_PYTHON_PATH`` at an interpreter that actually
    has pytorch installed, instead of a generic python that does not.
    """

    python_path: str
    available: bool
    version: str | None
    torch: bool
    ultralytics: bool
    is_control_plane: bool
    is_configured_local_cpu: bool


@dataclass(frozen=True)
class ProviderReport:
    executor: str
    device_type: str
    precision: str
    runtime_ref: str | None
    configured: bool
    identity_scheme: str
    identity_status: str
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

    def to_operator_json(self) -> dict:
        """Operator-only projection (may include the private executable_id)."""
        return asdict(self)


class InterpreterProbe(Protocol):
    def inspect(self, python_path: Path | None) -> InterpreterReport: ...


class GpuProbe(Protocol):
    def inspect(self) -> GpuReport: ...


class SubprocessInterpreterProbe:
    """Best-effort, read-only interpreter diagnostic (never identity)."""

    def inspect(self, python_path: Path | None) -> InterpreterReport:
        if python_path is None:
            return InterpreterReport(False, None, None)
        path = Path(python_path)
        if not path.is_file():
            return InterpreterReport(False, None, str(path))
        try:
            result = subprocess.run(
                [str(path), "-c", "import sys; print(sys.version.split()[0])"],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return InterpreterReport(False, None, str(path))
        if result.returncode != 0:
            return InterpreterReport(False, None, str(path))
        lines = (result.stdout or "").strip().splitlines()
        return InterpreterReport(True, lines[-1] if lines else None, str(path))


_DISCOVERY_PROBE_SNIPPET = (
    "import json, sys\n"
    "import importlib.util as u\n"
    "print(json.dumps({'python': sys.version.split()[0],"
    " 'torch': u.find_spec('torch') is not None,"
    " 'ultralytics': u.find_spec('ultralytics') is not None}))\n"
)


def _inspect_ml_stack(python_path: Path) -> DiscoveredRuntime:
    """Best-effort read-only ML-stack inspection; never crashes the doctor."""
    payload = {"python": None, "torch": False, "ultralytics": False}
    available = False
    try:
        result = subprocess.run(
            [str(python_path), "-c", _DISCOVERY_PROBE_SNIPPET],
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        available = result.returncode == 0
        for line in reversed((result.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    payload = parsed
                    available = payload.get("python") is not None
                    break
    except (OSError, subprocess.SubprocessError):
        available = False
    return DiscoveredRuntime(
        python_path=str(python_path),
        available=available,
        version=payload.get("python") if available else None,
        torch=bool(payload.get("torch")),
        ultralytics=bool(payload.get("ultralytics")),
        is_control_plane=False,
        is_configured_local_cpu=False,
    )


def _python_in_env(env_dir: Path) -> Path | None:
    candidate = env_dir / ("python.exe" if os.name == "nt" else "bin/python")
    return candidate if candidate.is_file() else None


def _conda_env_python_paths() -> list[Path]:
    """Best-effort enumeration of conda environments, without running conda.

    Discovery is diagnostic only; it reads operator-owned environment variables
    (CONDA_ENVS_PATH / CONDA_PREFIX / CONDA_EXE) and a few conventional install
    roots, and never invokes conda or rewrites anything.
    """
    envs_roots: list[Path] = []

    configured = os.environ.get("CONDA_ENVS_PATH")
    if configured:
        envs_roots.extend(Path(part) for part in configured.split(os.pathsep) if part)

    prefix = os.environ.get("CONDA_PREFIX")
    if prefix:
        parent = Path(prefix).parent
        if parent.name.lower() == "envs":
            envs_roots.append(parent)

    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        # <root>/Scripts/conda.exe or <root>/condabin/conda.bat -> <root>/envs
        for candidate_root in (Path(conda_exe).parent.parent, Path(conda_exe).parent.parent.parent):
            envs_roots.append(candidate_root / "envs")

    home = Path.home()
    for base in (
        home / "miniconda3",
        home / "anaconda3",
        home / "AppData" / "Local" / "miniconda3",
        home / "AppData" / "Local" / "Continuum" / "anaconda3",
    ):
        envs_roots.append(base / "envs")

    paths: list[Path] = []
    for root in envs_roots:
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for env_dir in children:
            python = _python_in_env(env_dir)
            if python is not None:
                paths.append(python)
    return paths


def _extra_python_paths() -> list[Path]:
    """Operator-declared interpreters (WSP_EXTRA_PYTHON_PATHS, os.pathsep list)."""
    configured = os.environ.get("WSP_EXTRA_PYTHON_PATHS")
    if not configured:
        return []
    return [Path(part) for part in configured.split(os.pathsep) if part.strip()]


def discover_local_runtimes(settings: Settings) -> list[DiscoveredRuntime]:
    """Best-effort scan for local interpreters that can run pytorch inference.

    Diagnostics only: it never imports torch into this process and never rewrites
    configuration. Results help the operator decide where each pipeline can run.
    """
    control_plane = Path(sys.executable)
    candidates: list[tuple[Path, bool]] = []
    if settings.local_cpu_python_path is not None:
        candidates.append((Path(settings.local_cpu_python_path), True))
    candidates.append((control_plane, False))
    candidates.extend((path, False) for path in _extra_python_paths())
    candidates.extend((path, False) for path in _conda_env_python_paths())

    seen: set[str] = set()
    discovered: list[DiscoveredRuntime] = []
    for path, is_configured in candidates:
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        report = _inspect_ml_stack(path)
        if report.available:
            discovered.append(
                DiscoveredRuntime(
                    python_path=report.python_path,
                    available=True,
                    version=report.version,
                    torch=report.torch,
                    ultralytics=report.ultralytics,
                    is_control_plane=path.resolve() == control_plane.resolve(),
                    is_configured_local_cpu=is_configured,
                )
            )
    return discovered


class NvidiaSmiGpuProbe:
    """Optional CUDA diagnostic only. Absence of ``nvidia-smi`` is safe."""
    def inspect(self) -> GpuReport:
        exe = shutil.which("nvidia-smi")
        if exe is None:
            return GpuReport(True, False, None, None, None, None, "nvidia-smi not found")
        try:
            result = subprocess.run(
                [exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return GpuReport(True, False, None, None, None, None, "nvidia-smi could not be run")
        if result.returncode != 0:
            return GpuReport(True, False, None, None, None, None, "nvidia-smi exited nonzero")
        lines = (result.stdout or "").strip().splitlines()
        parts = [part.strip() for part in lines[0].split(",")] if lines else []
        name = parts[0] if parts and parts[0] else None
        driver = parts[1] if len(parts) > 1 and parts[1] else None
        return GpuReport(True, True, name, None, None, driver, None)


def _default_identity_resolver(spec: ProviderSpec, settings: Settings) -> tuple:
    if spec.runtime_ref is None:
        return (None, None, spec.identity_scheme, IDENTITY_NOT_CONFIGURED)
    if spec.identity_scheme == IDENTITY_LEGACY_OPAQUE:
        return (spec.runtime_ref, None, IDENTITY_LEGACY_OPAQUE, IDENTITY_LEGACY_OPAQUE)
    return (spec.runtime_ref, None, spec.identity_scheme, IDENTITY_NOT_CONFIGURED)


def _not_applicable_gpu(device_type: str) -> GpuReport:
    return GpuReport(
        applicable=False,
        available=False,
        device_name=None,
        compute_capability=None,
        cuda_runtime=None,
        driver_version=None,
        reason=f"{device_type} runtime; GPU diagnostics not applicable",
    )


def build_runtime_doctor_report(
    *,
    settings: Settings,
    provider_specs: Sequence[ProviderSpec],
    interpreter_probe: InterpreterProbe,
    gpu_probe: GpuProbe,
    identity_resolver: Callable[[ProviderSpec, Settings], tuple] | None = None,
    clock: Callable[[], datetime] | None = None,
    host_platform: str | None = None,
) -> RuntimeDoctorReport:
    resolver = identity_resolver or _default_identity_resolver
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    providers: list[ProviderReport] = []
    for spec in provider_specs:
        interpreter = interpreter_probe.inspect(spec.python_path)
        gpu = gpu_probe.inspect() if spec.device_type == "cuda" else _not_applicable_gpu(spec.device_type)
        configured_ref, derived_ref, scheme, status = resolver(spec, settings)
        providers.append(
            ProviderReport(
                executor=spec.executor,
                device_type=spec.device_type,
                precision=spec.precision,
                runtime_ref=spec.runtime_ref,
                configured=spec.runtime_ref is not None,
                identity_scheme=scheme,
                identity_status=status,
                configured_runtime_ref=configured_ref,
                derived_runtime_ref=derived_ref,
                interpreter=interpreter,
                gpu=gpu,
            )
        )
    return RuntimeDoctorReport(
        schema_version=SCHEMA_VERSION,
        created_at=now.isoformat(),
        runtime_family=settings.runtime_family,
        control_plane_python=sys.executable,
        platform_system=host_platform or _platform.system(),
        architecture=_platform.machine(),
        providers=tuple(providers),
        notes=("Doctor never rewrites WSP_LOCAL_*_RUNTIME_REF.",),
    )


def build_provider_specs(
    settings: Settings,
    providers: object,
    *,
    scheme_for: Callable[[str, str], str] | None = None,
) -> tuple[ProviderSpec, ...]:
    """Derive doctor specs from registered providers (no plugin-id branching)."""
    if not hasattr(providers, "items"):
        return ()
    resolve = scheme_for or (lambda executor, runtime_ref: IDENTITY_UNAVAILABLE)
    specs: list[ProviderSpec] = []
    for name, provider in providers.items():  # type: ignore[union-attr]
        descriptor = provider.runtime_descriptor()
        runtime_ref = provider.runtime_ref
        specs.append(
            ProviderSpec(
                executor=name,
                python_path=getattr(settings, f"{name}_python_path", None),
                runtime_ref=runtime_ref,
                device_type=descriptor.device_type,
                precision=descriptor.precision,
                identity_scheme=resolve(name, runtime_ref),
            )
        )
    return tuple(specs)
