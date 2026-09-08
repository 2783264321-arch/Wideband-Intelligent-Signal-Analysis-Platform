"""Single remote AnalysisRun coordinator (local/fenced subprocess).

The coordinator drives ONE remote ``AnalysisRun`` via ``RemoteGpuJobManager``:
submit-or-attach the same immutable batch, poll the remote batch, download +
ingest on completion, and map terminal states — under a per-launch local
``coordinator_token`` fencing token.

Fencing rules
- The coordinator opens a fresh short-lived DB session per action
  (``_open_current_session``); it never holds one transaction across a poll sleep.
- Before every remote side effect and terminal mutation it re-checks the fence in
  a current session (``_require_current_fence``).
- For a completed ingest, the fence check + ingest + status commit occur in the
  SAME DB session/transaction.
- ``result_ingestor`` / ``AnalysisResultWriter`` remains the only persistence path.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable

from app.core.errors import PlatformError
from app.remote_execution.request_builder import build_batch, verify_request_sha256


class _StaleFence(RuntimeError):
    """The coordinator token no longer matches the persisted run token."""


def _default_ingest(session, run, envelope, zip_path, writer) -> str:
    from app.remote_execution.result_ingestor import ingest_remote_result

    return ingest_remote_result(session, run.id, envelope, zip_path, writer)


class Coordinator:
    def __init__(
        self,
        *,
        session_factory,
        job_manager,
        metadata,
        coordinator_token,
        sleep_fn: Callable[[float], None] = time.sleep,
        poll_interval: float = 1.0,
        max_polls: int | None = None,
        ingest: Callable | None = None,
        writer=None,
        logger=None,
    ) -> None:
        self._session_factory = session_factory
        self._job_manager = job_manager
        self._metadata = metadata
        self._coordinator_token = coordinator_token
        self._sleep_fn = sleep_fn
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._ingest = ingest or _default_ingest
        self._writer = writer
        self._logger = logger
        self._run_id = metadata.get("local_run_id")

    def _open_current_session(self):
        return self._session_factory()

    def _require_current_fence(self, session):
        from app.analysis.model import AnalysisRunModel

        run = session.get(AnalysisRunModel, self._run_id)
        if run is None:
            raise _StaleFence(f"AnalysisRun {self._run_id} not found.")
        if run.executor != "remote_gpu":
            raise _StaleFence("AnalysisRun is not a remote_gpu run.")
        metadata = run.execution_metadata_json or {}
        if metadata.get("coordinator_token") != self._coordinator_token:
            raise _StaleFence("coordinator token is stale.")
        return run

    def _read_current_status(self) -> str:
        with self._open_current_session() as session:
            from app.analysis.model import AnalysisRunModel

            run = session.get(AnalysisRunModel, self._run_id)
            if run is None:
                return "interrupted"
            return run.status

    def _set_status(self, session, run, *, status, error_type=None, error_message=None,
                    started_at=None, finished_at=None, commit=True) -> None:
        run.status = status
        if error_type is not None:
            run.error_type = error_type
        if error_message is not None:
            run.error_message = error_message[:1000]
        if started_at is not None and run.started_at is None:
            run.started_at = started_at
        if finished_at is not None:
            run.finished_at = finished_at
        if commit:
            session.commit()

    def run(self) -> str:
        from datetime import datetime, timezone

        batch = build_batch(self._metadata)
        if verify_request_sha256(batch, self._metadata) is False:
            raise PlatformError("REMOTE_REQUEST_INVALID", "request_sha256 reconstruction failed.")

        # Fence-check + submit-or-attach in one session/transaction (the submit
        # remote side effect is guarded by a current fence).
        with self._open_current_session() as session:
            try:
                run = self._require_current_fence(session)
            except _StaleFence:
                return self._metadata.get("status", self._read_current_status())
            if run.status == "completed":
                return "completed"  # idempotent
            if run.status in {"failed", "interrupted"}:
                return run.status  # immutable terminal
            try:
                self._job_manager.submit(batch, self._request_json_path(batch))
            except PlatformError as exc:
                if exc.code in {"REMOTE_SUBMIT_FAILED", "REMOTE_TRANSPORT_UNAVAILABLE",
                                "REMOTE_REQUEST_INVALID"}:
                    # Uncertain submit: reconcile the SAME batch before a terminal decision.
                    pass
                else:
                    raise
            self._set_status(session, run, status="pending")
            started = run.started_at

        polls = 0
        while True:
            with self._open_current_session() as session:
                try:
                    run = self._require_current_fence(session)
                except _StaleFence:
                    return self._read_current_status()
                if run.status in {"completed", "failed", "interrupted"}:
                    return run.status
                try:
                    remote = self._job_manager.status(batch.batch_id)
                except PlatformError as exc:
                    if exc.code in {"REMOTE_STATUS_UNAVAILABLE", "REMOTE_TRANSPORT_UNAVAILABLE"}:
                        polls += 1
                        if self._max_polls is not None and polls >= self._max_polls:
                            return run.status  # remain recoverable; no terminal
                        self._sleep_fn(self._poll_interval)
                        continue
                    raise
                status = remote.status
                if status == "queued":
                    self._set_status(session, run, status="pending")
                elif status == "running":
                    self._set_status(session, run, status="running",
                                     started_at=datetime.now(timezone.utc))
                elif status == "completed":
                    return self._finish_completed(session, run, batch)
                elif status == "failed":
                    self._set_status(session, run, status="failed", error_type="ANALYSIS_FAILED",
                                     error_message="Remote analysis failed.",
                                     finished_at=datetime.now(timezone.utc))
                    return "failed"
                elif status == "interrupted":
                    self._set_status(session, run, status="interrupted", error_type="ANALYSIS_INTERRUPTED",
                                     error_message="Remote analysis was interrupted.",
                                     finished_at=datetime.now(timezone.utc))
                    return "interrupted"

            polls += 1
            if self._max_polls is not None and polls >= self._max_polls:
                return run.status
            self._sleep_fn(self._poll_interval)

    def _finish_completed(self, session, run, batch) -> str:
        from datetime import datetime, timezone

        # Re-check fence in the SAME transaction that will ingest + commit.
        try:
            self._require_current_fence(session)
        except _StaleFence:
            return "interrupted"  # stale token -> no download/ingest, no mutation
        try:
            dest = self._job_manager.download(batch.batch_id, batch.items[0].item_key, self._workspace())
        except PlatformError:
            return "interrupted"  # unrecoverable remote job
        try:
            envelope = self._parse_envelope(dest / "envelope.json")
        except PlatformError:
            return "interrupted"
        payload_sha = self._ingest(session, run, envelope, dest / "analysis_result.zip", self._writer)
        self._set_status(session, run, status="completed", finished_at=datetime.now(timezone.utc))
        return "completed"

    def _request_json_path(self, batch):
        import tempfile

        temp_dir = Path(tempfile.gettempdir())
        path = temp_dir / f"{batch.batch_id}.request.json"
        path.write_text(self._serialize_batch(batch), encoding="utf-8")
        return path

    def _serialize_batch(self, batch) -> str:
        return json.dumps(batch.model_dump(mode="json"))

    def _parse_envelope(self, path):
        from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json

        return parse_remote_execution_envelope_json(path.read_bytes())

    def _workspace(self) -> Path:
        import tempfile

        workspace = Path(tempfile.gettempdir()) / f"coordinator_{self._run_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace


def main(argv: list[str] | None = None) -> int:
    """python -m app.remote_execution.coordinator <run_id> --coordinator-token <token>"""
    parser = argparse.ArgumentParser(prog="python -m app.remote_execution.coordinator")
    parser.add_argument("run_id")
    parser.add_argument("--coordinator-token", required=True)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args(argv)

    import os

    from app.db.session import Database
    from app.remote_execution.identity import (
        resolve_asset_manifest_sha256,
        resolve_local_orchestrator_commit,
    )
    from app.remote_execution.profile import RemoteProfile
    from app.remote_execution.transport import SshRunner
    from app.remote_execution.job_manager import RemoteGpuJobManager

    database_url = os.environ.get("WSP_DATABASE_URL")
    if not database_url:
        raise SystemExit("WSP_DATABASE_URL is not set")
    database = Database(database_url)
    with database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel

        run = session.get(AnalysisRunModel, args.run_id)
        if run is None:
            print("ANALYSIS_RUN_NOT_FOUND: Analysis run was not found.", file=sys.stderr)
            return 1
        metadata = dict(run.execution_metadata_json or {})

    profile = RemoteProfile.from_env(None)
    job_manager = RemoteGpuJobManager(profile, SshRunner(profile))
    coordinator = Coordinator(
        session_factory=database.session_factory,
        job_manager=job_manager,
        metadata=metadata,
        coordinator_token=args.coordinator_token,
        poll_interval=args.poll_interval,
    )
    try:
        coordinator.run()
    except Exception as exc:
        print(f"COORDINATOR_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())