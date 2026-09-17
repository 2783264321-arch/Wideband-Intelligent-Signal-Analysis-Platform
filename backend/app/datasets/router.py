from fastapi import APIRouter, Query, Request

from app.datasets.read_service import DatasetReadService
from app.datasets.schema import (
    DatasetListRead,
    DatasetRead,
    DatasetSampleListRead,
    RegisterSpaceNetRequest,
    RegistrationSummaryRead,
)
from app.datasets.service import SpaceNetRegistrationService

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=DatasetListRead)
def list_datasets(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with request.app.state.database.session_factory() as session:
        items, total = DatasetReadService(session).list_datasets(limit, offset)
        return DatasetListRead(items=items, total=total)


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return DatasetReadService(session).get_dataset(dataset_id)


@router.get("/{dataset_id}/samples", response_model=DatasetSampleListRead)
def list_dataset_samples(
    dataset_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=200),
):
    with request.app.state.database.session_factory() as session:
        items, total = DatasetReadService(session).list_samples(dataset_id, limit, offset, search)
        return DatasetSampleListRead(dataset_id=dataset_id, items=items, total=total)


@router.post("/spacenet/register", response_model=RegistrationSummaryRead)
def register_spacenet(payload: RegisterSpaceNetRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        service = SpaceNetRegistrationService(session, request.app.state.settings.label_space_root)
        summary = service.register_directory(payload.dataset_path, payload.split)
        return RegistrationSummaryRead(
            created=summary.created,
            skipped=summary.skipped,
            invalid=summary.invalid,
            total=summary.total,
            dataset_id=summary.dataset_id,
        )
