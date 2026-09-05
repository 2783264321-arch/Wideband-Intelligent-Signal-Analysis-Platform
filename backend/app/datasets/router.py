from fastapi import APIRouter, Request

from app.datasets.schema import RegisterSpaceNetRequest, RegistrationSummaryRead
from app.datasets.service import SpaceNetRegistrationService

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


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
        )