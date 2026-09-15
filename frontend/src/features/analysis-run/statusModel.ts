/**
 * Unified readable presentation for the approved bounded status/reason classes.
 *
 * The raw backend code is ALWAYS preserved (unknown codes are returned verbatim);
 * nothing here replaces backend identity or turns `ANALYSIS_LAUNCH_AMBIGUOUS`
 * into a generic failure.
 */

const RUN_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  interrupted: "Interrupted",
};

const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  evaluating: "Evaluating",
  completed: "Completed",
  completed_with_failures: "Completed with failures",
  failed: "Failed",
};

const REASON_DESCRIPTIONS: Record<string, string> = {
  EXECUTION_CAPABILITY_UNAVAILABLE: "Executor is not available for this input.",
  EXECUTION_NOT_CERTIFIED: "Executor is not certified for this release/runtime.",
  INPUT_INCOMPATIBLE: "Pipeline cannot run for this recording label space.",
  RUNTIME_DESCRIPTOR_INVALID: "The runtime environment changed.",
  ANALYSIS_LAUNCH_AMBIGUOUS: "The run was interrupted with an ambiguous launch state.",
  ANALYSIS_INTERRUPTED: "The run was interrupted.",
  AUTO_NO_RUNNABLE_EXECUTOR: "No execution environment is runnable for this request.",
  DATASET_EXPERIMENT_ORCHESTRATION_FAILED: "Dataset experiment orchestration failed.",
  DATASET_EXPERIMENT_EVALUATION_FAILED: "Evaluation failed.",
  BENCHMARK_FAILED: "Evaluation failed.",
  BENCHMARK_INTERRUPTED: "Evaluation was interrupted.",
};

export function describeRunStatus(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status;
}

export function describeExperimentStatus(status: string): string {
  return EXPERIMENT_STATUS_LABELS[status] ?? status;
}

/** Readable description of a bounded code; unknown codes are preserved verbatim. */
export function describeReasonCode(code: string | null | undefined): string | null {
  if (code === null || code === undefined || code === "") return null;
  return REASON_DESCRIPTIONS[code] ?? code;
}
