from dataclasses import asdict

from fastapi import APIRouter, Query, Request

from app.analysis.schema import (
    AnalysisRunCreate,
    AnalysisRunRead,
    ExecutorAvailabilityRead,
    PipelineDefinitionRead,
)
from app.analysis.service import AnalysisService

router = APIRouter(tags=["analysis"])


def _service(request: Request, session) -> AnalysisService:
    state = request.app.state
    return AnalysisService(
        session,
        state.pipeline_registry,
        state.job_manager,
        getattr(state, "remote_executor_probe", None),
        remote_coordinator_launcher=getattr(state, "remote_coordinator_launcher", None),
        identity_resolver=getattr(state, "identity_resolver", None),
        orchestrator_commit_resolver=getattr(state, "orchestrator_commit_resolver", None),
        asset_manifest_sha256_resolver=getattr(state, "asset_manifest_sha256_resolver", None),
        runtime_commit_config=getattr(state, "runtime_commit_config", None),
        project_root=getattr(state, "project_root", None),
        data_root=getattr(state, "data_root", None),
        asset_manifest_path=getattr(state, "asset_manifest_path", None),
    )


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


@router.get("/api/executor-availability", response_model=ExecutorAvailabilityRead)
def executor_availability(
    request: Request,
    recording_id: str = Query(...),
    pipeline_id: str = Query(...),
):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).executor_availability(recording_id, pipeline_id)
