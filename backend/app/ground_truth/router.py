from fastapi import APIRouter, Request

from app.ground_truth.schema import GroundTruthImport, GroundTruthRead
from app.ground_truth.service import GroundTruthService
from app.labels.service import LabelSpaceService

router = APIRouter(prefix="/api/recordings", tags=["ground-truth"])


def _service(request: Request, session) -> GroundTruthService:
    return GroundTruthService(session, LabelSpaceService(request.app.state.settings.label_space_root))


@router.post("/{recording_id}/ground-truth", response_model=list[GroundTruthRead])
def import_ground_truth(recording_id: str, payload: GroundTruthImport, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).replace(recording_id, payload)


@router.get("/{recording_id}/ground-truth", response_model=list[GroundTruthRead])
def list_ground_truth(recording_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list(recording_id)
