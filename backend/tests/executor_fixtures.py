"""Shared D2C test doubles: certified-capable providers and an exact-executor registry.

These fakes exercise dispatch/lifecycle without a real runtime or certificate
store. Real certification logic is covered by
``test_executor_registry.py`` / ``test_execution_certificate.py``; real local
execution evidence is D2B.5 (``test_local_cpu_acceptance.py``).
"""
from __future__ import annotations

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability
from app.remote_execution.runtime import RuntimeDescriptor


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        device_type: str | None = None,
        precision: str | None = None,
        runtime_ref: str | None = None,
        available: bool = True,
        reason_code: str | None = None,
        pid: int = 4242,
        probe=None,
        launcher=None,
    ) -> None:
        self.name = name
        self._device_type = device_type or ("cuda" if name == "remote_gpu" else "cpu")
        self._precision = precision or ("float16" if name == "remote_gpu" else "float32")
        self._runtime_ref = runtime_ref or f"fake:{name}"
        self._available = available
        self._reason_code = reason_code
        self._pid = pid
        self._probe = probe
        self._launcher = launcher
        self.launches: list[tuple[str, str | None]] = []

    @property
    def runtime_ref(self) -> str:
        return self._runtime_ref

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type=self._device_type,
            device_index=0 if self._device_type == "cuda" else None,
            precision=self._precision,
            environment_ref=f"/fake/{self.name}",
            environment_label=self._runtime_ref,
        )

    def availability(self, definition, model_release, recording):
        from app.pipelines.compatibility import is_input_compatible

        if not is_input_compatible(definition, recording.label_space):
            return ExecutorAvailabilityRead(
                executor=self.name,
                available=False,
                reason_code="INPUT_INCOMPATIBLE",
                reason_message="incompatible",
                remote_profile=None,
                recommended=False,
            )
        if self._probe is not None:
            return self._probe.availability(recording, definition, recording.source_data_sha256)
        remote_profile = self.name if self.name == "remote_gpu" else None
        if self.name == "remote_gpu" and self._launcher is None:
            return ExecutorAvailabilityRead(
                executor=self.name,
                available=False,
                reason_code="REMOTE_EXECUTOR_UNAVAILABLE",
                reason_message="No remote executor profile is configured.",
                remote_profile=None,
                recommended=False,
            )
        if not self._available:
            return ExecutorAvailabilityRead(
                executor=self.name,
                available=False,
                reason_code=self._reason_code or "REMOTE_EXECUTOR_UNAVAILABLE",
                reason_message="unavailable",
                remote_profile=remote_profile,
                recommended=False,
            )
        return ExecutorAvailabilityRead(
            executor=self.name,
            available=True,
            reason_code=None,
            reason_message=None,
            remote_profile=remote_profile,
            recommended=(definition.recommended_executor == self.name),
        )

    def launch(self, run_id: str, *, coordinator_token: str | None) -> int:
        self.launches.append((run_id, coordinator_token))
        if self._launcher is not None:
            return self._launcher.launch(run_id, coordinator_token)
        return self._pid


class FakeRegistry:
    """Exact-executor registry seam for service tests (no real certificates)."""

    def __init__(self, providers) -> None:
        self._providers = dict(providers)
        self.availability_calls: list[tuple[str, str]] = []

    def provider(self, executor: str) -> FakeProvider:
        provider = self._providers.get(executor)
        if provider is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE",
                f"No executor provider is registered for '{executor}'.",
            )
        return provider

    def availability_for(self, definition, model_release, recording, executor):
        self.availability_calls.append((definition.plugin_id, executor))
        model_release_id = (
            None if model_release is None else model_release.release.model_release_id
        )
        if self.certified_capability(definition, model_release_id, executor) is None:
            return ExecutorAvailabilityRead(
                executor=executor,
                available=False,
                reason_code="EXECUTION_NOT_CERTIFIED",
                reason_message="exact requested executor is not certified",
                remote_profile=None,
                recommended=False,
            )
        return self.provider(executor).availability(definition, model_release, recording)

    def certified_capability(self, definition, model_release_id, executor):
        provider = self._providers.get(executor)
        if provider is None or executor not in definition.executors_supported:
            return None
        descriptor = provider.runtime_descriptor()
        return ExecutionCapability(executor, descriptor.device_type, descriptor.precision)

    def deployment_qualified_executors(self, definition, model_release_id):
        supported = sorted(
            name for name in self._providers if name in definition.executors_supported
        )
        recommended = definition.recommended_execution
        return supported, (recommended if recommended in supported else None)
