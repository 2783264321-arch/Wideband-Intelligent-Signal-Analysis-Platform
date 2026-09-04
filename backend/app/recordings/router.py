from fastapi import APIRouter, File, Form, Request, UploadFile

from app.recordings.schema import RecordingRead
from app.recordings.service import RecordingService

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


def _service(request: Request, session):
    return RecordingService(session, request.app.state.storage, request.app.state.settings.data_root)


@router.post("", response_model=RecordingRead, status_code=201)
def import_recording(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    sample_rate_hz: float = Form(...),
    center_frequency_hz: float = Form(...),
    data_format: str = Form("complex64_le"),
    dataset_name: str | None = Form(None),
    dataset_split: str | None = Form(None),
    label_space: str | None = Form(None),
):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).import_complex64(
            upload=file,
            name=name,
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=center_frequency_hz,
            data_format=data_format,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            label_space=label_space,
        )


@router.get("", response_model=list[RecordingRead])
def list_recordings(request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).list()


@router.get("/{recording_id}", response_model=RecordingRead)
def get_recording(recording_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).get(recording_id)
