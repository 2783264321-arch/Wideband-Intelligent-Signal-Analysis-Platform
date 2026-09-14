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

# Monitor failure reason codes (fail closed).
CONCURRENCY_MONITOR_FAILURE = "CONCURRENCY_MONITOR_FAILURE"
RESOURCE_MONITOR_FAILURE = "RESOURCE_MONITOR_FAILURE"


def h5_cycles(stems: Iterable[str]) -> list[list[str]]:
    """16 + 16 + 8 = 40 over the frozen 16-stem pool."""
    pool = list(stems)
    if len(pool) != 16:
        raise ValueError(f"H5 requires exactly 16 stems, got {len(pool)}")
    return [pool[0:16], pool[0:16], pool[0:8]]


def h5_expected_executions(stems: Iterable[str]) -> int:
    return sum(len(cycle) for cycle in h5_cycles(stems))


@dataclass(frozen=True)
class WorkerOwnership:
    run_id: str
    pid: int | None


@dataclass(frozen=True)
class ConcurrencySample:
    timestamp: float
    worker_pids: tuple[int, ...]
    worker_run_ids: tuple[str, ...]
    gpu_compute_pids: tuple[int, ...]
    db_running_runs: int
    db_pending_runs: int
    interval_s: float
    experiment_id: str | None = None
    ownership: tuple[WorkerOwnership, ...] = ()


@dataclass(frozen=True)
class ResourceSample:
    timestamp: float
    gpu_memory_used_mib: int
    gpu_memory_free_mib: int | None = None
    gpu_utilization_pct: int | None = None
    worker_rss_kb: dict = field(default_factory=dict)
    worker_vmhwm_kb: dict = field(default_factory=dict)
    cgroup_memory_current_bytes: int | None = None
    cgroup_clean_file_cache_bytes: int | None = None
    cgroup_committed_floor_bytes: int | None = None
    cgroup_effective_headroom_bytes: int | None = None
    cgroup_pressure_some_avg10: float | None = None
    cgroup_pressure_full_avg10: float | None = None
    # CURRENT absolute cgroup memory.events counters (deltas computed vs baseline).
    cgroup_events_max: int = 0
    cgroup_events_oom: int = 0
    cgroup_events_oom_kill: int = 0
    cgroup_oom_events: int = 0
    cgroup_oom_kill_events: int = 0
    cgroup_max_events: int = 0
    disk_free_bytes: int | None = None
    db_completed_items: int = 0
    db_failed_items: int = 0
    db_running_runs: int = 0
    db_pending_runs: int = 0
    db_attempt_count: int = 0
    db_run_count: int = 0
    wall_time_s: float | None = None


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
    resource_baseline: dict | None, disk_free_bytes: int | None, unexpected_db_escape: bool,
) -> H5AbortEvaluation:
    all_checks: dict = {}
    for sample in samples:
        evaluation = evaluate_concurrency_sample(sample)
        all_checks[f"concurrency@{sample.timestamp}"] = evaluation.checks
        if evaluation.abort:
            return H5AbortEvaluation(True, evaluation.reason, evaluation.checks)
    baseline = resource_baseline or {}
    for resource in resource_samples:
        evaluation = evaluate_resource_sample(sample=resource, baseline=baseline)
        if evaluation.abort:
            return evaluation
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
    """Pre-campaign admission (acceptance-only).

    Fail-closed order:
      1. foreign GPU compute process gate (allowed Plan-B set must be EMPTY before
         the first H5 experiment exists);
      2. GPU memory baseline measurable;
      3. A1 cache-aware memory admission actually invoked and admitted;
      4. disk >= ``min_disk_bytes``;
      5. cgroup ``memory.events`` baseline captured.

    The exact A1 admission report is returned for H5 evidence.
    """
    compute = core.compute_apps()
    admitted, reason, foreign = pre_admission_gate(compute, plan_b_pids=plan_b_pids)
    if not admitted:
        return {"admitted": False, "reason": reason, "detail": {"foreign": foreign},
                "gpu_memory_baseline_mib": None, "cgroup_events_baseline": None,
                "a1_admission": None}

    try:
        baseline_mib = core.gpu_memory_used_mib()
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"admitted": False, "reason": "GPU_BASELINE_UNAVAILABLE", "detail": {"error": str(exc)},
                "gpu_memory_baseline_mib": None, "cgroup_events_baseline": None,
                "a1_admission": None}

    # Issue 4: actually run the A1 cache-aware admission (two-path, fail closed).
    try:
        a1_result, a1_snapshot = run_a1_memory_admission()
    except SystemExit as exc:
        return {"admitted": False, "reason": "A1_MEMORY_ADMISSION_BLOCKED",
                "detail": {"a1": str(exc)}, "gpu_memory_baseline_mib": baseline_mib,
                "cgroup_events_baseline": None, "a1_admission": None}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"admitted": False, "reason": "A1_MEMORY_ADMISSION_UNAVAILABLE",
                "detail": {"error": str(exc)}, "gpu_memory_baseline_mib": baseline_mib,
                "cgroup_events_baseline": None, "a1_admission": None}

    try:
        events = read_cgroup_events_baseline()
    except Exception as exc:
        return {"admitted": False, "reason": "CGROUP_BASELINE_UNAVAILABLE", "detail": {"error": str(exc)},
                "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": None,
                "a1_admission": a1_result}
    try:
        import shutil
        free = shutil.disk_usage(str(core.common.PLAN_B_ROOT)).free
    except Exception as exc:  # pragma: no cover
        return {"admitted": False, "reason": "DISK_FREE_UNAVAILABLE", "detail": {"error": str(exc)},
                "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": events,
                "a1_admission": a1_result}
    if free < min_disk_bytes:
        return {"admitted": False, "reason": "DISK_LOW", "detail": {"free": free},
                "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": events,
                "a1_admission": a1_result}
    return {"admitted": True, "reason": None, "detail": {"free_disk_bytes": free},
            "gpu_memory_baseline_mib": baseline_mib, "cgroup_events_baseline": events,
            "a1_admission": a1_result, "a1_snapshot": a1_snapshot}


