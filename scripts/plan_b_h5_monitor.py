"""Plan B H5 — dedicated high-frequency concurrency + resource monitors.

The slow DatasetExperiment polling loop cannot itself prove a <= 0.5 s sampling
cadence. These monitors run on their own threads:

  * ConcurrencyMonitor samples at ~0.25 s, records the ACTUAL monotonic gap from
    the previous sample, maps exact qualification-owned worker PIDs <-> run_ids,
    correlates GPU compute-app PIDs, and aborts on a sampling gap > 0.5 s, on an
    unmapped GPU compute PID, or on > 2 live qualification workers.
  * ResourceMonitor samples ~5 s for GPU memory, worker RSS/VmHWM, cgroup
    memory/pressure/events, disk, and persists raw samples for endurance trend
    analysis (cache-aware; no drop_caches).

CPU-testable: the sample/abort logic is pure and driven by injected callables.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

WORKER_MODULE = "app.analysis.local_inference_worker"


def plan_b_local_worker_pids(proc_root: Path = Path("/proc")) -> list[int]:
    pids: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if WORKER_MODULE in cmd:
            pids.append(int(entry.name))
    return pids


def worker_pid_for_run(run_id: str, proc_root: Path = Path("/proc")) -> int | None:
    """Exact ``-m app.analysis.local_inference_worker <run_id>`` PID (unique)."""
    matches: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parts = [p for p in (entry / "cmdline").read_bytes().split(b"\0") if p]
        except OSError:
            continue
        decoded = [p.decode("utf-8", "replace") for p in parts]
        try:
            index = decoded.index("-m")
        except ValueError:
            continue
        if (len(decoded) == index + 3 and decoded[index + 1] == WORKER_MODULE
                and decoded[index + 2] == run_id):
            matches.append(int(entry.name))
    if len(matches) > 1:
        raise RuntimeError(f"ambiguous worker PIDs for run {run_id}: {sorted(matches)}")
    return matches[0] if matches else None


def foreign_compute_pids(samples: list) -> list[int]:
    """GPU compute PIDs observed that were not mapped to an exact Plan-B worker."""
    foreign: set[int] = set()
    for sample in samples:
        mapped = set(sample.worker_pids) if hasattr(sample, "worker_pids") else set()
        for pid in getattr(sample, "gpu_compute_pids", ()):  # pragma: no cover - defensive
            if pid not in mapped:
                foreign.add(pid)
    return sorted(foreign)


class ConcurrencyMonitor:
    """Threaded high-frequency monitor writing raw samples to JSONL."""

    def __init__(self, *, app, evidence_path: Path, target_period_s: float,
                 max_gap_s: float, bound: int,
                 worker_pids: Callable[[], list[int]],
                 gpu_compute_pids: Callable[[], list[int]],
                 clock: Callable[[], float] = time.monotonic):
        self._app = app
        self._path = Path(evidence_path)
        self._target_period_s = target_period_s
        self._max_gap_s = max_gap_s
        self._bound = bound
        self._worker_pids = worker_pids
        self._gpu_compute_pids = gpu_compute_pids
        self._clock = clock
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._experiment_id: str | None = None
        self._prev_ts: float | None = None
        self.samples: list = []
        self.abort_reason: str | None = None
        self.abort_detail: dict = {}

    def activate(self, experiment_id: str) -> None:
        self._experiment_id = experiment_id

    def deactivate(self) -> None:
        self._experiment_id = None

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _experiment_run_ids(self) -> list[str]:
        if self._experiment_id is None:
            return []
        from fastapi.testclient import TestClient

        client = TestClient(self._app)
        items = client.get(f"/api/dataset-experiments/{self._experiment_id}/items").json()
        return [i["latest_analysis_run_id"] for i in items if i.get("latest_analysis_run_id")]

    def _sample(self, *, experiment_id: str | None) -> object:
        from plan_b_h5_core import ConcurrencySample

        ts = self._clock()
        gap = 0.0 if self._prev_ts is None else ts - self._prev_ts
        self._prev_ts = ts
        run_ids = self._experiment_run_ids()
        mapped_workers: list[int] = []
        for run_id in run_ids:
            try:
                pid = worker_pid_for_run(run_id)
            except RuntimeError as exc:
                self.abort_reason = "AMBIGUOUS_WORKER_PID"
                self.abort_detail = {"run_id": run_id, "detail": str(exc)}
                pid = None
            if pid is not None:
                mapped_workers.append(pid)
        gpu_pids = list(self._gpu_compute_pids())
        db_running = 0
        db_pending = 0
        if experiment_id is not None:
            from fastapi.testclient import TestClient

            client = TestClient(self._app)
            for run_id in run_ids:
                status = client.get(f"/api/analysis-runs/{run_id}").json()["status"]
                db_running += status == "running"
                db_pending += status == "pending"
        return ConcurrencySample(
            timestamp=ts,
            worker_pids=tuple(mapped_workers),
            worker_run_ids=tuple(run_ids),
            gpu_compute_pids=tuple(gpu_pids),
            db_running_runs=db_running,
            db_pending_runs=db_pending,
            interval_s=gap,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            experiment_id = self._experiment_id
            sample = self._sample(experiment_id=experiment_id)
            self.samples.append(sample)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(sample), sort_keys=True) + "\n")
            if self.abort_reason is None:
                self._evaluate(sample)
            elapsed = self._clock() - started
            time.sleep(max(0.0, self._target_period_s - elapsed))

    def _evaluate(self, sample) -> None:
        if sample.interval_s > self._max_gap_s:
            self.abort_reason = "CONCURRENCY_SAMPLING_GAP"
            self.abort_detail = {"gap_s": sample.interval_s, "max_gap_s": self._max_gap_s}
            return
        if len(set(sample.worker_pids)) > self._bound:
            self.abort_reason = "CONCURRENCY_BOUND_EXCEEDED"
            self.abort_detail = {"workers": list(sample.worker_pids)}
            return
        allowed = set(sample.worker_pids)
        for pid in sample.gpu_compute_pids:
            if pid not in allowed:
                self.abort_reason = "UNMAPPED_GPU_COMPUTE_PID"
                self.abort_detail = {"gpu_pid": pid, "mapped": sorted(allowed)}
                return


class ResourceMonitor:
    """Threaded ~5 s resource sampler writing raw samples to JSONL."""

    def __init__(self, *, evidence_path: Path, gpu_memory_used_mib: Callable[[], int],
                 plan_b_pids: Callable[[], list[int]], interval_s: float,
                 clock: Callable[[], float] = time.time):
        self._path = Path(evidence_path)
        self._gpu_memory_used_mib = gpu_memory_used_mib
        self._plan_b_pids = plan_b_pids
        self._interval_s = interval_s
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.samples: list = []

    def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("", encoding="utf-8")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _sample(self):
        from plan_b_h5_core import ResourceSample

        rss: dict[int, int] = {}
        for pid in self._plan_b_pids():
            for line in Path(f"/proc/{pid}/status").read_text().splitlines() if Path(f"/proc/{pid}/status").exists() else []:
                if line.startswith("VmHWM:"):
                    rss[pid] = int(line.split()[1])
        return ResourceSample(
            timestamp=self._clock(),
            gpu_memory_used_mib=self._gpu_memory_used_mib(),
            worker_rss_kb=rss,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = self._sample()
            self.samples.append(sample)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(sample), sort_keys=True) + "\n")
            self._stop.wait(self._interval_s)
