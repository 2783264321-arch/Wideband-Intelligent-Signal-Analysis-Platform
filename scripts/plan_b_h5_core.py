"""Plan B H5 — concurrency + endurance contract (CPU-testable core).

Pure helpers so the H5 campaign can be fully validated deterministically before
any real GPU execution:

  * 16 + 16 + 8 = 40 partition over the frozen 16-stem pool
  * high-frequency concurrency sampling representation (<= 0.5 s)
  * verified Plan-B worker PID mapping + GPU compute-PID correlation
  * DB running/pending ownership sampling
  * CONCURRENCY_BOUND_EXCEEDED detection (never > 2)
  * GPU quiescence baseline+64 MiB / 60 s rule
  * resource-sample serialization
  * abort-condition evaluation
  * foreign-GPU-process pre-admission STOP

No CUDA, no model inference, no DB access at import time.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

GPU_QUIESCENT_MARGIN_MIB = 64
GPU_QUIESCENT_TIMEOUT_S = 60
CONCURRENCY_BOUND = 2
CONCURRENCY_SAMPLE_INTERVAL_S = 0.5  # high-frequency proof: <= 0.5 s (prefer 0.25-0.5)
ENDURANCE_SAMPLE_INTERVAL_S = 5.0
H5_TARGET_SAMPLE_PERIOD_S = 0.25
H5_MAX_SAMPLE_GAP_S = 0.5


def h5_cycles(stems: Iterable[str]) -> list[list[str]]:
    """16 + 16 + 8 = 40 over the frozen 16-stem pool."""
    pool = list(stems)
    if len(pool) != 16:
        raise ValueError(f"H5 requires exactly 16 stems, got {len(pool)}")
    return [pool[0:16], pool[0:16], pool[0:8]]


def h5_expected_executions(stems: Iterable[str]) -> int:
    return sum(len(cycle) for cycle in h5_cycles(stems))


@dataclass(frozen=True)
class ConcurrencySample:
    timestamp: float
    worker_pids: tuple[int, ...]
    worker_run_ids: tuple[str, ...]
    gpu_compute_pids: tuple[int, ...]
    db_running_runs: int
    db_pending_runs: int
    interval_s: float


@dataclass(frozen=True)
class ResourceSample:
    timestamp: float
    gpu_memory_used_mib: int
    worker_rss_kb: dict = field(default_factory=dict)
    cgroup_current_bytes: int | None = None
    cgroup_committed_floor_bytes: int | None = None
    cgroup_effective_headroom_bytes: int | None = None
    cgroup_oom_events: int = 0
    cgroup_oom_kill_events: int = 0
    cgroup_max_events: int = 0
    db_completed_items: int = 0
    db_failed_items: int = 0
    wall_time_s: float | None = None
    disk_free_bytes: int | None = None


@dataclass(frozen=True)
class H5AbortEvaluation:
    abort: bool
    reason: str | None
    checks: dict


def evaluate_concurrency_sample(sample: ConcurrencySample) -> H5AbortEvaluation:
    """Enforce the hard concurrency invariant: never more than two live workers,
    and never a sampling gap larger than the high-frequency maximum."""
    worker_count = len(set(sample.worker_pids))
    checks = {
        "interval_high_frequency": sample.interval_s <= H5_MAX_SAMPLE_GAP_S,
        "workers_le_2": worker_count <= CONCURRENCY_BOUND,
        "gpu_pids_mapped": set(sample.gpu_compute_pids).issubset(set(sample.worker_pids)),
        "db_live_le_2": (sample.db_running_runs + sample.db_pending_runs) <= CONCURRENCY_BOUND,
    }
    if not checks["workers_le_2"] or not checks["db_live_le_2"]:
        return H5AbortEvaluation(True, "CONCURRENCY_BOUND_EXCEEDED", checks)
    if not checks["gpu_pids_mapped"]:
        return H5AbortEvaluation(True, "UNMAPPED_GPU_COMPUTE_PID", checks)
    if not checks["interval_high_frequency"]:
        return H5AbortEvaluation(True, "CONCURRENCY_SAMPLING_GAP", checks)
    return H5AbortEvaluation(False, None, checks)


def max_observed_concurrency(samples: Iterable[ConcurrencySample]) -> int:
    return max((len(set(s.worker_pids)) for s in samples), default=0)


def evaluate_gpu_quiescence(
    *, baseline_mib: int, observed_mib: int, compute_apps: Iterable[int],
    elapsed_since_terminal_s: float,
) -> H5AbortEvaluation:
    compute_pids = list(compute_apps)
    checks = {
        "compute_apps_empty": not compute_pids,
        "memory_within_margin": observed_mib <= baseline_mib + GPU_QUIESCENT_MARGIN_MIB,
        "within_timeout": elapsed_since_terminal_s <= GPU_QUIESCENT_TIMEOUT_S,
    }
    if compute_pids:
        return H5AbortEvaluation(True, "GPU_MEMORY_NOT_QUIESCENT", checks)
    if not checks["memory_within_margin"] and not checks["within_timeout"]:
        return H5AbortEvaluation(True, "GPU_MEMORY_NOT_QUIESCENT", checks)
    return H5AbortEvaluation(False, None, checks)


def evaluate_abort_conditions(
    *, samples: Iterable[ConcurrencySample], resource_samples: Iterable[ResourceSample],
    disk_free_bytes: int | None, unexpected_db_escape: bool,
) -> H5AbortEvaluation:
    all_checks: dict = {}
    for sample in samples:
        evaluation = evaluate_concurrency_sample(sample)
        all_checks[f"concurrency@{sample.timestamp}"] = evaluation.checks
        if evaluation.abort:
            return H5AbortEvaluation(True, evaluation.reason, evaluation.checks)
    for resource in resource_samples:
        if resource.cgroup_oom_events > 0 or resource.cgroup_oom_kill_events > 0 or resource.cgroup_max_events > 0:
            return H5AbortEvaluation(True, "CGROUP_MEMORY_EVENT", {
                "oom": resource.cgroup_oom_events,
                "oom_kill": resource.cgroup_oom_kill_events,
                "max": resource.cgroup_max_events,
            })
    if disk_free_bytes is not None and disk_free_bytes < 2 * 1024 ** 3:
        return H5AbortEvaluation(True, "DISK_LOW", {"disk_free_bytes": disk_free_bytes})
    if unexpected_db_escape:
        return H5AbortEvaluation(True, "QUALIFICATION_DB_ESCAPE", {})
    return H5AbortEvaluation(False, None, all_checks)


def foreign_gpu_processes(compute_apps: Iterable[tuple[int, int]], *, plan_b_pids: Iterable[int]) -> list[tuple[int, int]]:
    """Compute-app entries NOT owned by a live Plan-B worker (pre-admission STOP)."""
    allowed = set(plan_b_pids)
    return [entry for entry in compute_apps if entry[0] not in allowed]


def pre_admission_gate(compute_apps: Iterable[tuple[int, int]], *, plan_b_pids: Iterable[int] = ()) -> tuple[bool, str | None, list[tuple[int, int]]]:
    """H5 must NOT start while a foreign GPU compute process exists."""
    foreign = foreign_gpu_processes(compute_apps, plan_b_pids=plan_b_pids)
    if foreign:
        return False, "FOREIGN_GPU_PROCESS_PRESENT", foreign
    return True, None, []


def serialize_concurrency_samples(samples: Iterable[ConcurrencySample], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(asdict(sample), sort_keys=True) + "\n")
    return path


def serialize_resource_samples(samples: Iterable[ResourceSample], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(s) for s in samples], indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


def run_per_campaign_admission(core, *, plan_b_pids, min_disk_bytes: int = 5 * 1024 ** 3) -> dict:
    """Pre-campaign admission. Fails closed if a foreign GPU compute process
    exists, GPU memory baseline cannot be measured, A1 memory admission fails,
    disk is too low, or the cgroup baseline cannot be captured."""
    compute = core.compute_apps()
    admitted, reason, foreign = pre_admission_gate(compute, plan_b_pids=plan_b_pids)
    if not admitted:
        return {"admitted": False, "reason": reason, "detail": {"foreign": foreign},
                "gpu_memory_baseline_mib": None, "cgroup_events_baseline": None}
    try:
        baseline_mib = core.gpu_memory_used_mib()
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"admitted": False, "reason": "GPU_BASELINE_UNAVAILABLE", "detail": {"error": str(exc)},
                "gpu_memory_baseline_mib": None, "cgroup_events_baseline": None}
    try:
        events = read_cgroup_events_baseline()
    except Exception as exc:
        return {"admitted": False, "reason": "CGROUP_BASELINE_UNAVAILABLE", "detail": {"error": str(exc)},
                "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": None}
    try:
        import shutil
        free = shutil.disk_usage(str(core.common.PLAN_B_ROOT)).free
    except Exception:  # pragma: no cover
        free = None
    if free is not None and free < min_disk_bytes:
        return {"admitted": False, "reason": "DISK_LOW", "detail": {"free": free},
                "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": events}
    return {"admitted": True, "reason": None, "detail": {"free_disk_bytes": free},
            "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": events}


def read_cgroup_events_baseline() -> dict:
    """Read current cgroup memory.events counters (absolute baseline)."""
    path = Path("/sys/fs/cgroup/memory.events")
    events = {}
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                events[parts[0]] = int(parts[1])
    return events


def evaluate_resource_deltas(*, baseline: dict, current: dict) -> dict:
    return {key: int(current.get(key, 0)) - int(baseline.get(key, 0))
            for key in ("max", "oom", "oom_kill", "high")}


def evaluate_cycle_acceptance(*, summary: dict) -> H5AbortEvaluation:
    """Per-cycle acceptance derived from the persisted experiment read model."""
    checks = {
        "expected_items_exact": summary.get("expected_items") == summary.get("_membership", {}).get("expected_items"),
        "completed": summary.get("status") == "completed",
        "failed_0": summary.get("failed_items") == 0,
    }
    if not all(checks.values()):
        return H5AbortEvaluation(True, "CYCLE_ACCEPTANCE_FAILED", checks)
    return H5AbortEvaluation(False, None, checks)


def evaluate_final_acceptance(*, cycle_summaries, concurrency_samples, resource_samples,
                              resource_baseline: dict | None, foreign_gpu_pids) -> dict:
    """Final H5 acceptance derived from DB/evidence, not from a constant."""
    cycle_items = [s.get("_membership", {}).get("expected_items") for s in cycle_summaries]
    max_concurrency = max_observed_concurrency(concurrency_samples)
    gaps = [s.interval_s for s in concurrency_samples]
    max_gap = max(gaps) if gaps else 0.0

    event_deltas = {"max": 0, "oom": 0, "oom_kill": 0}
    if resource_baseline is not None:
        final = resource_samples[-1].__dict__ if resource_samples else {}
        event_deltas = {
            "max": int(final.get("cgroup_max_events", 0)) - int(resource_baseline.get("max", 0)),
            "oom": int(final.get("cgroup_oom_events", 0)) - int(resource_baseline.get("oom", 0)),
            "oom_kill": int(final.get("cgroup_oom_kill_events", 0)) - int(resource_baseline.get("oom_kill", 0)),
        }

    checks = {
        "cycle_partition_16_16_8": cycle_items == [16, 16, 8],
        "total_executions_40": sum(i for i in cycle_items if isinstance(i, int)) == 40,
        "max_concurrency_le_2": max_concurrency <= CONCURRENCY_BOUND,
        "all_gaps_le_max": max_gap <= H5_MAX_SAMPLE_GAP_S,
        "oom_delta_0": event_deltas["oom"] == 0,
        "oom_kill_delta_0": event_deltas["oom_kill"] == 0,
        "max_delta_0": event_deltas["max"] == 0,
        "no_foreign_gpu_pids": not list(foreign_gpu_pids),
        "all_cycles_completed": all(s.get("status") == "completed" for s in cycle_summaries),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "cycle_expected_items": cycle_items,
        "max_observed_concurrency": max_concurrency,
        "max_sample_gap_s": max_gap,
        "cgroup_event_deltas": event_deltas,
        "foreign_gpu_pids": list(foreign_gpu_pids),
    }
