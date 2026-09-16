"""Shared dependency preflight for destructive operations.

Guards standalone Recording deletion, dataset removal, and AnalysisRun deletion.
Combines real FK references with imported-batch *semantic* state (BAPv1), which
is not represented by a foreign key: a partial deletion of an imported
fingerprint would corrupt idempotent reimport.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
)


@dataclass(frozen=True)
class DeleteBlocker:
    kind: str  # dataset_evaluation | dataset_experiment | dataset_experiment_attempt | imported_batch
    resource_id: str
    reference: str  # recording | analysis_run


def _dedupe(blockers: list[DeleteBlocker]) -> list[DeleteBlocker]:
    seen: set[tuple[str, str, str]] = set()
    result: list[DeleteBlocker] = []
    for blocker in blockers:
        key = (blocker.kind, blocker.resource_id, blocker.reference)
        if key in seen:
            continue
        seen.add(key)
        result.append(blocker)
    return result


def _fk_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]:
    ids = list(run_ids)
    if not ids:
        return []
    blockers: list[DeleteBlocker] = []
    rows = session.execute(
        select(DatasetEvaluationItemModel.evaluation_id).where(
            DatasetEvaluationItemModel.analysis_run_id.in_(ids)
        )
    ).all()
    for (evaluation_id,) in rows:
        blockers.append(DeleteBlocker("dataset_evaluation", evaluation_id, "analysis_run"))
    rows = session.execute(
        select(DatasetExperimentAttemptModel.id).where(
            DatasetExperimentAttemptModel.analysis_run_id.in_(ids)
        )
    ).all()
    for (attempt_id,) in rows:
        blockers.append(DeleteBlocker("dataset_experiment_attempt", attempt_id, "analysis_run"))
    return blockers


def _fk_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]:
    ids = list(recording_ids)
    if not ids:
        return []
    blockers: list[DeleteBlocker] = []
    rows = session.execute(
        select(DatasetEvaluationItemModel.evaluation_id).where(
            DatasetEvaluationItemModel.recording_id.in_(ids)
        )
    ).all()
    for (evaluation_id,) in rows:
        blockers.append(DeleteBlocker("dataset_evaluation", evaluation_id, "recording"))
    rows = session.execute(
        select(DatasetExperimentItemModel.experiment_id).where(
            DatasetExperimentItemModel.recording_id.in_(ids)
        )
    ).all()
    for (experiment_id,) in rows:
        blockers.append(DeleteBlocker("dataset_experiment", experiment_id, "recording"))
    return blockers


def _batch_fingerprint(run: AnalysisRunModel | None) -> str | None:
    if run is None:
        return None
    payload = (run.parameters_json or {}).get("batch_import")
    if not isinstance(payload, dict):
        return None
    fingerprint = payload.get("import_fingerprint")
    return fingerprint if isinstance(fingerprint, str) and fingerprint else None


def _runs_by_fingerprint(session) -> dict[str, set[str]]:
    runs = session.scalars(
        select(AnalysisRunModel).where(
            AnalysisRunModel.executor == "imported",
            AnalysisRunModel.status == "completed",
        )
    ).all()
    grouped: dict[str, set[str]] = {}
    for run in runs:
        fingerprint = _batch_fingerprint(run)
        if fingerprint:
            grouped.setdefault(fingerprint, set()).add(run.id)
    return grouped


def _imported_batch_blockers(
    session, proposed_run_ids: set[str], reference: str
) -> list[DeleteBlocker]:
    if not proposed_run_ids:
        return []
    fingerprints = {
        fingerprint
        for run_id in proposed_run_ids
        if (fingerprint := _batch_fingerprint(session.get(AnalysisRunModel, run_id)))
    }
    if not fingerprints:
        return []
    index = _runs_by_fingerprint(session)
    blockers: list[DeleteBlocker] = []
    for fingerprint in fingerprints:
        complete = index.get(fingerprint, set())
        if not complete <= proposed_run_ids:
            blockers.append(DeleteBlocker("imported_batch", fingerprint, reference))
    return blockers


_ACTIVE_RUN_STATUSES = ("pending", "running")


def _active_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]:
    ids = list(run_ids)
    if not ids:
        return []
    rows = session.execute(
        select(AnalysisRunModel.id, AnalysisRunModel.status).where(AnalysisRunModel.id.in_(ids))
    ).all()
    return [
        DeleteBlocker("active_analysis_run", run_id, "analysis_run")
        for run_id, status in rows
        if status in _ACTIVE_RUN_STATUSES
    ]


def find_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]:
    ids = list(run_ids)
    return _dedupe(
        _fk_run_blockers(session, ids)
        + _imported_batch_blockers(session, set(ids), "analysis_run")
        + _active_run_blockers(session, ids)
    )


def find_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]:
    ids = list(recording_ids)
    owned = (
        set(
            session.scalars(
                select(AnalysisRunModel.id).where(AnalysisRunModel.recording_id.in_(ids))
            ).all()
        )
        if ids
        else set()
    )
    return _dedupe(
        _fk_recording_blockers(session, ids)
        + _fk_run_blockers(session, owned)
        + _imported_batch_blockers(session, owned, "recording")
        + _active_run_blockers(session, owned)
    )
