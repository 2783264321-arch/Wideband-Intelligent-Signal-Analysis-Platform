"""Remote GPU job management over the secure transport.

The manager uploads a request to a deterministic inbox, invokes the fixed
runner for submit/status, and downloads exactly two result files per item. It
never mutates the deployed remote repository and never runs arbitrary remote
commands. All remote paths derive from the trusted configured root plus strict
validated identifiers.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from app.core.errors import PlatformError
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteExecutionBatchV1,
    parse_remote_execution_batch_json,
)
from app.remote_execution.transport import RemoteTransportError, SshRunner

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


def _require_identifier(value: str, name: str, error_code: str) -> None:
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise PlatformError(error_code, f"{name} must be a safe identifier.")


def _parse_status_json(text: str) -> RemoteBatchStatusV1:
    def _unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def _constant(value: str):
        raise ValueError(f"non-finite JSON constant: {value!r}")

    try:
        payload = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, TypeError):
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "Remote status JSON is invalid.")
    if not isinstance(payload, dict):
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "Remote status must be a JSON object.")
    try:
        return RemoteBatchStatusV1.model_validate(payload)
    except Exception:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "Remote status schema is invalid.")


class RemoteGpuJobManager:
    def __init__(self, profile: RemoteProfile, transport: SshRunner) -> None:
        self.profile = profile
        self.transport = transport

    def submit(self, batch: RemoteExecutionBatchV1, request_json_path: Path) -> None:
        _require_identifier(batch.batch_id, "batch_id", "REMOTE_SUBMIT_FAILED")
        if not request_json_path.is_file():
            raise PlatformError("REMOTE_SUBMIT_FAILED", "Local request file must be a regular file.")
        try:
            parsed = parse_remote_execution_batch_json(request_json_path.read_bytes())
        except Exception:
            raise PlatformError("REMOTE_SUBMIT_FAILED", "Local request file could not be parsed.")
        if parsed != batch:
            raise PlatformError("REMOTE_SUBMIT_FAILED", "Local request file does not match the supplied batch.")
        remote_request_path = self.profile.remote_job_root / "incoming" / f"{batch.batch_id}.request.json"
        try:
            self.transport.upload_file(request_json_path, remote_request_path)
            self.transport.run_runner("submit", ("--request-path", remote_request_path.as_posix()))
        except RemoteTransportError:
            raise PlatformError("REMOTE_SUBMIT_FAILED", "Remote submit transport failed.")

    def status(self, batch_id: str) -> RemoteBatchStatusV1:
        _require_identifier(batch_id, "batch_id", "REMOTE_STATUS_UNAVAILABLE")
        try:
            result = self.transport.run_runner("status", ("--batch-id", batch_id))
        except RemoteTransportError:
            raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "Remote status transport failed.")
        return _parse_status_json(result.stdout)

    def download(self, batch_id: str, item_key: str, dest_dir: Path) -> Path:
        _require_identifier(batch_id, "batch_id", "REMOTE_DOWNLOAD_FAILED")
        _require_identifier(item_key, "item_key", "REMOTE_DOWNLOAD_FAILED")
        remote_base = self.profile.remote_job_root / batch_id / "results" / item_key
        local_dir = dest_dir / item_key
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.transport.download_file(remote_base / "envelope.json", local_dir / "envelope.json")
            self.transport.download_file(remote_base / "analysis_result.zip", local_dir / "analysis_result.zip")
        except RemoteTransportError:
            raise PlatformError("REMOTE_DOWNLOAD_FAILED", "Remote result download failed.")
        for name in ("envelope.json", "analysis_result.zip"):
            if not (local_dir / name).is_file():
                raise PlatformError("REMOTE_DOWNLOAD_FAILED", "Remote result file is missing after download.")
        return local_dir