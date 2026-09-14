from fastapi import APIRouter, Request

from app.dataset_experiments.schema import (
    DatasetExperimentAttemptRead,
    DatasetExperimentCreate,
    DatasetExperimentItemRead,
    DatasetExperimentRead,
)
from app.dataset_experiments.service import DatasetExperimentService

router = APIRouter(prefix="/api/dataset-experiments", tags=["dataset-experiments"])


def _service(request: Request, session) -> DatasetExperimentService:
    return DatasetExperimentService(
        session,
        request.app.state.pipeline_registry,
        request.app.state.model_release_store,
        request.app.state.executor_registry,
    )


@router.post("", response_model=DatasetExperimentRead, status_code=201)
def create_experiment(payload: DatasetExperimentCreate, request: Request):
    with request.app.state.database.session_factory() as session:
        service = _service(request, session)
        experiment = service.create_experiment(
            name=payload.name,
            dataset_name=payload.dataset_name,
            dataset_split=payload.dataset_split,
            dataset_label_space=payload.dataset_label_space,
            plugin_id=payload.plugin_id,
            plugin_version=payload.plugin_version,
            executor=payload.executor,
            parameters=payload.parameters,
            evaluation_protocol=payload.evaluation_protocol,
            max_concurrency=payload.max_concurrency,
            model_release_id=payload.model_release_id,
            execution_mode=payload.execution_mode,
        )
        return service.get_experiment(experiment.id)


@router.get("", response_model=list[DatasetExperimentRead])
def list_experiments(request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list_experiments()


@router.get("/{experiment_id}", response_model=DatasetExperimentRead)
def get_experiment(experiment_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).get_experiment(experiment_id)


@router.get("/{experiment_id}/items", response_model=list[DatasetExperimentItemRead])
def list_items(experiment_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list_items(experiment_id)


@router.get("/{experiment_id}/items/{item_id}/attempts",
            response_model=list[DatasetExperimentAttemptRead])
def list_attempts(experiment_id: str, item_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list_attempts(
            item_id, experiment_id=experiment_id
        )


@router.post("/{experiment_id}/run", response_model=DatasetExperimentRead, status_code=202)
def run_experiment(experiment_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        service = _service(request, session)
        service.start_experiment(
            experiment_id, request.app.state.dataset_experiment_job_manager
        )
        return service.get_experiment(experiment_id)


@router.post("/{experiment_id}/retry-failed", response_model=DatasetExperimentRead,
             status_code=202)
def retry_failed(experiment_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        service = _service(request, session)
        service.retry_failed(
            experiment_id, request.app.state.dataset_experiment_job_manager
        )
        return service.get_experiment(experiment_id)


@router.post("/{experiment_id}/retry-evaluation", response_model=DatasetExperimentRead,
             status_code=202)
def retry_evaluation(experiment_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        service = _service(request, session)
        service.retry_evaluation(
            experiment_id, request.app.state.dataset_experiment_job_manager
        )
        return service.get_experiment(experiment_id)
