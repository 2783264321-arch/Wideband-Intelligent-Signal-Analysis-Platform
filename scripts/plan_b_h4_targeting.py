"""Plan B H4 — running-worker crash targeting (CPU-testable seam).

The genuine crash evidence requires killing a worker whose AnalysisRun has
ALREADY transitioned to ``running``. Waiting only for
``DatasetExperimentItem.status == running`` is insufficient: the item turns
running before the run does, so a kill in that window exercises the
launch-ambiguous path instead of a real running-worker crash.

This module owns the bounded target selection so it can be unit-tested with
deterministic fakes (no CUDA, no model inference). It never signals anything
itself; it only decides whether a valid kill target exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RunningWorkerTarget:
    experiment_id: str
    item_id: str
    run_id: str
    pid: int
    pre_kill_status: str
    verified_cmdline: bool
    gpu_compute_pid_seen: bool


def verified_worker_pid(run_id: str, *, proc_root: Path = Path("/proc")) -> int | None:
    """Return the PID whose cmdline is exactly
    ``app.analysis.local_inference_worker <run_id>`` (and nothing else varies)."""
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        parts = [p.decode("utf-8", "replace") for p in argv if p]
        try:
            index = parts.index("-m")
        except ValueError:
            continue
        if (len(parts) > index + 2
                and parts[index + 1] == "app.analysis.local_inference_worker"
                and parts[index + 2] == run_id):
            return int(entry.name)
    return None


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
    """Bounded selection of a genuine running-worker kill target.

    Returns a target ONLY when ALL hold:
      * an item is ``running`` and has a ``latest_analysis_run_id``
      * ``GET /api/analysis-runs/<run_id>`` reports ``status == "running"``
      * a worker PID is discoverable whose cmdline exactly identifies
        ``app.analysis.local_inference_worker <run_id>``

    ``gpu_compute_pid_seen`` records whether that PID was also observed in the
    GPU compute set at capture time. It is informational provenance; the
    non-negotiable assertion is ``pre_kill_status == "running"``.

    Raises ``TimeoutError`` if no such target appears before ``deadline_s`` or
    if the run completes first. It NEVER falls back to killing another process.
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
            return RunningWorkerTarget(
                experiment_id=experiment_id,
                item_id=item["id"],
                run_id=run_id,
                pid=pid,
                pre_kill_status="running",
                verified_cmdline=True,
                gpu_compute_pid_seen=pid in compute,
            )
        sleep(poll_s)
    raise TimeoutError(
        "no verified running worker target before deadline; refusing to kill "
        "any other process"
    )
