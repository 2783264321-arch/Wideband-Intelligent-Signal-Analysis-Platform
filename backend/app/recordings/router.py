from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from app.core.errors import PlatformError
from app.recordings.schema import (
    RecordingListRead,
    RecordingRead,
    RegisterRecordingPathRequest,
)
from app.recordings.service import PATH_FORMAT_BYTES, RecordingService
from app.recordings.spacenet_upload import SPACENET_UPLOAD_DATA_FORMAT, parse_upload_metadata
from app.lifecycle.service import delete_standalone_recording

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


def _service(request: Request, session):
    return RecordingService(session, request.app.state.storage, request.app.state.settings.data_root)


def _observation_range(document: str | None):
    """Best-effort [low_mhz, high_mhz] read used only to size the GT duration check."""
    if not document:
        return None
    try:
        payload = json.loads(document.lstrip("\ufeff").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("observation_range")
    if not isinstance(value, list) or len(value) != 2:
        return None
    try:
        low, high = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return (low, high) if low < high else None


@router.post("", response_model=RecordingRead, status_code=201)
def import_recording(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    sample_rate_hz: float | None = Form(None),
    center_frequency_hz: float | None = Form(None),
    data_format: str | None = Form(None),
    dataset_name: str | None = Form(None),
    dataset_split: str | None = Form(None),
    label_space: str | None = Form(None),
    metadata: UploadFile | None = File(None),
):
    """Import a managed IQ sample.

    Either supply ``sample_rate_hz`` + ``center_frequency_hz`` explicitly, or
    upload the SpaceNet ``.json`` sidecar as ``metadata`` so the platform derives
    sampling rate and center frequency from ``observation_range`` instead of
    trusting hand-typed values. Explicit form values always win.

    A SpaceNet sidecar also pins the IQ encoding: SpaceNet ships little-endian
    float16 I/Q pairs, so the uploaded file is read as ``float16_interleaved_le``
    rather than the default ``complex64_le``. Reading a float16 capture as
    complex64 halves the derived duration and mis-aligns every sample.
    """
    with request.app.state.database.session_factory() as session:
        metadata_document = None
        if metadata is not None:
            raw = metadata.file.read()
            if len(raw) > 1_048_576:
                raise PlatformError(
                    "INVALID_RECORDING_METADATA", "Uploaded IQ metadata file is too large.", 422
                )
            try:
                metadata_document = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise PlatformError(
                    "INVALID_RECORDING_METADATA",
                    "Uploaded IQ metadata is not valid UTF-8 text.",
                    422,
                ) from error

        # A SpaceNet sidecar fixes the encoding; an explicit form value still wins.
        resolved_format = data_format or (
            SPACENET_UPLOAD_DATA_FORMAT if metadata_document else "complex64_le"
        )
        if resolved_format not in PATH_FORMAT_BYTES:
            raise PlatformError(
                "INVALID_RECORDING",
                "Unsupported data format. Use complex64_le or float16_interleaved_le.",
                422,
            )

        # The sidecar carries no IQ length; discover it so ground-truth time
        # bounds can be validated against the real duration. An explicit Fs is
        # used for that check so the duration matches what will be persisted.
        bytes_per_sample = PATH_FORMAT_BYTES[resolved_format]
        position = file.file.tell()
        file.file.seek(0, 2)
        byte_size = file.file.tell()
        file.file.seek(position)
        effective_rate = sample_rate_hz if sample_rate_hz and sample_rate_hz > 0 else None
        upload_metadata_sample_count = (
            byte_size // bytes_per_sample if byte_size % bytes_per_sample == 0 else None
        )
        upload_metadata = parse_upload_metadata(
            metadata_document,
            label_space_root=request.app.state.settings.label_space_root,
            num_samples=upload_metadata_sample_count,
            sample_rate_hz=effective_rate,
        )

        resolved_name = (name or "").strip() or upload_metadata.sample_id or "sample"
        resolved_rate = sample_rate_hz if sample_rate_hz is not None else upload_metadata.sample_rate_hz
        resolved_center = (
            center_frequency_hz if center_frequency_hz is not None else upload_metadata.center_frequency_hz
        )
        if resolved_rate is None:
            raise PlatformError(
                "INVALID_RECORDING",
                "Sample rate is required. Provide sample_rate_hz or upload the SpaceNet JSON metadata.",
                422,
            )
        if resolved_center is None:
            raise PlatformError(
                "INVALID_RECORDING",
                "Center frequency is required. Provide center_frequency_hz or upload the SpaceNet JSON metadata.",
                422,
            )
        resolved_label_space = label_space or upload_metadata.label_space
        resolved_name = (
            (name or "").strip()
            or (upload_metadata.stem if metadata_document else "")
            or "sample"
        )

        return _service(request, session).import_uploaded_iq(
            upload=file,
            name=resolved_name,
            sample_rate_hz=resolved_rate,
            center_frequency_hz=resolved_center,
            data_format=resolved_format,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            label_space=resolved_label_space,
            ground_truth=upload_metadata.ground_truth,
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
