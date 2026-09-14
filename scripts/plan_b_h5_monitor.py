"""Plan B H5 — dedicated high-frequency concurrency + resource monitors.

Both monitors run on their own threads and FAIL CLOSED: any unexpected sampler or
thread exception is recorded as ``thread_failure`` and surfaced through
``abort_reason`` so the live campaign stops instead of silently losing
monitoring. The slow DatasetExperiment polling loop cannot itself prove a
<= 0.5 s sampling cadence.

  * ConcurrencyMonitor samples at ~0.25 s, records the ACTUAL monotonic gap from
    the previous sample, persists exact ``(run_id, pid)`` ownership pairs for the
    CURRENT active H5 experiment only, correlates GPU compute-app PIDs, and
    aborts on a sampling gap > 0.5 s, an unmapped GPU compute PID, or > 2
    qualification workers.
  * ResourceMonitor samples ~5 s for GPU memory/utilization, exact current H5
    worker RSS/VmHWM, cgroup memory/pressure/events (absolute counters), disk,
    and DB counts — cache-aware, no drop_caches.

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
    """Broad discovery of local inference worker PIDs (diagnostics only).

    NEVER used as an H5 qualification-owned allowlist: H5 ownership is exact
    ``(run_id -> pid)`` mapping for the current experiment.
    """
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


def map_ownership_pairs(run_ids: list[str], *, proc_root: Path = Path("/proc")):
    """Return exact WorkerOwnership pairs for the given run_ids.

    Raises RuntimeError on ambiguous PIDs (fail closed). PIDs that do not exist
    are represented with ``pid=None`` (not yet spawned or already exited).
    """
    from plan_b_h5_core import WorkerOwnership

    return [
        WorkerOwnership(run_id=run_id, pid=worker_pid_for_run(run_id, proc_root=proc_root))
        for run_id in run_ids
    ]


def foreign_compute_pids(samples: list) -> list[int]:
    """GPU compute PIDs observed that were not mapped to an exact Plan-B worker."""
    foreign: set[int] = set()
    for sample in samples:
        mapped = set(sample.worker_pids) if hasattr(sample, "worker_pids") else set()
        for pid in getattr(sample, "gpu_compute_pids", ()):
            if pid not in mapped:
                foreign.add(pid)
    return sorted(foreign)


class _MonitorBase:
    """Shared fail-closed thread machinery."""

    failure_code = "MONITOR_FAILURE"

    def __init__(self, *, evidence_path: Path):
        self.path = Path(evidence_path)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.samples: list = []
        self.abort_reason: str | None = None
        self.abort_detail: dict = {}
        self.thread_failure: str | None = None
        self.last_sample_at: float | None = None
        self.sample_count = 0

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._thread = threading.Thread(target=self._run_guarded, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _record_failure(self, exc: BaseException) -> None:
        self.thread_failure = f"{type(exc).__name__}: {exc}"
        if self.abort_reason is None:
            self.abort_reason = self.failure_code
            self.abort_detail = {"thread_failure": self.thread_failure}

    def _run_guarded(self) -> None:
        try:
            self._run()
        except BaseException as exc:  # noqa: BLE001 - fail closed on ANY thread error
            self._record_failure(exc)

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _append(self, payload: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


class ConcurrencyMonitor(_MonitorBase):
    failure_code = "CONCURRENCY_MONITOR_FAILURE"

    def __init__(self, *, app, evidence_path: Path, target_period_s: float,
                 max_gap_s: float, bound: int,
                 run_ids_for_experiment: Callable[[str], list[str]],
                 gpu_compute_pids: Callable[[], list[int]],
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(evidence_path=evidence_path)
        self._app = app
        self._target_period_s = target_period_s
        self._max_gap_s = max_gap_s
        self._bound = bound
        self._run_ids_for_experiment = run_ids_for_experiment
        self._gpu_compute_pids = gpu_compute_pids
        self._clock = clock
        self._experiment_id: str | None = None
        self._prev_ts: float | None = None

    def activate(self, experiment_id: str) -> None:
        self._experiment_id = experiment_id

    def deactivate(self) -> None:
        self._experiment_id = None

    def _sample(self, *, experiment_id: str | None) -> object:
        from plan_b_h5_core import ConcurrencySample

        ts = self._clock()
        gap = 0.0 if self._prev_ts is None else ts - self._prev_ts
        self._prev_ts = ts
        run_ids = list(self._run_ids_for_experiment(experiment_id)) if experiment_id else []
        ownership = map_ownership_pairs(run_ids)
        mapped_workers = tuple(o.pid for o in ownership if o.pid is not None)
        gpu_pids = tuple(self._gpu_compute_pids())
        db_running = 0
        db_pending = 0
        if experiment_id is not None:
            db_running, db_pending = self._db_run_counts(experiment_id, run_ids)
        return ConcurrencySample(
            timestamp=ts,
            worker_pids=mapped_workers,
            worker_run_ids=tuple(run_ids),
            gpu_compute_pids=gpu_pids,
            db_running_runs=db_running,
            db_pending_runs=db_pending,
            interval_s=gap,
            experiment_id=experiment_id,
            ownership=tuple(ownership),
        )

    def _db_run_counts(self, experiment_id: str, run_ids: list[str]) -> tuple[int, int]:
        from fastapi.testclient import TestClient

        client = TestClient(self._app)
        running = 0
        pending = 0
        for run_id in run_ids:
            status = client.get(f"/api/analysis-runs/{run_id}").json()["status"]
            running += status == "running"
            pending += status == "pending"
        return running, pending

    def _run(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            sample = self._sample(experiment_id=self._experiment_id)
            self.samples.append(sample)
            self.last_sample_at = sample.timestamp
            self.sample_count = len(self.samples)
            self._append(asdict(sample))
            if self.abort_reason is None:
                self._evaluate(sample)
            elapsed = self._clock() - started
            time.sleep(max(0.0, self._target_period_s - elapsed))

    def _evaluate(self, sample) -> None:
        from plan_b_h5_core import evaluate_concurrency_sample

        evaluation = evaluate_concurrency_sample(sample)
        if evaluation.abort:
            self.abort_reason = evaluation.reason
            self.abort_detail = evaluation.checks


class ResourceMonitor(_MonitorBase):
    failure_code = "RESOURCE_MONITOR_FAILURE"

    def __init__(self, *, evidence_path: Path, interval_s: float,
                 gpu_metrics: Callable[[], dict],
                 exact_worker_pids: Callable[[], list[int]],
                 cgroup_reader: Callable[[], dict] | None = None,
                 disk_free: Callable[[], int | None] | None = None,
                 db_counts: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.time):
        super().__init__(evidence_path=evidence_path)
        self._interval_s = interval_s
        self._gpu_metrics = gpu_metrics
        self._exact_worker_pids = exact_worker_pids
        self._cgroup_reader = cgroup_reader or read_cgroup_sample_fields
        self._disk_free = disk_free or disk_free_bytes
        self._db_counts = db_counts or (lambda: {})
        self._clock = clock

    def _sample(self):
        from plan_b_h5_core import ResourceSample

        gpu = self._gpu_metrics()
        rss: dict[int, int] = {}
        vmhwm: dict[int, int] = {}
        for pid in self._exact_worker_pids():
            status = Path(f"/proc/{pid}/status")
            if not status.exists():
                continue
            for line in status.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss[pid] = int(line.split()[1])
                elif line.startswith("VmHWM:"):
                    vmhwm[pid] = int(line.split()[1])
        cgroup = self._cgroup_reader()
        db = self._db_counts()
        return ResourceSample(
            timestamp=self._clock(),
            gpu_memory_used_mib=int(gpu.get("used_mib", 0)),
            gpu_memory_free_mib=gpu.get("free_mib"),
            gpu_utilization_pct=gpu.get("utilization_pct"),
            worker_rss_kb=rss,
            worker_vmhwm_kb=vmhwm,
            cgroup_memory_current_bytes=cgroup.get("memory_current"),
            cgroup_clean_file_cache_bytes=cgroup.get("clean_file_cache"),
            cgroup_committed_floor_bytes=cgroup.get("committed_floor"),
            cgroup_effective_headroom_bytes=cgroup.get("effective_headroom"),
            cgroup_pressure_some_avg10=cgroup.get("psi_some_avg10"),
            cgroup_pressure_full_avg10=cgroup.get("psi_full_avg10"),
            cgroup_events_max=cgroup.get("events_max", 0),
            cgroup_events_oom=cgroup.get("events_oom", 0),
            cgroup_events_oom_kill=cgroup.get("events_oom_kill", 0),
            cgroup_oom_events=cgroup.get("events_oom", 0),
            cgroup_oom_kill_events=cgroup.get("events_oom_kill", 0),
            cgroup_max_events=cgroup.get("events_max", 0),
            disk_free_bytes=self._disk_free(),
            db_completed_items=db.get("completed_items", 0),
            db_failed_items=db.get("failed_items", 0),
            db_running_runs=db.get("running_runs", 0),
            db_pending_runs=db.get("pending_runs", 0),
            db_attempt_count=db.get("attempt_count", 0),
            db_run_count=db.get("run_count", 0),
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = self._sample()
            self.samples.append(sample)
            self.last_sample_at = sample.timestamp
            self.sample_count = len(self.samples)
            self._append(asdict(sample))
            self._stop.wait(self._interval_s)


def read_cgroup_sample_fields() -> dict:
    """Current cgroup absolute values reused from the A1 cache-aware derivation."""
    import bhq3_memory_gate as memory_gate

    snapshot = memory_gate.read_snapshot()
    try:
        derived = memory_gate.derive(snapshot)
    except Exception:  # pragma: no cover - malformed snapshot
        derived = {}
    events = snapshot.get("events") or {}
    pressure = snapshot.get("pressure") or {}
    return {
        "memory_current": snapshot.get("memory_current"),
        "clean_file_cache": derived.get("clean_file_cache"),
        "committed_floor": derived.get("committed_floor"),
        "effective_headroom": derived.get("effective_headroom"),
        "psi_some_avg10": pressure.get("some_avg10"),
        "psi_full_avg10": pressure.get("full_avg10"),
        "events_max": int(events.get("max", 0) or 0),
        "events_oom": int(events.get("oom", 0) or 0),
        "events_oom_kill": int(events.get("oom_kill", 0) or 0),
    }


def disk_free_bytes(path: str = "/root/autodl-tmp") -> int | None:
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
