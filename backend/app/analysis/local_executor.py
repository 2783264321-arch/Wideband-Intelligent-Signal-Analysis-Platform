"""M9.2-D2 local inference-worker executor providers.

Strict/qualified local CPU/GPU plugin inference must run in a separate,
explicitly configured ML interpreter and never the legacy
``app.analysis.worker``. A strict provider is registered only when both its
interpreter and its immutable runtime generation label are configured;
otherwise that executor is unavailable (fail closed).

Pragmatic Standard Mode adds a built-in, unqualified local CPU provider used
only when no explicit ``local_cpu`` provider is configured. It runs the
release-less built-in CPU detection pipeline in a separate subprocess using the
control-plane interpreter (never inline in the API process) and never
manufactures a qualification certificate.

The worker entrypoint body is implemented in D2B
(``app.analysis.local_inference_worker``); D2 owns only provider construction and
launch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.config import Settings
from app.core.errors import PlatformError
from app.remote_execution.runtime import RuntimeDescriptor
from app.runtime_qualification.identity import validate_configured_local_runtime_ref

_WORKER_MODULE = "app.analysis.local_inference_worker"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_LOCAL_SPECS = {
    "local_cpu": ("local_cpu_python_path", "local_cpu_runtime_ref", "cpu", "float32"),
    "local_gpu": ("local_gpu_python_path", "local_gpu_runtime_ref", "cuda", "float16"),
}

# Platform-owned, bounded CUDA health probe executed INSIDE the configured ML
# interpreter. The control-plane process never imports torch. No user-controlled
# Python code is ever executed; only this fixed script plus the configured index.
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


class LocalInferenceWorkerProvider:
    """Launches a plugin-native local inference worker with a configured interpreter."""

    def __init__(
        self,
        *,
        interpreter: Path,
        runtime_ref: str,
        work_root: Path | None,
        executor_kind: str,
        settings: Settings | None = None,
    ) -> None:
        self._interpreter = Path(interpreter)
        self._runtime_ref = runtime_ref
        self._work_root = Path(work_root) if work_root is not None else None
        self._executor_kind = executor_kind
        self._settings = settings
        self._device_type, self._precision = (
            ("cuda", "float16") if executor_kind == "local_gpu" else ("cpu", "float32")
        )
        # BHQ-3 supports CUDA device index 0 for local_gpu only.
        self._device_index = 0

    @property
    def name(self) -> str:
        return self._executor_kind

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=0 if self._device_type == "cuda" else None,
            precision=self._precision,
            environment_ref=str(self._interpreter),
            environment_label=self._runtime_ref,
        )

    def _probe_interpreter(self) -> tuple[bool, str | None]:
        """Check the configured interpreter and work root. Never assume readiness."""
        if not self._interpreter.is_file():
            return False, "Configured local inference interpreter does not exist."
        if not os.access(self._interpreter, os.X_OK):
            return False, "Configured local inference interpreter is not executable."
        if self._work_root is not None and not self._work_root.is_dir():
            return False, "Configured local inference work root does not exist."
        try:
            result = subprocess.run(
                [str(self._interpreter), "-c", "import sys"],
                shell=False,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False, "Unable to run the configured local inference interpreter."
        if result.returncode != 0:
            return False, "Configured local inference interpreter failed its health check."
        return True, None

    def _probe_gpu(self) -> tuple[bool, str | None]:
        """CUDA health check via the configured ML interpreter (never the control plane)."""
        try:
            result = subprocess.run(
                [str(self._interpreter), "-c", _GPU_PROBE_SCRIPT, str(self._device_index)],
                shell=False,
                capture_output=True,
                text=True,
                timeout=_GPU_PROBE_TIMEOUT_S,
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
        """Check the configured interpreter/work root, then CUDA for local_gpu."""
        ok, reason = self._probe_interpreter()
        if not ok:
            return ok, reason
        if self._executor_kind == "local_gpu":
            return self._probe_gpu()
        return True, None

    def availability(
        self, definition, model_release, recording
    ) -> ExecutorAvailabilityRead:
        from app.pipelines.compatibility import is_input_compatible

        if not is_input_compatible(definition, recording.label_space):
            return ExecutorAvailabilityRead(
                executor=self.name,
                available=False,
                reason_code="INPUT_INCOMPATIBLE",
                reason_message="Pipeline cannot run for this recording label space.",
                remote_profile=None,
                recommended=False,
            )
        ok, reason = self.probe()
        if not ok:
            return ExecutorAvailabilityRead(
                executor=self.name,
                available=False,
                reason_code="EXECUTION_CAPABILITY_UNAVAILABLE",
                reason_message=reason,
                remote_profile=None,
                recommended=False,
            )
        return ExecutorAvailabilityRead(
            executor=self.name,
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile=None,
            recommended=(definition.recommended_executor == self.name),
        )

    def launch(self, run_id: str, *, coordinator_token: str | None) -> int:
        env = os.environ.copy()
        env["WSP_LOCAL_INFERENCE_RUNTIME_REF"] = self._runtime_ref
        if self._settings is not None:
            env["WSP_PROJECT_ROOT"] = str(self._settings.project_root)
            env["WSP_DATA_ROOT"] = str(self._settings.data_root)
            env["WSP_LABEL_SPACE_ROOT"] = str(self._settings.label_space_root)
            env["WSP_DATABASE_URL"] = str(self._settings.database_url)
            if self._settings.local_asset_paths is not None:
                env["WSP_LOCAL_ASSET_PATHS_JSON"] = json.dumps(self._settings.local_asset_paths)
        process = subprocess.Popen(
            [str(self._interpreter), "-m", _WORKER_MODULE, run_id],
            cwd=_BACKEND_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        return process.pid


def build_local_providers(settings: Settings) -> dict[str, LocalInferenceWorkerProvider]:
    """Register local providers only when interpreter AND runtime_ref are configured.

    Before a provider is constructed, the configured runtime_ref must satisfy the
    operator-owned ``WSP_RUNTIME_FAMILY`` authority (fail closed on disagreement);
    ``runtime_family=None`` preserves legacy repo-default compatibility.
    """
    providers: dict[str, LocalInferenceWorkerProvider] = {}
    for executor_kind, (path_attr, ref_attr, _device, _precision) in _LOCAL_SPECS.items():
        interpreter = getattr(settings, path_attr)
        runtime_ref = getattr(settings, ref_attr)
        if interpreter is None or runtime_ref is None:
            continue
        validate_configured_local_runtime_ref(
            executor=executor_kind,
            runtime_ref=runtime_ref,
            runtime_family=settings.runtime_family,
        )
        providers[executor_kind] = LocalInferenceWorkerProvider(
            interpreter=interpreter,
            runtime_ref=runtime_ref,
            work_root=settings.local_inference_work_root,
            executor_kind=executor_kind,
            settings=settings,
        )
    return providers


# Pragmatic Standard Mode: a built-in, unqualified local CPU path for the
# platform's own release-less CPU detection pipeline. It runs the real inference
# in a separate subprocess (never inline in the API process) using the
# control-plane interpreter. It never replaces an explicitly configured,
# qualified local_cpu provider.
STANDARD_LOCAL_CPU_RUNTIME_REF = "local:builtin:cpu:standard"


class StandardLocalCpuProvider(LocalInferenceWorkerProvider):
    """Built-in local CPU provider used when no explicit local_cpu is configured."""

    standard_mode = True

    def __init__(
        self,
        *,
        interpreter: Path,
        work_root: Path | None,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(
            interpreter=interpreter,
            runtime_ref=STANDARD_LOCAL_CPU_RUNTIME_REF,
            work_root=work_root,
            executor_kind="local_cpu",
            settings=settings,
        )


def build_standard_local_cpu_provider(settings: Settings) -> StandardLocalCpuProvider:
    return StandardLocalCpuProvider(
        interpreter=Path(sys.executable),
        work_root=settings.local_inference_work_root,
        settings=settings,
    )


def build_deployment_local_providers(settings: Settings) -> dict[str, LocalInferenceWorkerProvider]:
    """Deployment provider set for Standard Mode.

    Strict, explicitly configured providers always take precedence. When no
    explicit ``local_cpu`` provider is configured, the built-in Standard-Mode
    local CPU provider fills the gap for release-less built-in pipelines.
    """
    providers: dict[str, LocalInferenceWorkerProvider] = dict(build_local_providers(settings))
    if "local_cpu" not in providers:
        providers["local_cpu"] = build_standard_local_cpu_provider(settings)
    return providers
