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
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability


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
    model_release_id: str
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
        model_release_id: str,
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
        model_release_id: str,
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
            if not isinstance(value, str) or not value:
                raise _not_certified(
                    f"Certificate {index} field '{field}' must be a non-empty string."
                )
        certificates.append(ExecutionCertificate(**values))

    ExecutionCertificateStore(certificates)
    return certificates
