import type { DatasetExperimentCreateRequest } from "../../api/types";
import type { ExecutionEnvironmentValue } from "../execution-environment/types";

export interface ExperimentFormValue {
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  pluginId: string;
  pluginVersion: string;
  environment: ExecutionEnvironmentValue;
  maxConcurrency: number;
}

/**
 * Build the camelCase DatasetExperiment create request.
 *
 * Generic V1 omits `evaluationProtocol` (the backend applies its authoritative
 * default) and always sends `parameters: {}`. Auto stays auto (no executor);
 * manual carries the exact executor.
 */
export function toCreateRequest(value: ExperimentFormValue): DatasetExperimentCreateRequest {
  const base: DatasetExperimentCreateRequest = {
    name: value.name,
    datasetName: value.datasetName,
    datasetSplit: value.datasetSplit,
    datasetLabelSpace: value.datasetLabelSpace,
    pluginId: value.pluginId,
    pluginVersion: value.pluginVersion,
    executionMode: "manual",
    parameters: {},
    maxConcurrency: value.maxConcurrency,
  };
  if (value.environment.mode === "auto") {
    return { ...base, executionMode: "auto" };
  }
  if (value.environment.executor === null) {
    throw new Error("Manual execution mode requires a concrete executor.");
  }
  return { ...base, executionMode: "manual", executor: value.environment.executor };
}
