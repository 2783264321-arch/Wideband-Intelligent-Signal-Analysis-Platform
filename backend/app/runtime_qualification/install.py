"""A3 Task 5: live-authority validated evidence -> certificate + operator store.

Installation re-resolves the CURRENT deployment authority (definition, release,
provider, runtime_ref, descriptor, technical capability) and never trusts the
identity claims embedded in the evidence JSON. First installation does not and
must not depend on an existing certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    load_execution_certificates,
)
from app.runtime_qualification.evidence import QualificationEvidence, validated_passed
from app.runtime_qualification.qualification import qualification_type_install_eligible


def _invalid(message: str) -> PlatformError:
    return PlatformError("QUALIFICATION_EVIDENCE_INVALID", message)


def _not_certified(message: str) -> PlatformError:
    return PlatformError("EXECUTION_NOT_CERTIFIED", message)


@dataclass(frozen=True)
class InstallResult:
    status: str  # "created" | "already_installed" | "already_certified"
    certificate: ExecutionCertificate


@dataclass(frozen=True)
class LiveAuthority:
    definition: object
    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    asset_manifest_sha256: str | None
    executor: str
    runtime_ref: str
    runtime_descriptor: dict
    technical_capability_present: bool
    technical_capability: object | None
    provider_present: bool
    # Deliberately NO `certified_capability`: first install must not depend on an
    # existing certificate.


def operator_certificate_path(data_root: Path) -> Path:
    return Path(data_root) / "runtime_certificates.json"


def load_operator_certificates(data_root: Path) -> list[ExecutionCertificate]:
    path = operator_certificate_path(data_root)
    if not path.exists():
        return []
    return load_execution_certificates(path)


def build_certificate_store(*, repo_path: Path, data_root: Path) -> ExecutionCertificateStore:
    certificates = list(load_execution_certificates(repo_path))
    certificates.extend(load_operator_certificates(data_root))
    # Existing store enforces exact 7-field uniqueness and fails closed on any
    # duplicate (repo<->operator or within operator); no silent dedupe.
    return ExecutionCertificateStore(certificates)


def _providers_of(executor_registry: object) -> dict:
    getter = getattr(executor_registry, "providers", None)
    if callable(getter):
        return dict(getter())
    return dict(getattr(executor_registry, "_providers", {}) or {})


def resolve_live_authority(
    *,
    registry: object,
    model_release_store: object,
    executor_registry: object,
    plugin_id: str,
    plugin_version: str,
    executor: str,
    requested_model_release_id: str | None,
    data_root: Path,
) -> LiveAuthority:
    handle = registry.get(plugin_id)
    definition = handle.definition
    if definition.plugin_version != plugin_version:
        raise _not_certified("Requested plugin version is not the current definition.")

    if getattr(definition, "model_release_required", False):
        resolved = model_release_store.resolve(plugin_id, plugin_version, requested_model_release_id)
        model_release_id: str | None = resolved.release.model_release_id
        asset_manifest_sha256: str | None = resolved.manifest.asset_manifest_sha256
    else:
        if requested_model_release_id is not None:
            raise PlatformError(
                "MODEL_RELEASE_MISMATCH",
                "Release-less plugin must not carry a model release identity.",
            )
        model_release_id = None
        asset_manifest_sha256 = None

    provider = _providers_of(executor_registry).get(executor)
    provider_present = provider is not None
    runtime_ref = ""
    runtime_descriptor: dict = {}
    technical_capability: ExecutionCapability | None = None
    if provider is not None:
        descriptor = provider.runtime_descriptor()
        if (
            getattr(provider, "name", None) != executor
            or descriptor is None
            or descriptor.executor != executor
        ):
            raise _not_certified(
                "Provider executor identity does not match the requested executor."
            )
        runtime_ref = provider.runtime_ref
        runtime_descriptor = descriptor.to_metadata()
        wanted = ExecutionCapability(executor, descriptor.device_type, descriptor.precision)
        for capability in definition.technical_execution_capabilities:
            if capability.key() == wanted.key():
                technical_capability = capability
                break

    return LiveAuthority(
        definition=definition,
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        model_release_id=model_release_id,
        asset_manifest_sha256=asset_manifest_sha256,
        executor=executor,
        runtime_ref=runtime_ref,
        runtime_descriptor=runtime_descriptor,
        technical_capability_present=technical_capability is not None,
        technical_capability=technical_capability,
        provider_present=provider_present,
    )


def _check_staleness(evidence: QualificationEvidence, now: datetime | None, max_age_s: int | None) -> None:
    if max_age_s is None:
        return
    try:
        created = datetime.fromisoformat(evidence.created_at)
    except (ValueError, TypeError) as exc:
        raise _invalid("Evidence created_at is not a valid timestamp.") from exc
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if (reference - created).total_seconds() > max_age_s:
        raise _invalid("Qualification evidence is stale.")


def validate_evidence_for_install(
    *,
    evidence: QualificationEvidence,
    authority: LiveAuthority,
    now: datetime | None = None,
    max_age_s: int | None = None,
) -> ExecutionCertificate:
    if not validated_passed(evidence.results) or evidence.passed is not True:
        raise _invalid("Evidence did not pass qualification.")
    if not qualification_type_install_eligible(
        executor=evidence.executor, qualification_type=evidence.qualification_type
    ):
        raise _invalid("Qualification type is not install-eligible for this executor.")
    if evidence.plugin_id != authority.plugin_id or evidence.plugin_version != authority.plugin_version:
        raise _invalid("Evidence plugin/version does not match the current definition.")
    if evidence.model_release_id != authority.model_release_id:
        raise _invalid("Evidence model release does not match the current deployment.")
    if evidence.asset_manifest_sha256 != authority.asset_manifest_sha256:
        raise _invalid("Evidence asset manifest does not match the current deployment.")
    if evidence.executor != authority.executor:
        raise _invalid("Evidence executor does not match the current provider.")
    if evidence.runtime_ref != authority.runtime_ref:
        raise _invalid("Evidence runtime_ref does not match the current provider.")
    if evidence.runtime_descriptor != authority.runtime_descriptor:
        raise _invalid("Evidence runtime descriptor does not match the current provider.")
    if not authority.provider_present:
        raise _not_certified("Current provider is not registered.")
    if not authority.technical_capability_present:
        raise _not_certified("Definition declares no exact technical capability.")
    _check_staleness(evidence, now, max_age_s)

    descriptor = evidence.runtime_descriptor
    return ExecutionCertificate(
        plugin_id=evidence.plugin_id,
        plugin_version=evidence.plugin_version,
        model_release_id=evidence.model_release_id,
        executor=evidence.executor,
        device_type=str(descriptor.get("device_type", "")),
        precision=str(descriptor.get("precision", "")),
        runtime_ref=evidence.runtime_ref,
        evidence_ref=evidence.evidence_sha256,
    )


def _same_certificate(a: ExecutionCertificate, b: ExecutionCertificate) -> bool:
    return a.key() == b.key() and a.evidence_ref == b.evidence_ref


def _write_operator_certificates(data_root: Path, certificates: list[ExecutionCertificate]) -> None:
    path = operator_certificate_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"certificates": [certificate.__dict__ for certificate in certificates]}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".certs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def install_certificate(
    *,
    data_root: Path,
    repo_certificate_path: Path,
    evidence: QualificationEvidence,
    authority: LiveAuthority,
) -> InstallResult:
    certificate = validate_evidence_for_install(evidence=evidence, authority=authority)

    repo_certificates = load_execution_certificates(repo_certificate_path)
    for existing in repo_certificates:
        if existing.key() == certificate.key():
            return InstallResult("already_certified", existing)

    operator_certificates = load_operator_certificates(data_root)
    for existing in operator_certificates:
        if existing.key() == certificate.key():
            if _same_certificate(existing, certificate):
                return InstallResult("already_installed", existing)
            raise _invalid("Conflicting operator certificate for the same exact key.")

    _write_operator_certificates(data_root, operator_certificates + [certificate])
    return InstallResult("created", certificate)
