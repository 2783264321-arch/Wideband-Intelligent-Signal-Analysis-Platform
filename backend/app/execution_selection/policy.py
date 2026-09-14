"""Pure, portable Auto execution-selection policy.

This module owns only deterministic, explainable selection logic: the policy
constants, workload classification, executor ranking, and a platform-owned
public reason projection. It performs no I/O, no probing, and no persistence,
and it depends on no deployment-specific telemetry. Candidate availability
facts are supplied by callers (the resolver layer), never computed here.
"""
from dataclasses import dataclass
from enum import Enum

# Single-sourced policy thresholds (no duplicated magic values elsewhere).
GPU_PREFER_DURATION_S = 0.05
GPU_PREFER_SAMPLES = 3_000_000
GPU_BATCH_ITEMS = 8


class WorkloadClass(str, Enum):
    SMALL = "SMALL"
    GPU_BENEFICIAL = "GPU_BENEFICIAL"
    UNKNOWN = "UNKNOWN"


# Closed Auto reason-code set (V1). Do not add codes without a design change.
AUTO_NO_RUNNABLE_EXECUTOR = "AUTO_NO_RUNNABLE_EXECUTOR"
AUTO_ONLY_RUNNABLE_EXECUTOR = "AUTO_ONLY_RUNNABLE_EXECUTOR"
AUTO_LOCAL_CPU_PREFERRED = "AUTO_LOCAL_CPU_PREFERRED"
AUTO_LOCAL_GPU_PREFERRED = "AUTO_LOCAL_GPU_PREFERRED"
AUTO_REMOTE_GPU_PREFERRED = "AUTO_REMOTE_GPU_PREFERRED"
AUTO_UNKNOWN_RECOMMENDED_EXECUTOR = "AUTO_UNKNOWN_RECOMMENDED_EXECUTOR"
AUTO_UNKNOWN_DETERMINISTIC_RANK = "AUTO_UNKNOWN_DETERMINISTIC_RANK"

_SMALL_RANKING = ("local_cpu", "local_gpu", "remote_gpu")
_GPU_BENEFICIAL_RANKING = ("local_gpu", "remote_gpu", "local_cpu")
_UNKNOWN_FALLBACK_RANKING = ("local_gpu", "local_cpu", "remote_gpu")

_PREFERRED_REASON = {
    "local_cpu": AUTO_LOCAL_CPU_PREFERRED,
    "local_gpu": AUTO_LOCAL_GPU_PREFERRED,
    "remote_gpu": AUTO_REMOTE_GPU_PREFERRED,
}

_GENERIC_UNAVAILABLE_MESSAGE = "Executor is currently unavailable."

_PUBLIC_REASON_MESSAGES = {
    AUTO_NO_RUNNABLE_EXECUTOR: "No executor is currently runnable for this request.",
    AUTO_ONLY_RUNNABLE_EXECUTOR: "The only runnable executor was selected.",
    AUTO_LOCAL_CPU_PREFERRED: "Local CPU was selected.",
    AUTO_LOCAL_GPU_PREFERRED: "Local GPU was selected.",
    AUTO_REMOTE_GPU_PREFERRED: "Remote GPU was selected.",
    AUTO_UNKNOWN_RECOMMENDED_EXECUTOR: "The recommended executor was selected.",
    AUTO_UNKNOWN_DETERMINISTIC_RANK: "The deterministic executor ranking was applied.",
    "EXECUTION_CAPABILITY_UNAVAILABLE": _GENERIC_UNAVAILABLE_MESSAGE,
    "EXECUTION_NOT_CERTIFIED": "Executor is not certified for this release and runtime.",
    "INPUT_INCOMPATIBLE": "Executor cannot run this input.",
    "REMOTE_EXECUTOR_UNAVAILABLE": "Remote executor is unavailable.",
}


@dataclass(frozen=True)
class AutoDecision:
    resolved_executor: str | None
    reason_code: str
    workload_class: WorkloadClass


def classify_workload(
    *,
    duration_s: float | None,
    num_samples: int | None,
    dataset_item_count: int | None,
) -> WorkloadClass:
    if duration_s is None and num_samples is None and dataset_item_count is None:
        return WorkloadClass.UNKNOWN
    if duration_s is not None and duration_s > GPU_PREFER_DURATION_S:
        return WorkloadClass.GPU_BENEFICIAL
    if num_samples is not None and num_samples > GPU_PREFER_SAMPLES:
        return WorkloadClass.GPU_BENEFICIAL
    if dataset_item_count is not None and dataset_item_count >= GPU_BATCH_ITEMS:
        return WorkloadClass.GPU_BENEFICIAL
    return WorkloadClass.SMALL


def rank_for(workload_class: WorkloadClass) -> tuple[str, ...]:
    if workload_class is WorkloadClass.SMALL:
        return _SMALL_RANKING
    if workload_class is WorkloadClass.GPU_BENEFICIAL:
        return _GPU_BENEFICIAL_RANKING
    return _UNKNOWN_FALLBACK_RANKING


def select_executor(
    *,
    runnable: tuple[str, ...],
    workload_class: WorkloadClass,
    recommended_execution: str | None,
) -> AutoDecision:
    candidates = tuple(runnable)
    if not candidates:
        return AutoDecision(None, AUTO_NO_RUNNABLE_EXECUTOR, workload_class)
    if len(candidates) == 1:
        return AutoDecision(candidates[0], AUTO_ONLY_RUNNABLE_EXECUTOR, workload_class)
    if workload_class is WorkloadClass.UNKNOWN:
        if recommended_execution is not None and recommended_execution in candidates:
            return AutoDecision(
                recommended_execution, AUTO_UNKNOWN_RECOMMENDED_EXECUTOR, workload_class
            )
        chosen = next(name for name in _UNKNOWN_FALLBACK_RANKING if name in candidates)
        return AutoDecision(chosen, AUTO_UNKNOWN_DETERMINISTIC_RANK, workload_class)
    ranking = _SMALL_RANKING if workload_class is WorkloadClass.SMALL else _GPU_BENEFICIAL_RANKING
    chosen = next(name for name in ranking if name in candidates)
    return AutoDecision(chosen, _PREFERRED_REASON[chosen], workload_class)


def public_reason_message(reason_code: str | None) -> str:
    """Platform-owned, bounded, path/secret-free human text for a reason code.

    Callers must invoke this ONLY for unavailable/decision reason codes. An
    available candidate's message is ``None`` by caller contract; this helper
    never asserts that an available executor is unavailable.
    """
    if reason_code is None:
        return _GENERIC_UNAVAILABLE_MESSAGE
    return _PUBLIC_REASON_MESSAGES.get(reason_code, _GENERIC_UNAVAILABLE_MESSAGE)