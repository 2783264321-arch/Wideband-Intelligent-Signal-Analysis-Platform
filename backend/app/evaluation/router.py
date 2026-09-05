from fastapi import APIRouter, Request

from app.evaluation.schema import AlgorithmLabCompareRequest, AlgorithmLabCompareResponse
from app.evaluation.service import AlgorithmLabComparisonService

router = APIRouter(prefix="/api/algorithm-lab", tags=["algorithm-lab"])


@router.post("/compare", response_model=AlgorithmLabCompareResponse)
def compare_runs(payload: AlgorithmLabCompareRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        service = AlgorithmLabComparisonService(session, request.app.state.pipeline_registry)
        return service.compare(
            recording_id=payload.recording_id,
            run_a_id=payload.run_a_id,
            run_b_id=payload.run_b_id,
            iou_threshold=payload.iou_threshold,
        )