"""Candidate fact collection and Auto execution resolution.

This module is orchestration only: it derives per-executor facts from the
already-supplied plugin definition and executor registry, then delegates all
ranking/selection to the pure policy module. It performs no I/O, no probing, and
no persistence of its own; live availability is obtained exclusively through
``executor_registry.availability_for(...)``. Raw provider reason text is never
carried -- only the platform-owned projection of a bounded reason code.
"""
from dataclasses import dataclass

from app.execution_selection import policy


@dataclass(frozen=True)
class ExecutionCandidate:
    executor: str
    technical: bool
    configured: bool
    certified: bool
    available: bool
    reason_code: str | None
    safe_reason_message: str | None


@dataclass(frozen=True)
class ExecutionSelection:
    requested_mode: str
    resolved_executor: str | None
    reason_code: str
    reason: str
    workload_class: str
    candidates: tuple[ExecutionCandidate, ...]


def _model_release_id(model_release):
    return None if model_release is None else model_release.release.model_release_id


def collect_candidates(*, definition, model_release, probe_recording, executor_registry):
    technical_executors = {cap.executor for cap in definition.technical_execution_capabilities}
    provider_executors = set(executor_registry.providers())
    universe = sorted(technical_executors | provider_executors)
    model_release_id = _model_release_id(model_release)

    candidates: list[ExecutionCandidate] = []
    for executor in universe:
        technical = executor in technical_executors
        configured = executor in provider_executors
        if not configured:
            # No provider: never manufacture an availability probe.
            candidates.append(
                ExecutionCandidate(executor, technical, False, False, False, None, None)
            )
            continue
        certified = (
            executor_registry.certified_capability(definition, model_release_id, executor)
            is not None
        )
        availability = executor_registry.availability_for(
            definition, model_release, probe_recording, executor
        )
        available = bool(availability.available)
        if available:
            reason_code = None
            safe_reason_message = None
        else:
            reason_code = availability.reason_code
            safe_reason_message = policy.public_reason_message(reason_code)
        candidates.append(
            ExecutionCandidate(
                executor, technical, True, certified, available, reason_code, safe_reason_message
            )
        )
    return tuple(candidates)


def resolve_auto_execution(
    *,
    definition,
    model_release,
    probe_recording,
    executor_registry,
    dataset_item_count: int | None = None,
) -> ExecutionSelection:
    candidates = collect_candidates(
        definition=definition,
        model_release=model_release,
        probe_recording=probe_recording,
        executor_registry=executor_registry,
    )
    runnable = tuple(
        candidate.executor
        for candidate in candidates
        if candidate.technical and candidate.configured and candidate.certified and candidate.available
    )
    if dataset_item_count is not None:
        workload_class = policy.classify_workload(
            duration_s=None, num_samples=None, dataset_item_count=dataset_item_count
        )
    else:
        workload_class = policy.classify_workload(
            duration_s=probe_recording.duration_s,
            num_samples=probe_recording.num_samples,
            dataset_item_count=None,
        )
    decision = policy.select_executor(
        runnable=runnable,
        workload_class=workload_class,
        recommended_execution=definition.recommended_execution,
    )
    return ExecutionSelection(
        requested_mode="auto",
        resolved_executor=decision.resolved_executor,
        reason_code=decision.reason_code,
        reason=policy.public_reason_message(decision.reason_code),
        workload_class=decision.workload_class.value,
        candidates=candidates,
    )