from fastapi import APIRouter, File, Form, Request, UploadFile

from app.analysis.schema import AnalysisRunRead
from app.imported_runs.batch_schema import BatchImportSummary
from app.imported_runs.batch_service import BatchPackageImportService
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