def run_a1_memory_admission():
    """Invoke the existing A1 cache-aware two-path admission (never a weaker gate)."""
    import bhq3_memory_gate as memory_gate

    return memory_gate.require_memory_admission()


def check_h5_fresh_state(db_path: Path) -> dict:
    """Exactly-once guard: refuse to run H5 over an existing qualification state.

    Returns ``{"fresh": True}`` only when the dedicated H5 DB has no execution
    authority (no experiments, attempts, or analysis runs). Never deletes or
    resumes; STOPs before any model inference.
    """
    import sqlite3

    path = Path(db_path)
    if not path.exists():
        return {"fresh": True, "reason": None, "counts": {}}
    connection = sqlite3.connect(str(path))
    try:
        counts = {}
        for table in ("dataset_experiments", "dataset_experiment_attempts", "analysis_runs"):
            row = connection.execute(f"select count(*) from {table}").fetchone()
            counts[table] = int(row[0]) if row and row[0] is not None else 0
    finally:
        connection.close()
    if any(value > 0 for value in counts.values()):
        return {"fresh": False, "reason": "H5_EXISTING_STATE", "counts": counts}
    return {"fresh": True, "reason": None, "counts": counts}


MANDATORY_CGROUP_EVENT_KEYS = ("max", "oom", "oom_kill")


class CgroupBaselineError(RuntimeError):
    """Mandatory cgroup memory.events baseline fields are missing or malformed."""


def read_cgroup_events_baseline(*, events_path: Path | None = None) -> dict:
    """Read the mandatory cgroup memory.events baseline counters (fail closed).

    Requires numeric ``max``, ``oom`` and ``oom_kill``. An empty or missing file
    is NOT accepted as a zero baseline.
    """
    path = Path(events_path) if events_path is not None else Path("/sys/fs/cgroup/memory.events")
    if not path.exists():
        raise CgroupBaselineError("cgroup memory.events is unavailable.")
    parsed: dict = {}
    try:
        raw = path.read_text()
    except OSError as exc:
        raise CgroupBaselineError("cgroup memory.events could not be read.") from exc
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            parsed[parts[0]] = int(parts[1])
    missing = [key for key in MANDATORY_CGROUP_EVENT_KEYS if key not in parsed]
    if missing:
        raise CgroupBaselineError(f"mandatory cgroup memory.events fields missing: {missing}")
    return {key: parsed[key] for key in MANDATORY_CGROUP_EVENT_KEYS}


