"""C-pre-3 — Plan-C foreign-GPU admission gate (acceptance-only tooling).

This utility is deliberately acceptance-only. It is NOT a production backend
dependency and production WISA code must never import it. It performs no model
inference, no remote execution and no qualification/certificate installation.

Purpose (pre-live admission, immediately before a Plan-C C1/C2/C3 inference
gate): prove that the selected target CUDA device has no pre-existing NVIDIA
compute applications. At pre-launch any compute application is contamination.

Rule (intentionally simple; no ownership heuristic):
    target GPU has zero NVIDIA compute applications -> admitted
    one or more compute applications exist          -> blocked

The tool is strictly observational. It never terminates, notifies, resets or
otherwise alters any process or device state. A blocked device is reported with
a non-zero exit code and nothing else happens.

Telemetry is fail-closed: a missing executable, a non-zero query exit status,
malformed output or an unresolved target device all resolve to
``blocked_telemetry_unavailable``. Unknown state is never treated as clean.

Spawning is injectable for deterministic, GPU-free tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
MAX_PROCESS_NAME_LENGTH = 128
NVIDIA_SMI = "nvidia-smi"
DEFAULT_DEVICE_INDEX = 0
QUERY_TIMEOUT_S = 15

STATUS_ADMITTED = "admitted"
STATUS_BLOCKED_COMPUTE = "blocked_compute_present"
STATUS_BLOCKED_TELEMETRY = "blocked_telemetry_unavailable"

EXIT_ADMITTED = 0
EXIT_TELEMETRY = 2
EXIT_COMPUTE = 3
EXIT_OUTPUT_POLICY = 4

_GPU_FIELDS = "--query-gpu=index,uuid,name,memory.used,utilization.gpu"
_COMPUTE_FIELDS = "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory"
_FORMAT = "--format=csv,noheader,nounits"

_UUID_RE = re.compile(r"^(GPU|MIG)-[0-9a-fA-F-]+$")
_PLACEHOLDER_VALUES = {"N/A", "NOT SUPPORTED", "UNKNOWN", ""}


class TelemetryError(Exception):
    """Bounded internal marker for a fail-closed telemetry condition."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _validate_device_index(device_index: object) -> int:
    if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
        raise ValueError(f"device_index must be a non-negative integer, got {device_index!r}")
    return int(device_index)


def _target_argv(device_index: int) -> list[str]:
    return [NVIDIA_SMI, "-i", str(device_index), _GPU_FIELDS, _FORMAT]


def _compute_argv() -> list[str]:
    return [NVIDIA_SMI, _COMPUTE_FIELDS, _FORMAT]


def _default_runner(argv: list[str]):
    return subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S)


def _query_output(argv: list[str], runner) -> str:
    try:
        completed = runner(argv)
    except FileNotFoundError as exc:
        raise TelemetryError("nvidia_smi_not_found") from exc
    except Exception as exc:  # noqa: BLE001 - fail closed on any invocation problem
        raise TelemetryError("nvidia_smi_invocation_failed") from exc
    if getattr(completed, "returncode", None) != 0:
        raise TelemetryError("query_failed")
    stdout = getattr(completed, "stdout", None)
    if not isinstance(stdout, str):
        raise TelemetryError("query_output_malformed")
    return stdout


def _bounded_process_name(value: str) -> str:
    """Return only a bounded basename; never a full path or raw argument list."""
    text = (value or "").strip().replace("\\", "/")
    text = text.rsplit("/", 1)[-1].strip()
    if len(text) > MAX_PROCESS_NAME_LENGTH:
        text = text[:MAX_PROCESS_NAME_LENGTH]
    return text


