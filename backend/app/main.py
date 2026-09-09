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
from app.imported_runs.router import router as imported_runs_router
from app.pipelines.registry import create_pipeline_registry
from app.remote_execution.coordinator_job_manager import CoordinatorJobManager


def _asset_manifest_path(project_root) -> Path:
    return (
        Path(project_root)
        / "backend" / "app" / "pipelines" / "zoomspec_yolo26n_aug_combined_frn_v3"
        / "asset_manifest.json"
    )


def _wire_remote_lifecycle(app, settings) -> None:
    """Wire the local control-plane remote lifecycle when a valid RemoteProfile exists.

    Missing or invalid remote config keeps the app healthy: no coordinator recovery
    launch, remote availability=false, and existing remote runs are preserved.
    """
    from app.remote_execution.executor import SshRemoteExecutorProbe
    from app.remote_execution.identity import (
        resolve_asset_manifest_sha256,
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
        app.state.identity_resolver = None
        app.state.orchestrator_commit_resolver = None
        app.state.asset_manifest_sha256_resolver = None
        app.state.runtime_commit_config = None
        app.state.project_root = settings.project_root
        app.state.data_root = settings.data_root
        app.state.asset_manifest_path = None
        return

    asset_manifest_path = _asset_manifest_path(settings.project_root)
    try:
        asset_manifest_sha256 = resolve_asset_manifest_sha256(asset_manifest_path)
    except Exception:
        app.state.remote_config_available = False
        app.state.remote_executor_probe = None
        app.state.remote_coordinator_launcher = None
        app.state.identity_resolver = None
        app.state.orchestrator_commit_resolver = None
        app.state.asset_manifest_sha256_resolver = None
        app.state.runtime_commit_config = None
        app.state.project_root = settings.project_root
        app.state.data_root = settings.data_root
        app.state.asset_manifest_path = None
        return

    transport = SshRunner(profile)
    probe = SshRemoteExecutorProbe(
        profile,
        transport,
        expected_runtime_commit=profile.required_remote_runtime_commit,
        expected_manifest_sha256=asset_manifest_sha256,
    )
    app.state.remote_config_available = True
    app.state.remote_executor_probe = probe
    app.state.remote_coordinator_launcher = CoordinatorJobManager(settings)
    app.state.identity_resolver = resolve_remote_recording_identity
    app.state.orchestrator_commit_resolver = resolve_local_orchestrator_commit
    app.state.asset_manifest_sha256_resolver = resolve_asset_manifest_sha256
    app.state.runtime_commit_config = profile.required_remote_runtime_commit
    app.state.project_root = settings.project_root
    app.state.data_root = settings.data_root
    app.state.asset_manifest_path = asset_manifest_path

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Wideband Intelligent Signal Analysis Platform")
    app.state.settings = settings
    app.state.database = Database(settings.database_url)
    app.state.storage = StorageService(settings.data_root)
    app.state.pipeline_registry = create_pipeline_registry()
    app.state.job_manager = LocalJobManager(settings)
    app.state.benchmark_job_manager = LocalBenchmarkJobManager(settings)

    load_domain_models()
    Base.metadata.create_all(app.state.database.engine)
    run_additive_migrations(app.state.database.engine)
    _wire_remote_lifecycle(app, settings)
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