def cgroup_event_deltas(*, baseline: dict, current: dict) -> dict:
    """Campaign deltas for the mandatory memory.events counters.

    ``current`` holds CURRENT absolute counters; each delta subtracts the SAME
    named campaign baseline counter. Historical nonzero baselines therefore
    produce delta 0, and a nonzero ``oom`` baseline can never corrupt the
    ``oom_kill`` delta.
    """
    mapping = {
        "max": ("max", "cgroup_events_max"),
        "oom": ("oom", "cgroup_events_oom"),
        "oom_kill": ("oom_kill", "cgroup_events_oom_kill"),
    }
    return {
        name: int(current.get(cur_key, 0)) - int(baseline.get(base_key, 0))
        for name, (base_key, cur_key) in mapping.items()
    }


def evaluate_resource_deltas(*, baseline: dict, current: dict) -> dict:
    """Backward-compatible helper for already-extracted delta inputs."""
    return {key: int(current.get(key, 0)) - int(baseline.get(key, 0))
            for key in ("max", "oom", "oom_kill", "high")}


def evaluate_resource_sample(*, sample: ResourceSample, baseline: dict,
                             monitor_failure: str | None = None) -> H5AbortEvaluation:
    """Abort on any positive campaign cgroup event delta or A1 memory trip."""
    if monitor_failure is not None:
        return H5AbortEvaluation(True, RESOURCE_MONITOR_FAILURE, {"failure": monitor_failure})
    deltas = cgroup_event_deltas(baseline=baseline, current=_resource_events(sample))
    checks = {
        "oom_delta_0": deltas["oom"] == 0,
        "oom_kill_delta_0": deltas["oom_kill"] == 0,
        "max_delta_0": deltas["max"] == 0,
    }
    if not all(checks.values()):
        return H5AbortEvaluation(True, "CGROUP_MEMORY_EVENT", {**checks, "deltas": deltas})
    return H5AbortEvaluation(False, None, checks)


def _resource_events(sample: ResourceSample) -> dict:
    return {
        "cgroup_events_max": sample.cgroup_events_max,
        "cgroup_events_oom": sample.cgroup_events_oom,
        "cgroup_events_oom_kill": sample.cgroup_events_oom_kill,
    }


def evaluate_cycle_acceptance(*, summary: dict, cycle: object) -> H5AbortEvaluation:
    """Per-cycle acceptance from the real DB/API read models.

    ``summary`` is the experiment read model augmented with ``_evaluation``,
    ``_items``, ``_attempt_counts``, ``_actual_runs`` and ``_actual_recording_names``
    gathered from the live DB/API. ``cycle`` supplies the intended item count and
    exact intended recording names (``cycle.stems``).
    """
    intended = cycle.expected_items
    intended_names = list(getattr(cycle, "stems", ()))
    evaluation = summary.get("_evaluation") or {}
    items = summary.get("_items") or []
    completed_items = [i for i in items if i.get("status") == "completed"]
    actual_runs = summary.get("_actual_runs") or []
    attempt_counts = summary.get("_attempt_counts") or {}
    actual_names = summary.get("_actual_recording_names") or []

    checks = {
        "status_completed": summary.get("status") == "completed",
        "expected_items_exact": summary.get("expected_items") == intended,
        "completed_items_exact": summary.get("completed_items") == intended,
        "failed_items_0": summary.get("failed_items") == 0,
        "queued_items_0": summary.get("queued_items") == 0,
        "running_items_0": summary.get("running_items") == 0,
        "item_count_exact": len(items) == intended and len(completed_items) == intended,
        "recording_names_exact": (
            bool(intended_names)
            and sorted(actual_names) == sorted(intended_names)
            and len(actual_names) == intended
        ),
        "one_attempt_per_item": all(attempt_counts.get(i.get("id")) == 1 for i in items),
        "attempt_count_exact": summary.get("attempt_count") == intended,
        "unique_runs_per_item": (
            len([r for r in actual_runs if r]) == intended
            and len(set(actual_runs)) == intended
        ),
        "evaluation_completed": evaluation.get("status") == "completed",
        "evaluated_exact": evaluation.get("evaluated_recordings") == intended,
        "missing_0": evaluation.get("missing_recordings") == 0,
        "coverage_1": evaluation.get("coverage") == 1.0,
    }
    if not all(checks.values()):
        return H5AbortEvaluation(True, "CYCLE_ACCEPTANCE_FAILED", checks)
    return H5AbortEvaluation(False, None, checks)


