from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


@dataclass(frozen=True)
class ControlPlaneDependencies:
    registry: object
    model_release_store: object
    executor_registry: object
    identity_resolver: object
    orchestrator_commit_resolver: object
    runtime_commit_config: str | None
    project_root: Path
    data_root: Path


def _plugins_root() -> Path:
    return Path(__file__).resolve().parents[1] / "pipelines"


def build_control_plane_dependencies(settings: Settings) -> ControlPlaneDependencies:
    from app.analysis.local_executor import build_local_providers
    from app.pipelines.registry import create_pipeline_registry
    from app.remote_execution.identity import (
        resolve_local_orchestrator_commit,
        resolve_remote_recording_identity,
    )
    from app.remote_execution.model_release import (
        ModelReleaseStore,
        load_model_release_defaults,
    )
    from app.remote_execution.runtime import (
        ExecutionCertificateStore,
        ExecutorRegistry,
        load_execution_certificates,
    )

    plugins_root = _plugins_root()
    registry = create_pipeline_registry()
    model_release_store = ModelReleaseStore(
        plugins_root,
        load_model_release_defaults(plugins_root / "model_release_defaults.json"),
    )

    remote_provider = None
    runtime_commit_config = None
    identity_resolver = None
    try:
        from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
        from app.remote_execution.executor import SshRemoteExecutorProbe
        from app.remote_execution.profile import RemoteProfile
        from app.remote_execution.runtime import RemoteGpuExecutorProvider
        from app.remote_execution.transport import SshRunner

        profile = RemoteProfile.from_env(settings)
        transport = SshRunner(profile)
        probe = SshRemoteExecutorProbe(
            profile, transport,
            expected_runtime_commit=profile.required_remote_runtime_commit,
        )
        remote_provider = RemoteGpuExecutorProvider(
            profile=profile, probe=probe,
            launcher=CoordinatorJobManager(settings),
            required_runtime_commit=profile.required_remote_runtime_commit,
        )
        runtime_commit_config = profile.required_remote_runtime_commit
        identity_resolver = resolve_remote_recording_identity
    except Exception:
        remote_provider = None
        runtime_commit_config = None
        identity_resolver = None

    providers = dict(build_local_providers(settings))
    if remote_provider is not None:
        providers[remote_provider.name] = remote_provider
    certificates = ExecutionCertificateStore(
        load_execution_certificates(plugins_root / "execution_certificates.json")
    )
    executor_registry = ExecutorRegistry(providers, certificates)
    return ControlPlaneDependencies(
        registry=registry,
        model_release_store=model_release_store,
        executor_registry=executor_registry,
        identity_resolver=identity_resolver,
        orchestrator_commit_resolver=resolve_local_orchestrator_commit,
        runtime_commit_config=runtime_commit_config,
        project_root=settings.project_root,
        data_root=settings.data_root,
    )
