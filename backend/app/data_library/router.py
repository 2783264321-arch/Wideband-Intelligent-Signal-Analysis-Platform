from fastapi import APIRouter, Query, Request

from app.data_library.schema import (
    DatasetAnalysisHistoryListRead,
    DatasetProjectionListRead,
    DatasetProjectionSummaryRead,
    DatasetSampleListRead,
    StandaloneSampleListRead,
)
from app.data_library.service import DataLibraryService
from app.lifecycle.service import remove_dataset_projection

router = APIRouter(prefix="/api/data-library", tags=["data-library"])


@router.get("/datasets", response_model=DatasetProjectionListRead)
def list_datasets(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with request.app.state.database.session_factory() as session:
        items, total = DataLibraryService(session).list_dataset_projections(limit, offset)
        return DatasetProjectionListRead(items=items, total=total)


@router.get("/datasets/{dataset_projection_id}", response_model=DatasetProjectionSummaryRead)
def get_dataset(dataset_projection_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return DataLibraryService(session).get_dataset_projection(dataset_projection_id)


@router.get("/datasets/{dataset_projection_id}/samples", response_model=DatasetSampleListRead)
def list_dataset_samples(
    dataset_projection_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=200),
):
    with request.app.state.database.session_factory() as session:
        items, total = DataLibraryService(session).list_dataset_samples(
            dataset_projection_id, limit, offset, search
        )
        return DatasetSampleListRead(
            dataset_projection_id=dataset_projection_id, items=items, total=total
        )


@router.get(
    "/datasets/{dataset_projection_id}/analysis-history",
    response_model=DatasetAnalysisHistoryListRead,
)
def list_dataset_analysis_history(dataset_projection_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        items = DataLibraryService(session).list_dataset_analysis_history(dataset_projection_id)
        return DatasetAnalysisHistoryListRead(
            dataset_projection_id=dataset_projection_id, items=items, total=len(items)
        )


@router.get("/standalone-samples", response_model=StandaloneSampleListRead)
def list_standalone_samples(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=200),
):
    with request.app.state.database.session_factory() as session:
        items, total = DataLibraryService(session).list_standalone_samples(limit, offset, search)
        return StandaloneSampleListRead(items=items, total=total)


@router.delete("/datasets/{dataset_projection_id}", status_code=204)
def remove_dataset(dataset_projection_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        remove_dataset_projection(session, request.app.state.storage, dataset_projection_id)
