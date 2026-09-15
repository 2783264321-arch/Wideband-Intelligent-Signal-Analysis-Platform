import type { MessageKey } from "../../localization/types";

/**
 * L3 semantic status model: maps RAW backend status/code identity to
 * localization message keys. Language lives in the localization resources —
 * this module contains NO English or Chinese strings.
 *
 * Unknown statuses/codes return `null` so callers fall back to the raw backend
 * identity verbatim (never an invented translation).
 */

const RUN_STATUS_KEYS: Record<string, MessageKey> = {
  pending: "status.pending",
  running: "status.running",
  completed: "status.completed",
  failed: "status.failed",
  interrupted: "status.interrupted",
};

const EXPERIMENT_STATUS_KEYS: Record<string, MessageKey> = {
  pending: "status.pending",
  running: "status.running",
  evaluating: "status.evaluating",
  completed: "status.completed",
  completed_with_failures: "status.completedWithFailures",
  failed: "status.failed",
};

const ITEM_STATUS_KEYS: Record<string, MessageKey> = {
  queued: "status.queued",
  running: "status.running",
  completed: "status.completed",
  failed: "status.failed",
};

const EVALUATION_STATUS_KEYS: Record<string, MessageKey> = {
  pending: "status.pending",
  running: "status.running",
  completed: "status.completed",
  failed: "status.failed",
  interrupted: "status.interrupted",
};

const REASON_KEYS: Record<string, MessageKey> = {
  EXECUTION_CAPABILITY_UNAVAILABLE: "reason.EXECUTION_CAPABILITY_UNAVAILABLE",
  EXECUTION_NOT_CERTIFIED: "reason.EXECUTION_NOT_CERTIFIED",
  INPUT_INCOMPATIBLE: "reason.INPUT_INCOMPATIBLE",
  RUNTIME_DESCRIPTOR_INVALID: "reason.RUNTIME_DESCRIPTOR_INVALID",
  ANALYSIS_LAUNCH_AMBIGUOUS: "reason.ANALYSIS_LAUNCH_AMBIGUOUS",
  ANALYSIS_INTERRUPTED: "reason.ANALYSIS_INTERRUPTED",
  AUTO_NO_RUNNABLE_EXECUTOR: "reason.AUTO_NO_RUNNABLE_EXECUTOR",
  DATASET_EXPERIMENT_ORCHESTRATION_FAILED: "reason.DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
  DATASET_EXPERIMENT_EVALUATION_FAILED: "reason.DATASET_EXPERIMENT_EVALUATION_FAILED",
  BENCHMARK_FAILED: "reason.BENCHMARK_FAILED",
  BENCHMARK_INTERRUPTED: "reason.BENCHMARK_INTERRUPTED",
};

export function runStatusKey(status: string): MessageKey | null {
  return RUN_STATUS_KEYS[status] ?? null;
}

export function experimentStatusKey(status: string): MessageKey | null {
  return EXPERIMENT_STATUS_KEYS[status] ?? null;
}

export function itemStatusKey(status: string): MessageKey | null {
  return ITEM_STATUS_KEYS[status] ?? null;
}

export function evaluationStatusKey(status: string): MessageKey | null {
  return EVALUATION_STATUS_KEYS[status] ?? null;
}

export function reasonKey(code: string | null | undefined): MessageKey | null {
  if (code === null || code === undefined || code === "") return null;
  return REASON_KEYS[code] ?? null;
}
