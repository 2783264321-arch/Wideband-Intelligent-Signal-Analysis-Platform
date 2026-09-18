from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from app.analysis.schema import AnalysisRunRead
from app.imported_runs.batch_schema import BatchImportSummary
from app.imported_runs.batch_service import BatchPackageImportService
from app.imported_runs.bundle_schema import AnalysisBundleImportSummary
from app.imported_runs.bundle_service import (
    AnalysisBundleExportService,
    AnalysisBundleImportService,
)
from app.imported_runs.service import PackageImportService
from app.labels.service import LabelSpaceService

router = APIRouter(tags=["analysis"])


@router.post("/api/imported-runs", response_model=AnalysisRunRead, status_code=201)
def import_analysis_package(request: Request, recording_id: str = Form(...), file: UploadFile = File(...)):
    with request.app.state.database.session_factory() as session:
        return PackageImportService(
            session, request.app.state.storage,
            LabelSpaceService(request.app.state.settings.label_space_root),
        ).import_run(file.file, recording_id)


@router.post("/api/imported-runs/batch", response_model=BatchImportSummary, status_code=201)
def import_analysis_batch(request: Request, file: UploadFile = File(...)):
    with request.app.state.database.session_factory() as session:
        return BatchPackageImportService(
            session,
            request.app.state.storage,
            LabelSpaceService(request.app.state.settings.label_space_root),
        ).import_batch(file.file)


@router.get("/api/dataset-experiments/{experiment_id}/export")
def export_dataset_analysis_bundle(experiment_id: str, request: Request):
    """Export the results of a completed Dataset Analysis as an Analysis Bundle ZIP."""
    with request.app.state.database.session_factory() as session:
        filename, payload = AnalysisBundleExportService(session).export_experiment(experiment_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/analysis-runs/{run_id}/export")
def export_analysis_run_bundle(run_id: str, request: Request):
    """Export ONE completed single-sample analysis run as an Analysis Bundle ZIP."""
    with request.app.state.database.session_factory() as session:
        filename, payload = AnalysisBundleExportService(session).export_run(run_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/api/analysis-bundles/import",
    response_model=AnalysisBundleImportSummary,
    status_code=201,
)
def import_analysis_bundle(request: Request, file: UploadFile = File(...)):
    """Import a portable Analysis Bundle without rerunning inference."""
    with request.app.state.database.session_factory() as session:
        return AnalysisBundleImportService(session, request.app.state.storage).import_bundle(file.file)
