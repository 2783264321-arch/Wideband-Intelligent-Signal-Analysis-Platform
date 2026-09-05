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
from app.analysis.service import mark_stale_running_runs_interrupted
from app.imported_runs.router import router as imported_runs_router
from app.pipelines.registry import create_pipeline_registry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Wideband Intelligent Signal Analysis Platform")
    app.state.settings = settings
    app.state.database = Database(settings.database_url)
    app.state.storage = StorageService(settings.data_root)
    app.state.pipeline_registry = create_pipeline_registry()
    app.state.job_manager = LocalJobManager(settings)

    load_domain_models()
    Base.metadata.create_all(app.state.database.engine)
    run_additive_migrations(app.state.database.engine)
    with app.state.database.session_factory() as recovery_session:
        mark_stale_running_runs_interrupted(recovery_session)

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

    spectrogram_cache = settings.data_root / "cache" / "spectrograms"
    spectrogram_cache.mkdir(parents=True, exist_ok=True)
    app.mount("/media/spectrograms", StaticFiles(directory=spectrogram_cache), name="spectrograms")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
