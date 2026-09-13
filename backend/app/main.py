from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import Settings
from app.core.errors import PlatformError
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.storage.service import StorageService
from app.datasets.router import router as datasets_router
from app.evaluation.router import router as evaluation_router
from app.recordings.router import router as recordings_router
from app.dsp.router import router as dsp_router
from app.ground_truth.router import router as ground_truth_router
from app.detections.router import router as detections_router
from app.analysis.job_manager import LocalJobManager
from app.analysis.router import router as analysis_router
from app.benchmarks.job_manager import LocalBenchmarkJobManager
from app.benchmarks.router import router as benchmarks_router
from app.benchmarks.service import mark_stale_running_evaluations_interrupted
from app.dataset_experiments import recovery as dataset_experiment_recovery
from app.dataset_experiments.job_manager import DatasetExperimentJobManager
from app.imported_runs.router import router as imported_runs_router
from app.pipelines.registry import create_pipeline_registry
from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
from app.remote_execution.model_release import ModelReleaseStore, load_model_release_defaults
from app.analysis.local_executor import build_local_providers
from app.remote_execution.runtime import (
    ExecutionCertificateStore,
    ExecutorRegistry,
    RemoteGpuExecutorProvider,
    load_execution_certificates,
)


def _build_certificate_store() -> ExecutionCertificateStore:
    path = Path(__file__).resolve().parent / "pipelines" / "execution_certificates.json"
    return ExecutionCertificateStore(load_execution_certificates(path))


def _build_executor_registry(app, settings) -> ExecutorRegistry:
    providers = dict(build_local_providers(settings))
    remote = getattr(app.state, "remote_executor_provider", None)
    if remote is not None:
        providers[remote.name] = remote
    return ExecutorRegistry(providers, _build_certificate_store())


def _plugins_root() -> Path:
    return Path(__file__).resolve().parent / "pipelines"


def _build_model_release_store() -> ModelReleaseStore:
    plugins_root = _plugins_root()
    return ModelReleaseStore(
        plugins_root,
        load_model_release_defaults(plugins_root / "model_release_defaults.json"),
    )


def _wire_remote_lifecycle(app, settings) -> None:
    """Wire the local control-plane remote lifecycle when a valid RemoteProfile exists.

    Missing or invalid remote config keeps the app healthy: no coordinator recovery
    launch, remote availability=false, and existing remote runs are preserved.
    Per-plugin/per-release manifest identity is resolved at availability time, so
    any number of release-required plugins may coexist.
    """
    from app.remote_execution.executor import SshRemoteExecutorProbe
    from app.remote_execution.identity import (
        resolve_local_orchestrator_commit,
        resolve_remote_recording_identity,
    )
    from app.remote_execution.profile import RemoteProfile
    from app.remote_execution.transport import SshRunner

    try:
        profile = RemoteProfile.from_env(settings)
    except Exception:
        app.state.remote_config_available = False
        app.state.remote_executor_probe = None
        app.state.remote_coordinator_launcher = None
        app.state.remote_executor_provider = None
        app.state.identity_resolver = None
        app.state.orchestrator_commit_resolver = None
        app.state.runtime_commit_config = None
        app.state.project_root = settings.project_root
        app.state.data_root = settings.data_root
        return

    transport = SshRunner(profile)
    probe = SshRemoteExecutorProbe(
        profile,
        transport,
        expected_runtime_commit=profile.required_remote_runtime_commit,
    )
    app.state.remote_config_available = True
    app.state.remote_executor_probe = probe
    app.state.remote_coordinator_launcher = CoordinatorJobManager(settings)
    app.state.remote_executor_provider = RemoteGpuExecutorProvider(
        profile=profile,
        probe=probe,
        launcher=app.state.remote_coordinator_launcher,
        required_runtime_commit=profile.required_remote_runtime_commit,
    )
    app.state.identity_resolver = resolve_remote_recording_identity
    app.state.orchestrator_commit_resolver = resolve_local_orchestrator_commit
    app.state.runtime_commit_config = profile.required_remote_runtime_commit
    app.state.project_root = settings.project_root
    app.state.data_root = settings.data_root

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Wideband Intelligent Signal Analysis Platform")
    app.state.settings = settings
    app.state.database = Database(settings.database_url)
    app.state.storage = StorageService(settings.data_root)
    app.state.pipeline_registry = create_pipeline_registry()
    app.state.model_release_store = _build_model_release_store()
    app.state.job_manager = LocalJobManager(settings)
    app.state.benchmark_job_manager = LocalBenchmarkJobManager(settings)

    load_domain_models()
    Base.metadata.create_all(app.state.database.engine)
    run_additive_migrations(app.state.database.engine)
    _wire_remote_lifecycle(app, settings)
    app.state.executor_registry = _build_executor_registry(app, settings)
    startup_recovery_cutoff = datetime.now(timezone.utc)

    with app.state.database.session_factory() as recovery_session:
        from app.remote_execution.recovery import (
            coordinate_orphaned_remote_runs,
            mark_stale_local_cpu_runs_interrupted,
        )

        mark_stale_local_cpu_runs_interrupted(recovery_session)
        mark_stale_running_evaluations_interrupted(recovery_session)
        if app.state.remote_config_available and app.state.remote_coordinator_launcher is not None:
            coordinate_orphaned_remote_runs(
                recovery_session,
                launcher=app.state.remote_coordinator_launcher,
                remote_config_available=True,
                seen_run_ids=set(),
            )
        dataset_experiment_recovery.recover_dataset_experiments(
            recovery_session,
            job_manager=DatasetExperimentJobManager(settings),
            registry=app.state.pipeline_registry,
            model_release_store=app.state.model_release_store,
            executor_registry=app.state.executor_registry,
            startup_recovery_cutoff=startup_recovery_cutoff,
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(PlatformError)
    async def platform_error_handler(_: Request, exc: PlatformError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    app.include_router(recordings_router)
    app.include_router(datasets_router)
    app.include_router(dsp_router)
    app.include_router(ground_truth_router)
    app.include_router(detections_router)
    app.include_router(analysis_router)
    app.include_router(imported_runs_router)
    app.include_router(evaluation_router)
    app.include_router(benchmarks_router)

    spectrogram_cache = settings.data_root / "cache" / "spectrograms"
    spectrogram_cache.mkdir(parents=True, exist_ok=True)
    app.mount("/media/spectrograms", StaticFiles(directory=spectrogram_cache), name="spectrograms")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
