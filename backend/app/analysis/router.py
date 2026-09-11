from dataclasses import asdict

from fastapi import APIRouter, Query, Request

from app.analysis.schema import (
    AnalysisRunCreate,
    AnalysisRunRead,
    ExecutorAvailabilityRead,
    PipelineDefinitionRead,
)
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition

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
        runtime_commit_config=getattr(state, "runtime_commit_config", None),
        project_root=getattr(state, "project_root", None),
        data_root=getattr(state, "data_root", None),
        model_release_store=getattr(state, "model_release_store", None),
        executor_registry=getattr(state, "executor_registry", None),
    )


def _default_release_id(definition: PipelineDefinition, model_release_store) -> str | None:
    if not definition.model_release_required or model_release_store is None:
        return None
    try:
        return model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, None
        ).release.model_release_id
    except PlatformError:
        return None


@router.get("/api/pipelines", response_model=list[PipelineDefinitionRead])
def list_pipelines(request: Request):
    state = request.app.state
    registry = getattr(state, "executor_registry", None)
    model_release_store = getattr(state, "model_release_store", None)
    return [
        _pipeline_read_model(definition, registry, model_release_store)
        for definition in state.pipeline_registry.list()
    ]


def _pipeline_read_model(
    definition: PipelineDefinition, executor_registry, model_release_store
) -> PipelineDefinitionRead:
    payload = asdict(definition)
    resolved_label_space = definition.resolved_output_label_space
    payload["label_space"] = resolved_label_space
    payload["output_label_space"] = resolved_label_space
    payload["stages"] = list(definition.stages)
    payload["inspectable_stages"] = list(definition.inspectable_stages)
    payload["technical_execution_capabilities"] = [
        asdict(capability) for capability in definition.technical_execution_capabilities
    ]
    # Deployment-qualified executor projection: declared technical capability ∩
    # registered provider ∩ exact certificate for the default resolved release.
    # Configuration/certification only; never a live probe.
    if executor_registry is not None:
        if definition.model_release_required:
            release_id = _default_release_id(definition, model_release_store)
            if release_id is None:
                # Release-required but the default release could not be resolved:
                # never conflate with a release-less None certificate; fail closed.
                supported, recommended = [], None
            else:
                supported, recommended = executor_registry.deployment_qualified_executors(
                    definition, release_id
                )
        else:
            supported, recommended = executor_registry.deployment_qualified_executors(
                definition, None
            )
    else:
        supported, recommended = [], None
    payload["executors_supported"] = supported
    payload["recommended_executor"] = recommended
    return PipelineDefinitionRead(**payload)


@router.post("/api/analysis-runs", response_model=AnalysisRunRead, status_code=201)
def create_analysis_run(payload: AnalysisRunCreate, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).create_run(
            recording_id=payload.recording_id,
            pipeline_id=payload.pipeline_id,
            executor=payload.executor,
            parameters=payload.parameters,
            model_release_id=payload.model_release_id,
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
    executor: str = Query("remote_gpu"),
):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).executor_availability(
            recording_id, pipeline_id, executor
        )
