from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from app.recordings.schema import (
    RecordingListRead,
    RecordingRead,
    RegisterRecordingPathRequest,
)
from app.recordings.service import RecordingService
from app.lifecycle.service import delete_standalone_recording

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


@router.post("/register-path", response_model=RecordingRead, status_code=201)
def register_recording_path(payload: RegisterRecordingPathRequest, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).register_local_path(
            path=payload.path,
            name=payload.name,
            data_format=payload.data_format,
            sample_rate_hz=payload.sample_rate_hz,
            center_frequency_hz=payload.center_frequency_hz,
            label_space=payload.label_space,
        )


@router.get("", response_model=RecordingListRead)
def list_recordings(request: Request, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    with request.app.state.database.session_factory() as session:
        items, total = _service(request, session).list(limit=limit, offset=offset)
        return RecordingListRead(items=items, total=total)


@router.get("/{recording_id}", response_model=RecordingRead)
def get_recording(recording_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        return _service(request, session).get(recording_id)


@router.delete("/{recording_id}", status_code=204)
def delete_recording(recording_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        delete_standalone_recording(session, request.app.state.storage, recording_id)