def parse_gpu_row(text: str, device_index: int) -> dict:
    """Parse the single target-GPU row from nvidia-smi; fail closed otherwise."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise TelemetryError("target_gpu_not_uniquely_resolved")
    parts = [part.strip() for part in lines[0].split(",")]
    if len(parts) != 5:
        raise TelemetryError("target_row_malformed")
    index_text, uuid, name, memory_text, utilization_text = parts
    try:
        index = int(index_text)
    except ValueError as exc:
        raise TelemetryError("target_row_malformed") from exc
    if index != device_index:
        raise TelemetryError("target_device_mismatch")
    if not _UUID_RE.match(uuid):
        raise TelemetryError("gpu_uuid_malformed")
    if name.upper() in _PLACEHOLDER_VALUES:
        raise TelemetryError("target_row_malformed")
    try:
        memory_used = int(memory_text)
        utilization = int(utilization_text)
    except ValueError as exc:
        raise TelemetryError("target_row_malformed") from exc
    if memory_used < 0 or not 0 <= utilization <= 100:
        raise TelemetryError("target_row_malformed")
    return {
        "gpu_uuid": uuid,
        "gpu_name": name,
        "memory_used_mib": memory_used,
        "utilization_gpu_percent": utilization,
    }


def parse_compute_rows(text: str) -> list[dict]:
    """Parse compute-application rows; any malformed row fails closed."""
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            raise TelemetryError("compute_row_malformed")
        uuid, pid_text, name_text, memory_text = parts
        if not _UUID_RE.match(uuid):
            raise TelemetryError("compute_row_malformed")
        try:
            pid = int(pid_text)
        except ValueError as exc:
            raise TelemetryError("compute_row_malformed") from exc
        if pid <= 0:
            raise TelemetryError("compute_row_malformed")
        try:
            used_memory = int(memory_text)
        except ValueError as exc:
            raise TelemetryError("compute_row_malformed") from exc
        if used_memory < 0:
            raise TelemetryError("compute_row_malformed")
        name = _bounded_process_name(name_text)
        if not name or name.upper() in _PLACEHOLDER_VALUES:
            raise TelemetryError("compute_row_malformed")
        rows.append({
            "gpu_uuid": uuid,
            "pid": pid,
            "used_gpu_memory_mib": used_memory,
            "process_name": name,
        })
    return rows


def evaluate_admission(*, device_index: int, gpu: dict, compute_rows: list[dict]) -> dict:
    """Pure admission decision for the selected target GPU."""
    target_rows = [row for row in compute_rows if row["gpu_uuid"] == gpu["gpu_uuid"]]
    blocked = len(target_rows) >= 1
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_BLOCKED_COMPUTE if blocked else STATUS_ADMITTED,
        "device_index": device_index,
        "gpu_uuid": gpu["gpu_uuid"],
        "gpu_name": gpu["gpu_name"],
        "memory_used_mib": gpu["memory_used_mib"],
        "utilization_gpu_percent": gpu["utilization_gpu_percent"],
        "compute_process_count": len(target_rows),
        "compute_processes": target_rows,
    }


def _telemetry_result(device_index: int, reason: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_BLOCKED_TELEMETRY,
        "device_index": device_index,
        "gpu_uuid": None,
        "gpu_name": None,
        "memory_used_mib": None,
        "utilization_gpu_percent": None,
        "compute_process_count": None,
        "compute_processes": [],
        "reason": reason,
    }


def inspect_gpu_admission(*, device_index: int = DEFAULT_DEVICE_INDEX, runner=None) -> dict:
    """Inspect the selected target GPU and return a bounded admission result."""
    device_index = _validate_device_index(device_index)
    active_runner = runner if runner is not None else _default_runner
    try:
        target_stdout = _query_output(_target_argv(device_index), active_runner)
        gpu = parse_gpu_row(target_stdout, device_index)
        compute_stdout = _query_output(_compute_argv(), active_runner)
        compute_rows = parse_compute_rows(compute_stdout)
    except TelemetryError as exc:
        return _telemetry_result(device_index, exc.reason)
    return evaluate_admission(device_index=device_index, gpu=gpu, compute_rows=compute_rows)


def admission_exit_code(result: dict) -> int:
    status = result.get("status")
    if status == STATUS_ADMITTED:
        return EXIT_ADMITTED
    if status == STATUS_BLOCKED_COMPUTE:
        return EXIT_COMPUTE
    return EXIT_TELEMETRY


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None, runner=None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan-C pre-live GPU admission gate (acceptance-only, observational)."
    )
    parser.add_argument("--device-index", type=int, default=DEFAULT_DEVICE_INDEX)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.device_index < 0:
        print(json.dumps({"error": "invalid_device_index"}, separators=(",", ":")), file=sys.stderr)
        return EXIT_OUTPUT_POLICY

    result = inspect_gpu_admission(device_index=args.device_index, runner=runner)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    if args.output is not None:
        if args.output.exists():
            print(json.dumps({"error": "output_exists"}, separators=(",", ":")), file=sys.stderr)
            return EXIT_OUTPUT_POLICY
        _atomic_write_json(args.output, result)

    return admission_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
