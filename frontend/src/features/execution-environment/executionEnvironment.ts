import type { ExecutionCandidate, ExecutorSelection } from "../../api/types";

/**
 * The four product options are a FIXED product vocabulary, not a capability
 * matrix. All four are always enumerated; usability comes exclusively from
 * backend facts. `executors_supported` is never consulted here.
 */
export type ExecutorOptionKey = "auto" | "local_cpu" | "local_gpu" | "remote_gpu";

export const EXECUTOR_OPTIONS: readonly ExecutorOptionKey[] = [
  "auto",
  "local_cpu",
  "local_gpu",
  "remote_gpu",
];

export const EXECUTOR_LABELS: Record<ExecutorOptionKey, string> = {
  auto: "Auto",
  local_cpu: "Local CPU",
  local_gpu: "Local GPU",
  remote_gpu: "Remote GPU",
};

export type ExecutorOptionStateKind =
  | "available"
  | "not_configured"
  | "not_certified"
  | "unsupported"
  | "temporarily_unavailable"
  | "unresolved";

export interface ExecutorOptionState {
  key: ExecutorOptionKey;
  /** Concrete executor, or null for Auto. */
  executor: string | null;
  label: string;
  enabled: boolean;
  state: ExecutorOptionStateKind;
  reasonCode: string | null;
  reasonMessage: string | null;
}

const MANUAL_EXECUTORS: readonly ("local_cpu" | "local_gpu" | "remote_gpu")[] = [
  "local_cpu",
  "local_gpu",
  "remote_gpu",
];

/** Map a single backend candidate to its exact backend-derived option state. */
export function optionStateFromCandidate(candidate: ExecutionCandidate): ExecutorOptionState {
  const key = candidate.executor as ExecutorOptionKey;
  const state: ExecutorOptionStateKind = !candidate.technical
    ? "unsupported"
    : !candidate.configured
      ? "not_configured"
      : !candidate.certified
        ? "not_certified"
        : !candidate.available
          ? "temporarily_unavailable"
          : "available";
  return {
    key: key in EXECUTOR_LABELS ? key : "local_cpu",
    executor: candidate.executor,
    label: EXECUTOR_LABELS[key in EXECUTOR_LABELS ? key : "local_cpu"],
    enabled: state === "available",
    state,
    reasonCode: candidate.reasonCode,
    reasonMessage: candidate.reasonMessage,
  };
}

function defaultManualOption(key: "local_cpu" | "local_gpu" | "remote_gpu"): ExecutorOptionState {
  return {
    key,
    executor: key,
    label: EXECUTOR_LABELS[key],
    enabled: false,
    state: "unsupported",
    reasonCode: null,
    reasonMessage: null,
  };
}

/**
 * Auto is enabled only when the backend resolved an executor. When the backend
 * reports no runnable executor, Auto is unresolved and the UI must NOT fall back.
 */
export function autoOptionState(selection: ExecutorSelection | null): ExecutorOptionState {
  if (selection === null) {
    return {
      key: "auto",
      executor: null,
      label: EXECUTOR_LABELS.auto,
      enabled: false,
      state: "unresolved",
      reasonCode: null,
      reasonMessage: null,
    };
  }
  const resolved = selection.resolvedExecutor !== null;
  return {
    key: "auto",
    executor: null,
    label: EXECUTOR_LABELS.auto,
    enabled: resolved,
    state: resolved ? "available" : "unresolved",
    reasonCode: selection.reasonCode,
    reasonMessage: selection.reason,
  };
}

/** Always returns all four options (auto first), regardless of candidates. */
export function optionsFromSelection(selection: ExecutorSelection | null): ExecutorOptionState[] {
  const byExecutor = new Map<string, ExecutionCandidate>();
  if (selection !== null) {
    for (const candidate of selection.candidates) {
      byExecutor.set(candidate.executor, candidate);
    }
  }
  const manual = MANUAL_EXECUTORS.map((executor) => {
    const candidate = byExecutor.get(executor);
    return candidate !== undefined ? optionStateFromCandidate(candidate) : defaultManualOption(executor);
  });
  return [autoOptionState(selection), ...manual];
}

/** Human label for a concrete executor string. */
export function executorLabel(executor: string | null): string | null {
  if (executor === null) return null;
  if (executor === "local_cpu" || executor === "local_gpu" || executor === "remote_gpu") {
    return EXECUTOR_LABELS[executor];
  }
  return executor;
}

/**
 * A backend selection bound to the exact scope identity (recording, pipeline) that
 * produced it. Binding prevents a stale selection from authorizing a different scope.
 */
export interface BoundExecutorSelection {
  scopeKey: string;
  value: ExecutorSelection;
}

/** Stable scope identity for a recording + pipeline pair (no delimiter ambiguity). */
export function scopeKeyFor(recordingId: string, pipelineId: string): string {
  return JSON.stringify([recordingId, pipelineId]);
}

/**
 * The only selection that may authorize/present execution environments for the
 * current scope. A selection bound to any other scope (or absent) is discarded.
 */
export function effectiveSelectionForScope(
  bound: BoundExecutorSelection | null,
  scopeKey: string,
): ExecutorSelection | null {
  return bound !== null && bound.scopeKey === scopeKey ? bound.value : null;
}
