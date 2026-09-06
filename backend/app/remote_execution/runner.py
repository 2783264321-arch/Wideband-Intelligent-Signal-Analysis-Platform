"""M9.1-B Task 8 detached server remote runner.

Filesystem/protocol-only runner core. It never touches an ORM, SQLite, the
Recording model, or ``ImportedRunService``. Job directories are:

    job_root/
        request.json
        status.json
        results/
            <item_key>/
                envelope.json
                analysis_result.zip

Module import time imports ONLY the protocol/schema/canonical/error modules.
The Task-3/6 verification primitives (``parse_remote_execution_envelope_json``
and ``compute_file_sha256``) are imported lazily inside the verification
helper, and the future production ``ItemExecutor`` (resolver/assets/pipeline)
is imported lazily inside the ``work`` CLI handler. No ``torch`` /
``ultralytics`` / CUDA / legacy imports at module level.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Literal, Protocol

from app.core.errors import PlatformError
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteItemStatusV1,
    parse_remote_execution_batch_json,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")

_ENVELOPE_FILENAME = "envelope.json"
_PAYLOAD_FILENAME = "analysis_result.zip"
_RESULT_RELATIVE_PREFIX = "results"


class ItemExecutor(Protocol):
    """An executor that publishes a terminal result for one item.

    The runner owns lifecycle, verification, write-once behavior and status;
    the executor owns actual result creation.
    """

    def execute(self, item: RemoteExecutionItemV1, job_root: Path) -> None:
        ...


# ---------------------------------------------------------------------------
# Identifier / path validation
# ---------------------------------------------------------------------------


def _require_identifier(value: str, name: str, error_code: str) -> None:
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise PlatformError(error_code, f"{name} must be a safe identifier.")


def _require_absolute_safe_path(value: str, name: str, error_code: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PlatformError(error_code, f"{name} must be an absolute path.")
    if any(part in ("", ".", "..") for part in path.parts):
        raise PlatformError(error_code, f"{name} is not a safe absolute path.")
    return path


# ---------------------------------------------------------------------------
# Atomic file publication
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp + fsync + rename).

    A crash must never leave a partially written mutable metadata file
    presented as valid state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(path, data)


# ---------------------------------------------------------------------------
# Strict status.json parsing (mirrors job_manager boundary)
# ---------------------------------------------------------------------------


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value!r}")


def _parse_status_json(text: str) -> RemoteBatchStatusV1:
    try:
        payload = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
    except (ValueError, TypeError):
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status.json is invalid JSON.")
    if not isinstance(payload, dict):
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status.json must be a JSON object.")
    try:
        return RemoteBatchStatusV1.model_validate(payload)
    except Exception:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status.json schema is invalid.")


# ---------------------------------------------------------------------------
# Request identity
# ---------------------------------------------------------------------------


def validate_request_sha256(batch: RemoteExecutionBatchV1) -> None:
    """Require ``batch.request_sha256`` to equal the independently recomputed
    canonical request hash. Never repairs or mutates the supplied batch."""
    actual = compute_request_sha256(batch)
    if actual != batch.request_sha256:
        raise PlatformError(
            "REMOTE_REQUEST_INVALID",
            "request_sha256 does not match the canonical request payload.",
        )


# ---------------------------------------------------------------------------
# Frozen request loading (strict, fail closed)
# ---------------------------------------------------------------------------


def _load_batch(job_root: Path) -> RemoteExecutionBatchV1:
    request_path = job_root / "request.json"
    if not request_path.is_file():
        raise PlatformError("REMOTE_JOB_INTERRUPTED", "frozen request.json is missing.")
    try:
        batch = parse_remote_execution_batch_json(request_path.read_bytes())
    except Exception:
        raise PlatformError("REMOTE_JOB_INTERRUPTED", "frozen request.json is malformed.")
    try:
        validate_request_sha256(batch)
    except PlatformError as exc:
        raise PlatformError(
            "REMOTE_JOB_INTERRUPTED",
            "frozen request.json has an invalid stored request hash.",
        ) from exc
    return batch


def _load_status(job_root: Path) -> RemoteBatchStatusV1:
    status_path = job_root / "status.json"
    if not status_path.is_file():
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status.json is missing.")
    return _parse_status_json(status_path.read_text(encoding="utf-8"))


def _write_status(job_root: Path, status: RemoteBatchStatusV1) -> None:
    _atomic_write_json(job_root / "status.json", status.model_dump(mode="json"))


def _initial_status(batch: RemoteExecutionBatchV1) -> RemoteBatchStatusV1:
    items = [
        RemoteItemStatusV1(item_key=item.item_key, status="queued")
        for item in batch.items
    ]
    return RemoteBatchStatusV1(batch_id=batch.batch_id, status="queued", items=items)


def _persist_spawn_failure(job_root: Path, batch_id: str) -> None:
    """Atomically mark every item interrupted after a spawn failure.

    The frozen request.json is left untouched; the interrupted job remains a
    terminal auditable object that a later retry attaches to without re-spawn.
    """
    batch = _load_batch(job_root)
    items = [
        RemoteItemStatusV1(
            item_key=item.item_key,
            status="interrupted",
            error_code="REMOTE_JOB_INTERRUPTED",
            error_message="Detached remote worker failed to start.",
        )
        for item in batch.items
    ]
    _write_status(job_root, RemoteBatchStatusV1(batch_id=batch_id, status="interrupted", items=items))


def _validate_status_membership(
    batch: RemoteExecutionBatchV1,
    status: RemoteBatchStatusV1,
) -> None:
    request_keys = [item.item_key for item in batch.items]
    status_keys = [item.item_key for item in status.items]
    if len(set(status_keys)) != len(status_keys):
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status.json contains duplicate item keys.")
    if status_keys != request_keys:
        raise PlatformError(
            "REMOTE_STATUS_UNAVAILABLE",
            "status.json item membership does not match the frozen request.",
        )


def _update_and_persist_item(
    job_root: Path,
    status: RemoteBatchStatusV1,
    item_key: str,
    *,
    value: str,
    error_code: str | None = None,
    error_message: str | None = None,
    result_relative_path: str | None = None,
) -> RemoteBatchStatusV1:
    items: list[RemoteItemStatusV1] = []
    for existing in status.items:
        if existing.item_key == item_key:
            items.append(
                RemoteItemStatusV1(
                    item_key=existing.item_key,
                    status=value,
                    error_code=error_code,
                    error_message=error_message,
                    result_relative_path=result_relative_path,
                )
            )
        else:
            items.append(existing)
    updated = RemoteBatchStatusV1(
        batch_id=status.batch_id,
        status=status.status,
        items=items,
    )
    _write_status(job_root, updated)
    return updated


def _mark_corrupted(
    job_root: Path,
    status: RemoteBatchStatusV1,
    item_key: str,
    message: str,
) -> RemoteBatchStatusV1:
    """Persist an infrastructure/result-integrity corruption as interrupted.

    Corruption is never ``failed`` (that is a scientific pipeline/data failure)
    and the result artifacts are never regenerated or overwritten.
    """
    return _update_and_persist_item(
        job_root, status, item_key,
        value="interrupted",
        error_code="REMOTE_RESULT_CORRUPTED",
        error_message=message,
    )


# ---------------------------------------------------------------------------
# Create-or-attach
# ---------------------------------------------------------------------------


def create_or_attach(batch: RemoteExecutionBatchV1, job_root: Path) -> bool:
    """Create a new job (True) or attach to an existing identical one (False).

    ``job_root`` must be the exact per-batch directory and is owned by this
    function (only ``job_root.parent`` may pre-exist).
    """
    validate_request_sha256(batch)
    if job_root.name != batch.batch_id:
        raise PlatformError(
            "REMOTE_REQUEST_INVALID",
            "job_root name must equal batch_id.",
        )
    if not job_root.exists():
        job_root.mkdir(parents=True)
        _atomic_write_json(job_root / "request.json", batch.model_dump(mode="json"))
        _write_status(job_root, _initial_status(batch))
        return True

    # Pre-existing job: strict load of the frozen snapshot; never overwrite it.
    stored = _load_batch(job_root)
    _load_status(job_root)
    if stored.request_sha256 == batch.request_sha256:
        return False
    raise PlatformError(
        "REMOTE_REQUEST_CONFLICT",
        "batch_id already exists with a different semantic request.",
    )


def submit_job(
    batch: RemoteExecutionBatchV1,
    job_root: Path,
    spawn_worker: Callable[[str], None],
) -> Literal["created", "attached"]:
    """Validate, create-or-attach, and spawn the detached worker only once."""
    _require_identifier(batch.batch_id, "batch_id", "REMOTE_REQUEST_INVALID")
    created = create_or_attach(batch, job_root)
    if not created:
        return "attached"
    try:
        spawn_worker(batch.batch_id)
    except Exception as exc:
        _persist_spawn_failure(job_root, batch.batch_id)
        raise PlatformError(
            "REMOTE_JOB_INTERRUPTED",
            "Detached remote worker failed to start.",
        ) from exc
    return "created"


# ---------------------------------------------------------------------------
# reconcile_status
# ---------------------------------------------------------------------------


def reconcile_status(batch_id: str, job_root: Path) -> RemoteBatchStatusV1:
    """Strictly load and validate the current frozen request + status."""
    _require_identifier(batch_id, "batch_id", "REMOTE_JOB_INTERRUPTED")
    if job_root.name != batch_id:
        raise PlatformError(
            "REMOTE_JOB_INTERRUPTED",
            "job_root name does not match batch_id.",
        )
    batch = _load_batch(job_root)
    status = _load_status(job_root)
    if status.batch_id != batch_id:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status batch_id does not match.")
    _validate_status_membership(batch, status)
    return status


# ---------------------------------------------------------------------------
# Terminal result verification (write-once, never regenerates)
# ---------------------------------------------------------------------------


def _verify_terminal_result(
    batch: RemoteExecutionBatchV1,
    item: RemoteExecutionItemV1,
    job_root: Path,
) -> None:
    from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json
    from app.remote_execution.source_hash import compute_file_sha256

    result_dir = job_root / "results" / item.item_key
    envelope_path = result_dir / _ENVELOPE_FILENAME
    zip_path = result_dir / _PAYLOAD_FILENAME

    if not (envelope_path.is_file() and not envelope_path.is_symlink()):
        raise PlatformError("REMOTE_RESULT_CORRUPTED", "terminal envelope.json is missing.")
    if not (zip_path.is_file() and not zip_path.is_symlink()):
        raise PlatformError("REMOTE_RESULT_CORRUPTED", "terminal analysis_result.zip is missing.")

    try:
        envelope = parse_remote_execution_envelope_json(envelope_path.read_bytes())
    except Exception:
        raise PlatformError("REMOTE_RESULT_CORRUPTED", "terminal envelope.json is invalid.")

    checks = (
        (envelope.batch_id == batch.batch_id, "batch_id"),
        (envelope.item_key == item.item_key, "item_key"),
        (envelope.request_id == item.request_id, "request_id"),
        (envelope.local_run_id == item.local_run_id, "local_run_id"),
        (envelope.recording_fingerprint == item.recording.expected_recording_fingerprint,
         "recording_fingerprint"),
        (envelope.source_data_sha256 == item.recording.expected_source_data_sha256,
         "source_data_sha256"),
        (envelope.pipeline_id == batch.pipeline.id, "pipeline_id"),
        (envelope.pipeline_version == batch.pipeline.version, "pipeline_version"),
        (envelope.orchestrator_commit == item.orchestrator_commit, "orchestrator_commit"),
        (envelope.remote_runtime_commit == batch.required_remote_runtime_commit,
         "remote_runtime_commit"),
        (envelope.asset_manifest_sha256 == batch.asset_manifest_sha256,
         "asset_manifest_sha256"),
    )
    for ok, field in checks:
        if not ok:
            raise PlatformError(
                "REMOTE_RESULT_CORRUPTED",
                f"terminal envelope identity mismatch: {field}.",
            )

    if envelope.payload_sha256 != compute_file_sha256(zip_path):
        raise PlatformError(
            "REMOTE_RESULT_CORRUPTED",
            "terminal analysis_result.zip payload hash does not match the envelope.",
        )


# ---------------------------------------------------------------------------
# Aggregate status
# ---------------------------------------------------------------------------


def _aggregate_status(batch_id: str, items: list[RemoteItemStatusV1]) -> RemoteBatchStatusV1:
    statuses = {item.status for item in items}
    if statuses == {"completed"}:
        batch_status = "completed"
    elif statuses & {"queued", "running"}:
        batch_status = "running"
    elif "failed" in statuses:
        batch_status = "failed"
    elif "interrupted" in statuses:
        batch_status = "interrupted"
    else:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "unrecognized item statuses.")
    return RemoteBatchStatusV1(batch_id=batch_id, status=batch_status, items=items)


# ---------------------------------------------------------------------------
# run_work
# ---------------------------------------------------------------------------


def run_work(
    batch_id: str,
    job_root: Path,
    item_executor: ItemExecutor,
) -> None:
    """Execute queued/running items once, verify write-once results, and
    persist an aggregated batch status."""
    _require_identifier(batch_id, "batch_id", "REMOTE_JOB_INTERRUPTED")
    if job_root.name != batch_id:
        raise PlatformError("REMOTE_JOB_INTERRUPTED", "job_root name does not match batch_id.")
    batch = _load_batch(job_root)
    status = _load_status(job_root)
    if status.batch_id != batch_id or batch.batch_id != batch_id:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "status batch_id does not match.")
    _validate_status_membership(batch, status)

    for item in batch.items:
        item_status = next(it for it in status.items if it.item_key == item.item_key)
        result_dir = job_root / "results" / item.item_key
        envelope_path = result_dir / _ENVELOPE_FILENAME
        zip_path = result_dir / _PAYLOAD_FILENAME

        if item_status.status == "completed":
            # Completed items are write-once: verify and never re-execute.
            try:
                _verify_terminal_result(batch, item, job_root)
            except PlatformError as exc:
                if exc.code != "REMOTE_RESULT_CORRUPTED":
                    raise
                status = _mark_corrupted(job_root, status, item.item_key, exc.message)
            continue
        if item_status.status in ("failed", "interrupted"):
            # Terminal for Task 8; no automatic retry policy exists.
            continue

        envelope_exists = envelope_path.is_file()
        payload_exists = zip_path.is_file()

        if envelope_exists or payload_exists:
            # Crash window: result files exist before status==completed.
            if envelope_exists and payload_exists:
                try:
                    _verify_terminal_result(batch, item, job_root)
                except PlatformError as exc:
                    if exc.code != "REMOTE_RESULT_CORRUPTED":
                        raise
                    status = _mark_corrupted(job_root, status, item.item_key, exc.message)
                    continue
                status = _update_and_persist_item(
                    job_root, status, item.item_key,
                    value="completed",
                    result_relative_path=f"{_RESULT_RELATIVE_PREFIX}/{item.item_key}",
                )
                continue
            status = _mark_corrupted(
                job_root, status, item.item_key,
                "partial terminal artifact exists; refusing to regenerate.",
            )
            continue

        # Fresh execution path.
        status = _update_and_persist_item(job_root, status, item.item_key, value="running")
        try:
            item_executor.execute(item, job_root)
        except PlatformError as exc:
            status = _update_and_persist_item(
                job_root, status, item.item_key,
                value="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
            continue
        except Exception:
            # One item bug must not kill the rest of a batch. Never expose a
            # Python traceback in the persisted status.
            status = _update_and_persist_item(
                job_root, status, item.item_key,
                value="failed",
                error_code="PIPELINE_EXECUTION_FAILED",
                error_message="Remote pipeline execution failed.",
            )
            continue

        if not (envelope_path.is_file() and zip_path.is_file()):
            status = _mark_corrupted(
                job_root, status, item.item_key,
                "item executor did not publish a complete terminal result.",
            )
            continue
        try:
            _verify_terminal_result(batch, item, job_root)
        except PlatformError as exc:
            if exc.code != "REMOTE_RESULT_CORRUPTED":
                raise
            status = _mark_corrupted(job_root, status, item.item_key, exc.message)
            continue
        status = _update_and_persist_item(
            job_root, status, item.item_key,
            value="completed",
            result_relative_path=f"{_RESULT_RELATIVE_PREFIX}/{item.item_key}",
        )

    aggregated = _aggregate_status(batch.batch_id, status.items)
    _write_status(job_root, aggregated)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_spawn_worker(job_root: Path) -> Callable[[str], None]:
    """Detach a local ``runner work`` process; survives SSH command exit."""

    def _spawn(worker_batch_id: str) -> None:
        argv = [
            sys.executable,
            "-m",
            "app.remote_execution.runner",
            "work",
            "--batch-id",
            worker_batch_id,
            "--job-root",
            str(job_root),
        ]
        stdout = (job_root / "stdout.log").open("ab")
        stderr = (job_root / "stderr.log").open("ab")
        # cwd is the backend module root (parents[2] of runner.py), so the
        # detached worker is importable without pytest/conftest or an editable
        # install.
        backend_root = Path(__file__).resolve().parents[2]
        subprocess.Popen(
            argv,
            shell=False,
            start_new_session=True,
            cwd=str(backend_root),
            stdout=stdout,
            stderr=stderr,
        )

    return _spawn


def _cli_probe(args: argparse.Namespace) -> int:
    # Task 10 owns runtime/assets verification. Fail closed until it exists.
    # The future verifier import stays LAZY and inside this handler only.
    try:
        from app.remote_execution.assets import verify_asset_manifest  # noqa: F401
        from app.remote_execution.resolver import resolve_space_net  # noqa: F401
    except ImportError:
        raise PlatformError(
            "REMOTE_PROBE_UNAVAILABLE",
            "runtime/assets verification is not available until Task 10.",
        )
    raise PlatformError(
        "REMOTE_PROBE_UNAVAILABLE",
        "runtime/assets verification is not available until Task 10.",
    )


def _cli_submit(args: argparse.Namespace) -> int:
    request_path = _require_absolute_safe_path(
        args.request_path, "request path", "REMOTE_SUBMIT_INVALID"
    )
    if not request_path.is_file():
        raise PlatformError("REMOTE_SUBMIT_INVALID", "request path must be a regular file.")
    if request_path.parent.name != "incoming":
        raise PlatformError("REMOTE_SUBMIT_INVALID", "request must live under an incoming directory.")
    try:
        batch = parse_remote_execution_batch_json(request_path.read_bytes())
    except Exception:
        raise PlatformError("REMOTE_SUBMIT_INVALID", "request file could not be parsed.")
    if request_path.name != f"{batch.batch_id}.request.json":
        raise PlatformError("REMOTE_SUBMIT_INVALID", "request filename must match batch_id.")
    remote_job_root = request_path.parent.parent
    job_root = remote_job_root / batch.batch_id
    result = submit_job(batch, job_root, _default_spawn_worker(job_root))
    print(result)
    return 0


def _cli_status(args: argparse.Namespace) -> int:
    batch_id = args.batch_id
    _require_identifier(batch_id, "batch_id", "REMOTE_STATUS_UNAVAILABLE")
    env_root = os.environ.get("WSP_REMOTE_JOB_ROOT")
    if not env_root:
        raise PlatformError("REMOTE_STATUS_UNAVAILABLE", "WSP_REMOTE_JOB_ROOT is not set.")
    root = _require_absolute_safe_path(env_root, "WSP_REMOTE_JOB_ROOT", "REMOTE_STATUS_UNAVAILABLE")
    job_root = root / batch_id
    status = reconcile_status(batch_id, job_root)
    print(status.model_dump_json())
    return 0


def _cli_work(args: argparse.Namespace) -> int:
    batch_id = args.batch_id
    job_root = _require_absolute_safe_path(args.job_root, "job root", "REMOTE_EXECUTOR_UNAVAILABLE")
    _require_identifier(batch_id, "batch_id", "REMOTE_EXECUTOR_UNAVAILABLE")
    if job_root.name != batch_id:
        raise PlatformError("REMOTE_EXECUTOR_UNAVAILABLE", "job-root name must equal batch-id.")

    # Production ItemExecutor does not exist until Tasks 9/10/12. This import
    # stays LAZY and inside this handler; runner module import stays clean.
    try:
        from app.remote_execution.assets import verify_asset_manifest  # noqa: F401
        from app.remote_execution.resolver import resolve_space_net  # noqa: F401
    except ImportError:
        raise PlatformError(
            "REMOTE_EXECUTOR_UNAVAILABLE",
            "production remote ItemExecutor is not available until Tasks 9/10/12.",
        )
    raise PlatformError(
        "REMOTE_EXECUTOR_UNAVAILABLE",
        "production remote ItemExecutor is not available until Tasks 9/10/12.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.remote_execution.runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="fail-closed runtime/assets probe (Task 10 owns it)")
    probe.set_defaults(handler=_cli_probe)

    submit = subparsers.add_parser("submit", help="submit a frozen request to the server inbox")
    submit.add_argument("--request-path", required=True)
    submit.set_defaults(handler=_cli_submit)

    status = subparsers.add_parser("status", help="print strict batch status as JSON")
    status.add_argument("--batch-id", required=True)
    status.set_defaults(handler=_cli_status)

    work = subparsers.add_parser("work", help="detached worker loop (production executor from Tasks 9/10/12)")
    work.add_argument("--batch-id", required=True)
    work.add_argument("--job-root", required=True)
    work.set_defaults(handler=_cli_work)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except PlatformError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())