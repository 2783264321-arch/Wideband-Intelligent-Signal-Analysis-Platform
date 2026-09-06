from fastapi import APIRouter, Request

from app.benchmarks.schema import (
    DatasetBenchmarkCompareRequest,
    DatasetBenchmarkCompareResponse,
    DatasetEvaluationCreate,
    DatasetEvaluationItemRead,
    DatasetEvaluationRead,
    DatasetManifestPreviewRead,
    DatasetSelection,
    FrozenRunItemInput,
    ImportedBatchCatalogRead,
    ImportedBatchResolutionPreviewRead,
    ImportedBatchResolveRequest,
    RunResolutionPreviewRead,
    RunResolutionRequest,
)
from app.benchmarks.service import DatasetBenchmarkService

router = APIRouter(prefix="/api/dataset-benchmarks", tags=["dataset-benchmarks"])


def _service(request: Request, session) -> DatasetBenchmarkService:
    return DatasetBenchmarkService(session)


@router.post("/prepare", response_model=DatasetManifestPreviewRead)
def prepare_manifest(payload: DatasetSelection, request: Request):
    with request.app.state.database.session_factory() as session:
        preview = _service(request, session).prepare_manifest(
            payload.dataset_name, payload.dataset_split, payload.label_space)
        return DatasetManifestPreviewRead(
            dataset_name=payload.dataset_name,
            dataset_split=payload.dataset_split,
            label_space=payload.label_space,
            recording_manifest_hash=preview.recording_manifest_hash,
            expected_recordings=preview.expected_recordings,
            entries=[entry.__dict__ for entry in preview.entries],
        )


@router.post("/resolve-runs", response_model=RunResolutionPreviewRead)
def resolve_runs(payload: RunResolutionRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        preview = _service(request, session).resolve_pipeline_snapshot(
            payload.dataset_name, payload.dataset_split, payload.label_space,
            pipeline_id=payload.pipeline_id, pipeline_version=payload.pipeline_version)
        return RunResolutionPreviewRead(
            dataset_name=payload.dataset_name,
            dataset_split=payload.dataset_split,
            label_space=payload.label_space,
            pipeline_id=payload.pipeline_id,
            pipeline_version=payload.pipeline_version,
            recording_manifest_hash=preview.recording_manifest_hash,
            entries=[entry.__dict__ for entry in preview.entries],
        )


@router.post("", response_model=DatasetEvaluationRead, status_code=201)
def create_evaluation(payload: DatasetEvaluationCreate, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).create_evaluation(
            name=payload.name,
            dataset_name=payload.dataset_name,
            dataset_split=payload.dataset_split,
            label_space=payload.label_space,
            recording_manifest_hash=payload.recording_manifest_hash,
            items=[item.model_dump() for item in payload.items],
            allow_incomplete=payload.allow_incomplete,
        )


@router.get("", response_model=list[DatasetEvaluationRead])
def list_evaluations(request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list_evaluations()


@router.get("/imported-batches", response_model=list[ImportedBatchCatalogRead])
def list_imported_batches(request: Request):
    with request.app.state.database.session_factory() as session:
        entries = _service(request, session).list_imported_batches()
        return [
            {
                **{k: v for k, v in entry.__dict__.items() if k != "inconsistency_reasons"},
                "inconsistency_reasons": list(entry.inconsistency_reasons),
            }
            for entry in entries
        ]


@router.post("/resolve-imported-batch", response_model=ImportedBatchResolutionPreviewRead)
def resolve_imported_batch(payload: ImportedBatchResolveRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        preview = _service(request, session).resolve_imported_batch(payload.import_fingerprint)
        return {
            **{k: v for k, v in preview.__dict__.items() if k != "entries"},
            "entries": [entry.__dict__ for entry in preview.entries],
        }


@router.get("/{evaluation_id}", response_model=DatasetEvaluationRead)
def get_evaluation(evaluation_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).get_evaluation(evaluation_id)


@router.get("/{evaluation_id}/items", response_model=list[DatasetEvaluationItemRead])
def list_items(evaluation_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list_items(evaluation_id)


@router.post("/{evaluation_id}/run", response_model=DatasetEvaluationRead, status_code=202)
def run_evaluation(evaluation_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).start_evaluation(
            evaluation_id, request.app.state.benchmark_job_manager)


@router.post("/{evaluation_id}/retry", response_model=DatasetEvaluationRead)
def retry_evaluation(evaluation_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).retry_evaluation(evaluation_id)


@router.post("/compare", response_model=DatasetBenchmarkCompareResponse)
def compare_evaluations(payload: DatasetBenchmarkCompareRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).compare_evaluations(
            payload.evaluation_a_id, payload.evaluation_b_id)