def evaluate_final_acceptance(*, cycle_summaries, concurrency_samples, resource_samples,
                              resource_baseline: dict | None, foreign_gpu_pids,
                              concurrency_monitor_failure=None,
                              resource_monitor_failure=None,
                              concurrency_artifact_present=False,
                              resource_artifact_present=False) -> dict:
    """Final H5 acceptance derived from actual DB-backed evidence.

    Rejects missing/empty monitoring evidence and monitor failures. Requires the
    DB-backed actual model-run total (not the membership constant) to be 40.
    """
    cycle_items = [s.get("_membership", {}).get("expected_items") for s in cycle_summaries]
    actual_runs = [s.get("_actual_run_count") for s in cycle_summaries]
    actual_attempts = [s.get("_actual_attempt_count") for s in cycle_summaries]
    actual_completed = [s.get("completed_items") for s in cycle_summaries]

    max_concurrency = max_observed_concurrency(concurrency_samples)
    gaps = [s.interval_s for s in concurrency_samples]
    max_gap = max(gaps) if gaps else None

    # Issue 7: real ownership + GPU evidence derived from actual samples.
    owned_worker_samples = sum(
        1 for s in concurrency_samples
        if any(getattr(o, "pid", None) is not None for o in getattr(s, "ownership", ()))
    )
    unique_owned_run_ids = len({
        o.run_id
        for s in concurrency_samples
        for o in getattr(s, "ownership", ())
        if getattr(o, "pid", None) is not None
    })
    gpu_owned_samples = sum(
        1 for s in concurrency_samples
        if set(getattr(s, "gpu_compute_pids", ())).issubset(set(getattr(s, "worker_pids", ())))
        and len(getattr(s, "gpu_compute_pids", ())) > 0
        and set(getattr(s, "worker_pids", ()))
    )

    event_deltas = {name: 0 for name in ("max", "oom", "oom_kill")}
    if resource_baseline is not None and resource_samples:
        event_deltas = cgroup_event_deltas(
            baseline=resource_baseline, current=_resource_events(resource_samples[-1])
        )

    checks = {
        "actual_runs_16_16_8": actual_runs == [16, 16, 8],
        "actual_attempts_16_16_8": actual_attempts == [16, 16, 8],
        "actual_completed_16_16_8": actual_completed == [16, 16, 8],
        "actual_total_runs_40": sum(r for r in actual_runs if isinstance(r, int)) == 40,
        "actual_total_attempts_40": sum(a for a in actual_attempts if isinstance(a, int)) == 40,
        "no_hidden_fourth_cycle": len(cycle_summaries) == 3,
        "cycle_partition_16_16_8": cycle_items == [16, 16, 8],
        "max_concurrency_le_2": max_concurrency <= CONCURRENCY_BOUND,
        "all_gaps_le_max": max_gap is not None and max_gap <= H5_MAX_SAMPLE_GAP_S,
        "concurrency_samples_present": len(concurrency_samples) > 0,
        "resource_samples_present": len(resource_samples) > 0,
        "concurrency_artifact_present": bool(concurrency_artifact_present),
        "resource_artifact_present": bool(resource_artifact_present),
        "concurrency_monitor_ok": concurrency_monitor_failure is None,
        "resource_monitor_ok": resource_monitor_failure is None,
        # Issue 7: real ownership + GPU evidence is mandatory.
        "ownership_evidence_present": owned_worker_samples > 0,
        "unique_owned_run_ids_seen": unique_owned_run_ids > 0,
        "gpu_owned_evidence_present": gpu_owned_samples > 0,
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
        "actual_runs": actual_runs,
        "actual_attempts": actual_attempts,
        "actual_completed_items": actual_completed,
        "actual_total_runs": sum(r for r in actual_runs if isinstance(r, int)),
        "actual_total_attempts": sum(a for a in actual_attempts if isinstance(a, int)),
        "max_observed_concurrency": max_concurrency,
        "max_sample_gap_s": max_gap,
        "owned_worker_samples": owned_worker_samples,
        "gpu_owned_samples": gpu_owned_samples,
        "unique_owned_run_ids": unique_owned_run_ids,
        "concurrency_monitor_failure": concurrency_monitor_failure,
        "resource_monitor_failure": resource_monitor_failure,
        "cgroup_event_deltas": event_deltas,
        "foreign_gpu_pids": list(foreign_gpu_pids),
    }
