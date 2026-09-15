import type { AnalysisRunCreateRequest } from "../../api/types";
import type { ExecutionEnvironmentValue } from "../execution-environment/types";

export interface BuildAnalysisRunRequestArgs {
  recordingId: string;
  pipelineId: string;
  environment: ExecutionEnvironmentValue;
  modelReleaseId?: string | null;
}

/**
 * Build the camelCase AnalysisRun create request from an explicit environment
 * value.
 *
 * - manual: exact `executor`, and NO `executionMode: "auto"`.
 * - auto: `executionMode: "auto"`, and NO `executor` (the backend resolves it).
 *
 * `parameters` is always `{}` in generic V1 (no authoritative parameter schema).
 * Auto is never converted into its currently resolved executor here.
 */
export function buildAnalysisRunRequest(args: BuildAnalysisRunRequestArgs): AnalysisRunCreateRequest {
  const base: AnalysisRunCreateRequest = {
    recordingId: args.recordingId,
    pipelineId: args.pipelineId,
    parameters: {},
  };
  if (args.modelReleaseId != null) {
    base.modelReleaseId = args.modelReleaseId;
  }
  if (args.environment.mode === "auto") {
    return { ...base, executionMode: "auto" };
  }
  if (args.environment.executor === null) {
    throw new Error("Manual execution mode requires a concrete executor.");
  }
  return { ...base, executor: args.environment.executor };
}
