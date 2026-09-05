from dataclasses import asdict

from fastapi import APIRouter, Query, Request

from app.analysis.schema import AnalysisRunCreate, AnalysisRunRead, PipelineDefinitionRead
from app.analysis.service import AnalysisService

router = APIRouter(tags=["analysis"])


def _service(request: Request, session) -> AnalysisService:
    return AnalysisService(session, request.app.state.pipeline_registry, request.app.state.job_manager)


@router.get("/api/pipelines", response_model=list[PipelineDefinitionRead])
def list_pipelines(request: Request):
    return [PipelineDefinitionRead(**{**asdict(item), "stages": list(item.stages), "inspectable_stages": list(item.inspectable_stages)}) for item in request.app.state.pipeline_registry.list()]


@router.post("/api/analysis-runs", response_model=AnalysisRunRead, status_code=201)
def create_analysis_run(payload: AnalysisRunCreate, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).create_run(
            recording_id=payload.recording_id,
            pipeline_id=payload.pipeline_id,
            executor=payload.executor,
            parameters=payload.parameters,
        )


@router.get("/api/analysis-runs", response_model=list[AnalysisRunRead])
def list_analysis_runs(
    request: Request,
    recording_id: str | None = Query(None),
    status: str | None = Query(None, pattern=r"^(pending|running|completed|failed|interrupted)$"),
):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list(recording_id=recording_id, status=status)


@router.get("/api/analysis-runs/{run_id}", response_model=AnalysisRunRead)
def get_analysis_run(run_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).get(run_id)
