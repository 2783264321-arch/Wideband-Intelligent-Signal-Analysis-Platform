"""A3 Task 4: qualification runner framework + real production CPU target probe.

GENERIC: branches only on ``executor``, never on ``plugin_id``. The production
``LocalCpuTargetProbe`` reuses the existing platform seams (provider ``probe()``,
``resolve_local_assets``, ``verify_assets``, the identity material probe) rather
than copying their logic. No Recording inference is required for A3.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Protocol

from app.analysis.local_inference_worker import resolve_local_assets
from app.core.config import Settings
from app.core.errors import PlatformError
from app.pipelines.base import ExecutionCapability, PipelineDefinition
from app.remote_execution.assets import verify_assets
from app.runtime_qualification.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    QualificationEvidence,
    QualificationResult,
    compute_evidence_sha256,
    validated_passed,
)
from app.runtime_qualification.identity import (
    LOCAL_CPU_V1,
    UNAVAILABLE,
    collect_identity_material,
    parse_local_runtime_ref,
    validate_runtime_ref_against_material,
)

LOCAL_CPU_SMOKE_V1 = "local_cpu_smoke_v1"
GPU_DEFERRED = "gpu_deferred"

INSTALL_ELIGIBLE_TYPES = {
    "local_cpu": (LOCAL_CPU_SMOKE_V1,),
    "local_gpu": (),  # NONE in A3 (Plan B authorizes real GPU types)
    "remote_gpu": (),  # NONE in A3 (Plan C authorizes real remote types)
}


def _probe_error(message: str) -> PlatformError:
    return PlatformError("QUALIFICATION_PROBE_FAILED", message)


def qualification_type_install_eligible(*, executor: str, qualification_type: str) -> bool:
    return qualification_type in INSTALL_ELIGIBLE_TYPES.get(executor, ())


@dataclass(frozen=True)
class QualificationTarget:
    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    executor: str
    runtime_ref: str
    runtime_descriptor: dict


class LocalCpuTargetProbe:
    """Platform-owned PRODUCTION probe. Generic; never branches on plugin_id."""

    def __init__(
        self,
        *,
        settings: Settings,
        definition: PipelineDefinition,
        provider: object | None,
        model_release_store: object | None,
        material_probe: Callable[[], dict] | None = None,
        repo_default_runtime_refs: frozenset[str] = frozenset(),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._definition = definition
        self._provider = provider
        self._model_release_store = model_release_store
        self._material_probe = material_probe
        self._repo_default_runtime_refs = frozenset(repo_default_runtime_refs)
        self._now = now

    def _material(self) -> dict:
        if self._material_probe is not None:
            return self._material_probe()
        return collect_identity_material(self._settings.local_cpu_python_path)

    def __call__(self, target: QualificationTarget) -> None:
        if target.executor != "local_cpu":
            raise _probe_error("Qualification target executor is not local_cpu.")
        provider = self._provider
        if provider is None:
            raise _probe_error("Current local_cpu provider is not registered.")
        if getattr(provider, "name", None) != "local_cpu":
            raise _probe_error("Registered provider for local_cpu is not the expected provider.")
        ok, reason = provider.probe()
        if not ok:
            raise _probe_error(f"Local CPU provider probe failed: {reason or 'unknown'}")
        if provider.runtime_ref != target.runtime_ref:
            raise _probe_error("Current provider runtime_ref does not match the qualification target.")
        descriptor = provider.runtime_descriptor()
        if descriptor is None or descriptor.executor != "local_cpu":
            raise _probe_error("Provider runtime descriptor executor is not local_cpu.")
        if descriptor.to_metadata() != target.runtime_descriptor:
            raise _probe_error("Provider runtime descriptor does not match the qualification target.")

        capability = ExecutionCapability(
            descriptor.executor, descriptor.device_type, descriptor.precision
        )
        declared = {c.key() for c in self._definition.technical_execution_capabilities}
        if capability.key() not in declared:
            raise _probe_error("Definition declares no exact technical execution capability.")

        if self._settings.runtime_family is None:
            raise _probe_error("runtime_family is not configured for a new local_cpu qualification.")
        parsed = parse_local_runtime_ref(target.runtime_ref)
        if parsed is None:
            raise _probe_error("runtime_ref is not a well-formed local runtime reference.")
        family, kind = parsed
        if kind != "cpu":
            raise _probe_error("local_cpu qualification requires a cpu runtime kind.")
        if family != self._settings.runtime_family:
            raise _probe_error("runtime_ref family does not match the configured runtime_family.")
        material = self._material()
        validate_runtime_ref_against_material(
            runtime_ref=target.runtime_ref,
            family=self._settings.runtime_family,
            kind="cpu",
            scheme=LOCAL_CPU_V1,
            material=material,
        )

        if (
            self._definition.plugin_id != target.plugin_id
            or self._definition.plugin_version != target.plugin_version
        ):
            raise _probe_error("Qualification target plugin/version is not the current definition.")

        if self._definition.model_release_required:
            if target.model_release_id is None:
                raise _probe_error("Release-bound plugin requires an exact model release identity.")
            resolved = self._model_release_store.resolve(
                target.plugin_id, target.plugin_version, target.model_release_id
            )
            if resolved.release.model_release_id != target.model_release_id:
                raise _probe_error("Resolved model release does not match the qualification target.")
            assets = resolve_local_assets(
                deployment_config=self._settings.local_asset_paths,
                plugin_id=target.plugin_id,
                plugin_version=target.plugin_version,
                asset_manifest_sha256=resolved.manifest.asset_manifest_sha256,
            )
            verify_assets(resolved.manifest, dict(assets))
        elif target.model_release_id is not None:
            raise _probe_error("Release-less plugin must not carry a model release identity.")
        return None


def _provider_from_registry(executor_registry: object, executor: str) -> object | None:
    providers = None
    getter = getattr(executor_registry, "providers", None)
    if callable(getter):
        try:
            providers = getter()
        except Exception:
            providers = None
    if providers is None:
        providers = getattr(executor_registry, "_providers", None)
    if isinstance(providers, dict):
        return providers.get(executor)
    return None


def build_default_target_probe(
    *,
    target: QualificationTarget,
    settings: Settings,
    pipeline_registry: object,
    model_release_store: object | None,
    executor_registry: object,
    now: Callable[[], datetime] | None = None,
) -> LocalCpuTargetProbe:
    handle = pipeline_registry.get(target.plugin_id)
    definition = handle.definition
    provider = _provider_from_registry(executor_registry, target.executor)
    return LocalCpuTargetProbe(
        settings=settings,
        definition=definition,
        provider=provider,
        model_release_store=model_release_store,
        now=now,
    )


class QualificationRunner(Protocol):
    qualification_type: str

    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]: ...


class LocalCpuQualificationRunner:
    qualification_type = LOCAL_CPU_SMOKE_V1
    identity_scheme = LOCAL_CPU_V1

    def __init__(self, target_probe: Callable[[QualificationTarget], None] | None = None) -> None:
        self._target_probe = target_probe

    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]:
        if self._target_probe is None:
            return (
                QualificationResult("probe", False, "QUALIFICATION_PROBE_UNAVAILABLE"),
            )
        try:
            self._target_probe(target)
        except PlatformError as exc:
            return (QualificationResult("probe", False, exc.message),)
        except Exception as exc:  # never propagate as an exception
            return (QualificationResult("probe", False, type(exc).__name__),)
        return (QualificationResult("probe", True),)


class DeferredGpuQualificationRunner:
    qualification_type = GPU_DEFERRED
    identity_scheme = UNAVAILABLE

    def run(self, *, target: QualificationTarget) -> tuple[QualificationResult, ...]:
        return (QualificationResult("gpu", False, "GPU_QUALIFICATION_DEFERRED"),)


def select_runner(
    *, executor: str, target_probe: Callable[[QualificationTarget], None] | None = None
) -> QualificationRunner:
    if executor == "local_cpu":
        return LocalCpuQualificationRunner(target_probe=target_probe)
    return DeferredGpuQualificationRunner()


def run_qualification(
    *,
    target: QualificationTarget,
    runner: QualificationRunner,
    now: Callable[[], datetime] | None = None,
    input_identity: dict | None = None,
    asset_manifest_sha256: str | None = None,
) -> QualificationEvidence:
    results = tuple(runner.run(target=target))
    passed = validated_passed(results)
    created = (now or (lambda: datetime.now(timezone.utc)))().isoformat()
    descriptor = dict(target.runtime_descriptor)
    evidence = QualificationEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        created_at=created,
        plugin_id=target.plugin_id,
        plugin_version=target.plugin_version,
        model_release_id=target.model_release_id,
        executor=target.executor,
        device_type=str(descriptor.get("device_type", "")),
        precision=str(descriptor.get("precision", "")),
        runtime_ref=target.runtime_ref,
        runtime_descriptor=descriptor,
        identity_scheme=getattr(runner, "identity_scheme", UNAVAILABLE),
        qualification_type=runner.qualification_type,
        results=results,
        passed=passed,
        input_identity=input_identity,
        asset_manifest_sha256=asset_manifest_sha256,
        evidence_sha256="",
    )
    return replace(evidence, evidence_sha256=compute_evidence_sha256(evidence))
