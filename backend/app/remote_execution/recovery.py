"""Startup recovery for remote AnalysisRuns.

- ``local_cpu`` ``running`` runs are interrupted on startup (unchanged
  semantics, scoped to ``local_cpu`` only).
- ``remote_gpu`` ``pending``/``running`` runs are NEVER blindly interrupted:
  when valid remote config exists they are re-coordinated under a freshly
  rotated local fencing token; otherwise they are left untouched and no
  coordinator is launched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.remote_execution.startup import rotate_coordinator_token


def mark_stale_local_cpu_runs_interrupted(session: Session) -> int:
    """Interrupt stale ``local_cpu`` running runs only. Remote runs untouched."""
    statement = (
        update(AnalysisRunModel)
        .where(AnalysisRunModel.status == "running")
        .where(AnalysisRunModel.executor == "local_cpu")
        .values(
            status="interrupted",
            error_type="ANALYSIS_INTERRUPTED",
            error_message="Previous local analysis process ended before platform restart.",
            finished_at=datetime.now(timezone.utc),
        )
    )
    result = session.execute(statement)
    session.commit()
    return int(result.rowcount or 0)


def find_orphaned_remote_runs(session: Session) -> list[str]:
    result = session.scalars(
        select(AnalysisRunModel.id)
        .where(AnalysisRunModel.executor == "remote_gpu")
        .where(AnalysisRunModel.status.in_(("pending", "running")))
    ).all()
    return list(result)


def rotate_coordinator_token(metadata: dict) -> dict:
    """Replace the coordinator_token on the final local execution metadata.

    Never alters a frozen-request field; never changes ``build_batch()`` /
    ``request_sha256`` (the token is excluded from both). Returns a new dict
    without mutating the input.
    """
    result = dict(metadata)
    result["coordinator_token"] = f"coord_{uuid4().hex}"
    return result


def remote_config_available(settings=None) -> bool:
    """True iff a complete, valid RemoteProfile can be built from env.

    Bootstrap validity comes from actual ``RemoteProfile.from_env`` validation
    (which requires the SSH key, known-hosts, port, repo/job roots, python path,
    and the runtime commit), not from a partial env-key presence list.
    """
    from app.remote_execution.profile import RemoteProfile

    try:
        RemoteProfile.from_env(settings)
        return True
    except Exception:
        return False


def coordinate_orphaned_remote_runs(
    session: Session,
    *,
    launcher,
    remote_config_available: bool,
    seen_run_ids: set[str],
) -> int:
    """Re-coordinate orphaned remote_gpu pending/running runs.

    If remote config is unavailable, leaves the runs untouched and launches
    nothing. Otherwise rotates a fresh coordinator token and launches one
    coordinator per run, deduped within a single pass via ``seen_run_ids``.
    """
    if not remote_config_available:
        return 0
    orphans = find_orphaned_remote_runs(session)
    launches = 0
    for run_id in orphans:
        if run_id in seen_run_ids:
            continue
        run = session.get(AnalysisRunModel, run_id)
        if run is None or run.executor != "remote_gpu":
            continue
        metadata = dict(run.execution_metadata_json or {})
        rotated = rotate_coordinator_token(metadata)
        run.execution_metadata_json = rotated
        coordinator_token = rotated["coordinator_token"]
        session.commit()
        launcher.launch(run_id, coordinator_token)
        seen_run_ids.add(run_id)
        launches += 1
    return launches