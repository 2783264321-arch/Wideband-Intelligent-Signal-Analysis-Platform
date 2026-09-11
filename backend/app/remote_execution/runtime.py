"""M9.2-D1 runtime primitives.

- ``RuntimeDescriptor`` describes the concrete execution environment of an
  inference run (executor, device, precision, environment). Only its public
  projection may enter an Analysis Package; the full descriptor (including the
  private ``environment_ref``) stays in internal provenance.
- ``ExecutionCertificate`` / ``ExecutionCertificateStore`` are platform-owned
  certification records precisely bound to
  ``(plugin_id, plugin_version, model_release_id, executor, device_type,
  precision, runtime_ref)``. A combination without an exact certificate is not
  runnable.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from app.analysis.schema import ExecutorAvailabilityRead
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition


def _descriptor_invalid(message: str) -> PlatformError:
    return PlatformError("RUNTIME_DESCRIPTOR_INVALID", message)


def _not_certified(message: str) -> PlatformError:
    return PlatformError("EXECUTION_NOT_CERTIFIED", message)


@dataclass(frozen=True)
class RuntimeDescriptor:
    executor: str
    device_type: str
    device_index: int | None
    precision: str
    environment_ref: str | None = None  # PRIVATE (internal runtime/profile reference)
    environment_label: str | None = None  # PUBLIC (safe logical label, never a path)

    def public_device(self) -> str | None:
        """'cpu' for cpu; 'cuda:{index}' for cuda; None if unknown."""
        if self.device_type == "cpu":
            return "cpu"
        if self.device_type == "cuda":
            if self.device_index is None:
                return None
            return f"cuda:{self.device_index}"
        return None

    def public_projection(self) -> dict:
        """Public Analysis-Package projection; never includes ``environment_ref``."""
        return {
            "executor": self.executor,
            "device": self.public_device(),
            "environment": self.environment_label,
        }

    def to_metadata(self) -> dict:
        """Full internal metadata (includes the private ``environment_ref``)."""
        return {
            "executor": self.executor,
            "device_type": self.device_type,
            "device_index": self.device_index,
            "precision": self.precision,
            "environment_ref": self.environment_ref,
            "environment_label": self.environment_label,
        }

    @classmethod
    def from_metadata(cls, payload: Mapping[str, Any] | None) -> "RuntimeDescriptor | None":
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise _descriptor_invalid("Runtime descriptor metadata must be an object.")
        required = ("executor", "device_type", "device_index", "precision")
        missing = [field for field in required if field not in payload]
        if missing:
            raise _descriptor_invalid(
                f"Runtime descriptor metadata is missing fields {missing!r}."
            )
        executor = payload["executor"]
        device_type = payload["device_type"]
        precision = payload["precision"]
        for value in (executor, device_type, precision):
            if not isinstance(value, str) or not value:
                raise _descriptor_invalid(
                    "Runtime descriptor string fields must be non-empty strings."
                )
        device_index = payload["device_index"]
        if device_index is not None and (
            isinstance(device_index, bool) or not isinstance(device_index, int)
        ):
            raise _descriptor_invalid("device_index must be an int or null.")
        environment_ref = payload.get("environment_ref")
        environment_label = payload.get("environment_label")
        for value in (environment_ref, environment_label):
            if value is not None and (not isinstance(value, str) or not value):
                raise _descriptor_invalid(
                    "Runtime descriptor environment fields must be non-empty strings or null."
                )
        return cls(
            executor=executor,
            device_type=device_type,
            device_index=device_index,
            precision=precision,
            environment_ref=environment_ref,
            environment_label=environment_label,
        )


@dataclass(frozen=True)
class ExecutionCertificate:
    plugin_id: str
    plugin_version: str
    model_release_id: str | None  # None only for release-less/code-only certification
    executor: str
    device_type: str
    precision: str
    runtime_ref: str
    evidence_ref: str

    def key(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.plugin_id,
            self.plugin_version,
            self.model_release_id,
            self.executor,
            self.device_type,
            self.precision,
            self.runtime_ref,
        )


class ExecutionCertificateStore:
    """Exact-field certification lookup. Any single-field difference is uncertified."""

    def __init__(self, certificates: Iterable[ExecutionCertificate]) -> None:
        self._by_key: dict[tuple[str, str, str, str, str, str, str], ExecutionCertificate] = {}
        for certificate in certificates:
            key = certificate.key()
            if key in self._by_key:
                raise _not_certified(f"Duplicate execution certificate for {key!r}.")
            self._by_key[key] = certificate

    def is_certified(
        self,
        *,
        plugin_id: str,
        plugin_version: str,
        model_release_id: str | None,
        executor: str,
        device_type: str,
        precision: str,
        runtime_ref: str,
    ) -> bool:
        key = (
            plugin_id,
            plugin_version,
            model_release_id,
            executor,
            device_type,
            precision,
            runtime_ref,
        )
        return key in self._by_key

    def certified_capabilities(
        self,
        *,
        plugin_id: str,
        plugin_version: str,
        model_release_id: str | None,
        runtime_ref: str,
        technical: Iterable[ExecutionCapability],
    ) -> list[ExecutionCapability]:
        return [
            capability
            for capability in technical
            if self.is_certified(
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                model_release_id=model_release_id,
                executor=capability.executor,
                device_type=capability.device_type,
                precision=capability.precision,
                runtime_ref=runtime_ref,
            )
        ]


class ExecutorProvider(Protocol):
    """A concrete execution environment for one executor name."""

    name: str

    @property
    def runtime_ref(self) -> str: ...

    def runtime_descriptor(self) -> RuntimeDescriptor: ...

    def availability(
        self, definition: PipelineDefinition, model_release: Any, recording: Any
    ) -> ExecutorAvailabilityRead: ...

    def launch(self, run_id: str, *, coordinator_token: str | None) -> int | None: ...


class ExecutorRegistry:
    """Capability-driven dispatch: provider lookup + certificate-gated certification."""

    def __init__(
        self,
        providers: Mapping[str, ExecutorProvider],
        certificates: ExecutionCertificateStore,
    ) -> None:
        self._providers = dict(providers)
        self._certificates = certificates

    def provider(self, executor: str) -> ExecutorProvider:
        provider = self._providers.get(executor)
        if provider is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE",
                f"No executor provider is registered for '{executor}'.",
            )
        return provider

    def providers(self) -> Mapping[str, ExecutorProvider]:
        return dict(self._providers)

    def technical_capabilities(
        self, definition: PipelineDefinition
    ) -> list[ExecutionCapability]:
        return list(definition.technical_execution_capabilities)

    def certified_capabilities(
        self,
        definition: PipelineDefinition,
        model_release_id: str | None,
        runtime_ref: str,
    ) -> list[ExecutionCapability]:
        return self._certificates.certified_capabilities(
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            model_release_id=model_release_id,
            runtime_ref=runtime_ref,
            technical=definition.technical_execution_capabilities,
        )

    def certified_executors(
        self, definition: PipelineDefinition, model_release_id: str | None
    ) -> dict[str, list[ExecutionCapability]]:
        certified: dict[str, list[ExecutionCapability]] = {}
        for name, provider in self._providers.items():
            capabilities = self.certified_capabilities(
                definition, model_release_id, provider.runtime_ref
            )
            if capabilities:
                certified[name] = capabilities
        return certified

    def certified_capability(
        self, definition: PipelineDefinition, model_release_id: str | None, executor: str
    ) -> ExecutionCapability | None:
        """The exact certified capability for ``executor`` (or None). No substitution.

        Binds to the provider's ACTUAL ``runtime_descriptor()``
        (executor/device_type/precision), not merely the plugin's declaration: the
        plugin must declare a technical capability matching the actual descriptor,
        and an exact certificate must exist for that same tuple + runtime_ref +
        plugin/version/release.
        """
        provider = self._providers.get(executor)
        if provider is None:
            return None
        descriptor = provider.runtime_descriptor()
        if descriptor is None or descriptor.executor != executor:
            return None
        technical = [
            capability
            for capability in definition.technical_execution_capabilities
            if capability.executor == descriptor.executor
            and capability.device_type == descriptor.device_type
            and capability.precision == descriptor.precision
        ]
        if not technical:
            return None
        certified = self._certificates.certified_capabilities(
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            model_release_id=model_release_id,
            runtime_ref=provider.runtime_ref,
            technical=technical,
        )
        return certified[0] if certified else None

    def deployment_qualified_executors(
        self, definition: PipelineDefinition, model_release_id: str | None
    ) -> tuple[list[str], str | None]:
        """Deployment-qualified projection: technical ∩ registered provider ∩ exact cert.

        Configuration/certification only; never performs SSH/probe I/O.
        """
        supported = sorted(
            name
            for name in self._providers
            if self.certified_capability(definition, model_release_id, name) is not None
        )
        recommended = definition.recommended_execution
        return supported, (recommended if recommended in supported else None)

    def availability_for(
        self,
        definition: PipelineDefinition,
        model_release: Any,
        recording: Any,
        executor: str,
    ) -> ExecutorAvailabilityRead:
        """Exact requested-executor availability; never auto-substitutes another executor."""
        provider = self._providers.get(executor)
        if provider is None:
            return ExecutorAvailabilityRead(
                executor=executor,
                available=False,
                reason_code="EXECUTION_CAPABILITY_UNAVAILABLE",
                reason_message=f"No executor provider is registered for '{executor}'.",
                remote_profile=None,
                recommended=False,
            )
        model_release_id = (
            None if model_release is None else model_release.release.model_release_id
        )
        if self.certified_capability(definition, model_release_id, executor) is None:
            return ExecutorAvailabilityRead(
                executor=executor,
                available=False,
                reason_code="EXECUTION_NOT_CERTIFIED",
                reason_message=(
                    "The requested executor has no exact platform certificate for this "
                    "release and runtime."
                ),
                remote_profile=None,
                recommended=False,
            )
        return provider.availability(definition, model_release, recording)

    def availability(
        self, definition: PipelineDefinition, model_release: Any, recording: Any
    ) -> ExecutorAvailabilityRead:
        # Release-less/code-only plugins resolve to model_release_id=None and do not
        # create a fake ModelRelease; certification still uses None exactly.
        model_release_id = (
            None if model_release is None else model_release.release.model_release_id
        )
        certified = self.certified_executors(definition, model_release_id)
        if not certified:
            return ExecutorAvailabilityRead(
                executor=definition.recommended_executor,
                available=False,
                reason_code="EXECUTION_NOT_CERTIFIED",
                reason_message="No runtime has a platform certificate for this release.",
                remote_profile=None,
                recommended=False,
            )
        executor = (
            definition.recommended_executor
            if definition.recommended_executor in certified
            else sorted(certified)[0]
        )
        return self._providers[executor].availability(definition, model_release, recording)


class RemoteGpuExecutorProvider:
    """Adapts the M9.1 SSH probe + coordinator launcher as an ExecutorProvider."""

    name = "remote_gpu"

    def __init__(self, *, profile: Any, probe: Any, launcher: Any, required_runtime_commit: str) -> None:
        self._profile = profile
        self._probe = probe
        self._launcher = launcher
        self._required_runtime_commit = required_runtime_commit

    @property
    def runtime_ref(self) -> str:
        return f"remote:{self._profile.name}:{self._required_runtime_commit}"

    def runtime_descriptor(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            executor=self.name,
            device_type="cuda",
            device_index=0,
            precision="float16",
            environment_ref=self._profile.name,
            environment_label=self._profile.name,
        )

    def availability(self, definition: PipelineDefinition, model_release: Any, recording: Any) -> ExecutorAvailabilityRead:
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
        return self._probe.availability(recording, definition, recording.source_data_sha256)

    def launch(self, run_id: str, *, coordinator_token: str | None) -> int | None:
        return self._launcher.launch(run_id, coordinator_token)


_CERTIFICATE_FIELDS = (
    "plugin_id",
    "plugin_version",
    "model_release_id",
    "executor",
    "device_type",
    "precision",
    "runtime_ref",
    "evidence_ref",
)


def load_execution_certificates(path: Path) -> list[ExecutionCertificate]:
    """Load ``{"certificates": [...]}`` fail-closed; malformed data is not certifiable."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise _not_certified("Execution certificate file could not be read.") from exc
    except (ValueError, TypeError) as exc:
        raise _not_certified("Execution certificate JSON is invalid.") from exc
    if not isinstance(payload, dict):
        raise _not_certified("Execution certificate file must be a JSON object.")
    raw = payload.get("certificates")
    if not isinstance(raw, list):
        raise _not_certified("Execution certificate file must contain a 'certificates' array.")

    certificates: list[ExecutionCertificate] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise _not_certified(f"Certificate {index} must be an object.")
        missing = [field for field in _CERTIFICATE_FIELDS if field not in item]
        if missing:
            raise _not_certified(f"Certificate {index} is missing fields {missing!r}.")
        values = {field: item[field] for field in _CERTIFICATE_FIELDS}
        for field, value in values.items():
            if field == "model_release_id":
                # None = release-less/code-only certification; empty string is invalid.
                if value is not None and (not isinstance(value, str) or not value):
                    raise _not_certified(
                        f"Certificate {index} field 'model_release_id' must be a "
                        "non-empty string or null."
                    )
                continue
            if not isinstance(value, str) or not value:
                raise _not_certified(
                    f"Certificate {index} field '{field}' must be a non-empty string."
                )
        certificates.append(ExecutionCertificate(**values))

    ExecutionCertificateStore(certificates)
    return certificates
