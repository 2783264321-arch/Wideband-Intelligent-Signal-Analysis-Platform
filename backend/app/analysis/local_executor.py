"""M9.2-D2 local inference-worker executor providers.

Local CPU/GPU plugin inference must run in a separate, explicitly configured ML
interpreter, never the control-plane ``sys.executable`` and never the legacy
``app.analysis.worker``. A provider is registered only when both its interpreter
and its immutable runtime generation label are configured; otherwise that executor
is unavailable (fail closed).

The worker entrypoint body is implemented in D2B
(``app.analysis.local_inference_worker``); D2 owns only provider construction and
launch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.config import Settings
from app.core.errors import PlatformError
from app.remote_execution.runtime import RuntimeDescriptor

_WORKER_MODULE = "app.analysis.local_inference_worker"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_LOCAL_SPECS = {
    "local_cpu": ("local_cpu_python_path", "local_cpu_runtime_ref", "cpu", "float32"),
    "local_gpu": ("local_gpu_python_path", "local_gpu_runtime_ref", "cuda", "float16"),
}


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

    def probe(self) -> tuple[bool, str | None]:
        """Check the configured interpreter and work root. Never assume CPU is ready."""
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
    """Register local providers only when interpreter AND runtime_ref are configured."""
    providers: dict[str, LocalInferenceWorkerProvider] = {}
    for executor_kind, (path_attr, ref_attr, _device, _precision) in _LOCAL_SPECS.items():
        interpreter = getattr(settings, path_attr)
        runtime_ref = getattr(settings, ref_attr)
        if interpreter is None or runtime_ref is None:
            continue
        providers[executor_kind] = LocalInferenceWorkerProvider(
            interpreter=interpreter,
            runtime_ref=runtime_ref,
            work_root=settings.local_inference_work_root,
            executor_kind=executor_kind,
            settings=settings,
        )
    return providers
