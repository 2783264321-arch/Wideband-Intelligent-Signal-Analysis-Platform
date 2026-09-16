"""Fail-safe destructive operations with a shared dependency preflight.

Managed-file choreography: preflight -> same-filesystem rename into quarantine
-> DB transaction -> restore on DB failure -> remove quarantine after a
successful commit. External source paths are NEVER deletion targets.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.data_library.service import DataLibraryService
from app.lifecycle.preflight import find_recording_blockers, find_run_blockers
from app.lifecycle.schema import blocker_details
from app.recordings.model import RecordingModel


@contextmanager
def quarantine_managed_dirs(storage, managed_dirs: Iterable[Path]):
    token = uuid4().hex
    moved: list[tuple[Path, Path]] = []
    try:
        for index, source in enumerate(managed_dirs):
            if not source.exists():
                continue
            target = storage.quarantine_root() / f"{token}_{index}"
            os.replace(source, target)
            moved.append((source, target))
        yield
    except Exception:
        for source, target in reversed(moved):
            if target.exists():
                os.replace(target, source)
        raise
    else:
        for _source, target in moved:
            shutil.rmtree(target, ignore_errors=True)


def _run_owned_managed_dirs(storage, run: AnalysisRunModel) -> list[Path]:
    managed = [storage.artifact_path(run.id)]
    payload = run.parameters_json or {}
    if "package" in payload and "batch_import" not in payload:
        managed.append(storage.import_package_dir(run.id))
    return managed


def _owned_runs(session, recording_ids: list[str]) -> list[AnalysisRunModel]:
    if not recording_ids:
        return []
    return list(
        session.scalars(
            select(AnalysisRunModel).where(AnalysisRunModel.recording_id.in_(recording_ids))
        ).all()
    )


def delete_analysis_run(session, storage, run_id: str) -> None:
    run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run not found.", 404)
    blockers = find_run_blockers(session, [run_id])
    if blockers:
        raise PlatformError(
            "ANALYSIS_RUN_DELETE_BLOCKED",
            "Analysis run deletion is blocked by retained dependents.",
            409,
            blocker_details(blockers),
        )
    managed = _run_owned_managed_dirs(storage, run)
    with quarantine_managed_dirs(storage, managed):
        session.delete(run)
        session.commit()


def delete_standalone_recording(session, storage, recording_id: str) -> None:
    recording = session.get(RecordingModel, recording_id)
    if recording is None:
        raise PlatformError("RECORDING_NOT_FOUND", "Recording not found.", 404)
    if recording.dataset_name is not None:
        raise PlatformError(
            "RECORDING_IS_DATASET_MEMBER",
            "Dataset members are removed through the dataset; use Remove Dataset.",
            409,
            {"dataset_name": recording.dataset_name, "dataset_split": recording.dataset_split},
        )
    blockers = find_recording_blockers(session, [recording_id])
    if blockers:
        raise PlatformError(
            "RECORDING_DELETE_BLOCKED",
            "Recording deletion is blocked by retained dependents.",
            409,
            blocker_details(blockers),
        )
    managed: list[Path] = []
    if recording.external_path is None and recording.source == "custom":
        managed.append(storage.recording_dir(recording_id))
    for run in _owned_runs(session, [recording_id]):
        managed.extend(_run_owned_managed_dirs(storage, run))
    with quarantine_managed_dirs(storage, managed):
        session.delete(recording)
        session.commit()


def remove_dataset_projection(session, storage, dataset_projection_id: str) -> None:
    service = DataLibraryService(session)
    _projection, members = service._projection_members(dataset_projection_id)
    member_ids = [recording.id for recording in members]
    blockers = find_recording_blockers(session, member_ids)
    if blockers:
        raise PlatformError(
            "DATASET_REMOVE_BLOCKED",
            "Dataset removal is blocked by retained dependents; no members were removed.",
            409,
            blocker_details(blockers),
        )
    managed: list[Path] = []
    for recording in members:
        if recording.external_path is None and recording.source == "custom":
            managed.append(storage.recording_dir(recording.id))
    for run in _owned_runs(session, member_ids):
        managed.extend(_run_owned_managed_dirs(storage, run))
    with quarantine_managed_dirs(storage, managed):
        for recording in members:
            session.delete(recording)
        session.commit()
