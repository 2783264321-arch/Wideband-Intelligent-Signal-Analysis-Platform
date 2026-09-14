"""Plan B H4 — genuine GPU running-worker crash targeting (CPU-testable seam).

The genuine crash evidence requires killing a worker that is BOTH:

  * running a real ``AnalysisRun`` (``run.status == "running"``), AND
  * actively holding the GPU (its PID appears in the ``nvidia-smi`` compute-app set).

``run.status == "running"`` alone is insufficient: production
``local_inference_worker.execute_local_run`` sets ``run.status = "running"``
BEFORE plugin lookup, input compatibility, runtime/asset resolution, runtime
loading, and real model execution. Waiting only for the DB status could kill a
worker that has not started GPU work, which would not be genuine GPU-crash
evidence.

This module owns the bounded target selection so it can be unit-tested with
deterministic fakes (no CUDA, no model inference). It never signals anything
itself; it only decides whether a valid kill target exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

WORKER_MODULE = "app.analysis.local_inference_worker"


class AmbiguousWorkerError(RuntimeError):
    """More than one PID claims the exact same run_id (fail closed)."""


@dataclass(frozen=True)
class RunningWorkerTarget:
    experiment_id: str
    item_id: str
    run_id: str
    pid: int
    pre_kill_status: str
    verified_cmdline: bool
    gpu_compute_pid_seen: bool


def _argv_matches_worker(parts: list[str], run_id: str) -> bool:
    """Exact worker invocation: ``... -m <module> <run_id>`` with NO trailing args."""
    try:
        index = parts.index("-m")
    except ValueError:
        return False
    return (
        len(parts) == index + 3
        and parts[index + 1] == WORKER_MODULE
        and parts[index + 2] == run_id
    )


def verified_worker_pids(run_id: str, *, proc_root: Path = Path("/proc")) -> list[int]:
    """Return ALL PIDs whose cmdline is exactly ``-m <module> <run_id>``.

    More than one match for the same run_id is an ambiguous state; callers must
    treat it as fail-closed (never pick an arbitrary PID).
    """
    matches: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        parts = [p.decode("utf-8", "replace") for p in argv if p]
        if _argv_matches_worker(parts, run_id):
            matches.append(int(entry.name))
    return matches


def verified_worker_pid(run_id: str, *, proc_root: Path = Path("/proc")) -> int | None:
    """Return the unique exact worker PID for ``run_id``.

    Returns ``None`` when no PID matches. Raises ``AmbiguousWorkerError`` when
    more than one PID matches the same run_id (fail closed).
    """
    matches = verified_worker_pids(run_id, proc_root=proc_root)
    if len(matches) > 1:
        raise AmbiguousWorkerError(
            f"ambiguous worker PIDs for run {run_id}: {sorted(matches)}"
        )
    return matches[0] if matches else None


def wait_for_verified_running_worker(
    *,
    experiment_id: str,
    list_items: Callable[[], list[dict]],
    get_run: Callable[[str], dict],
    pid_for_run: Callable[[str], int | None],
    gpu_compute_pids: Callable[[], list[int]],
    deadline_s: int = 600,
    poll_s: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> RunningWorkerTarget:
    """Bounded selection of a genuine GPU running-worker kill target.

    Returns a target ONLY when ALL hold:
      * an item is ``running`` and has a ``latest_analysis_run_id``
      * ``GET /api/analysis-runs/<run_id>`` reports ``status == "running"``
      * a unique worker PID is discoverable whose cmdline exactly identifies
        ``-m app.analysis.local_inference_worker <run_id>``
      * that PID is CURRENTLY present in the GPU compute-app PID set
        (``gpu_compute_pid_seen is True``)

    If the run is running and the worker PID exists but the GPU compute PID is
    not yet visible, it KEEPS WAITING (never returns a non-GPU target). If the
    run completes first, or no GPU-backed target appears before ``deadline_s``,
    it raises ``TimeoutError``. It NEVER falls back to killing another process.

    An ``AmbiguousWorkerError`` from ``pid_for_run`` propagates (fail closed).
    """
    end = now() + deadline_s
    while now() < end:
        items = list_items()
        running_items = [i for i in items if i.get("status") == "running"]
        for item in running_items:
            run_id = item.get("latest_analysis_run_id")
            if not run_id:
                continue
            run = get_run(run_id)
            if run.get("status") != "running":
                continue
            pid = pid_for_run(run_id)
            if pid is None:
                continue
            compute = set(gpu_compute_pids())
            if pid not in compute:
                # Real GPU execution has not begun; keep waiting.
                continue
            return RunningWorkerTarget(
                experiment_id=experiment_id,
                item_id=item["id"],
                run_id=run_id,
                pid=pid,
                pre_kill_status="running",
                verified_cmdline=True,
                gpu_compute_pid_seen=True,
            )
        sleep(poll_s)
    raise TimeoutError(
        "no GPU-backed verified running worker target before deadline; refusing "
        "to kill any other process"
    )
