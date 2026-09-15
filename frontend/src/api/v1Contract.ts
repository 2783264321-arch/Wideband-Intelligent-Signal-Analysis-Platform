/**
 * Compile-time contract fixture for Frontend V1 (F0.2).
 *
 * This file is NOT a runtime test and is never imported by application code. It
 * exists so that `tsc -b` (via `npm run build`) fails when the approved Frontend
 * V1 contract/domain types are missing or drift. Vitest does not typecheck, so
 * type-only RED/GREEN uses the TypeScript compiler.
 *
 * Contract invariants exercised here (see the approved design/plan Interface
 * Ledger):
 * - public/domain fields are camelCase
 * - DatasetExperiment.executor is a required concrete string
 * - DatasetExperiment.requestedExecutionMode is ExecutionMode | null
 * - DatasetExperimentAttempt.analysisRunId is a required string
 * - DatasetExperimentCreateRequest.evaluationProtocol is optional
 * - DatasetExperiment.evaluationProtocol (read model) is a required string
 */
import type {
  AnalysisRunCreateRequest,
  DatasetExperiment,
  DatasetExperimentAttempt,
  DatasetExperimentCreateRequest,
  DatasetExperimentItem,
  ExecutionCandidate,
  ExecutionMode,
  ExecutionSelectionScope,
  ExecutorSelection,
} from "./types";

const mode: ExecutionMode = "auto";

const candidate: ExecutionCandidate = {
  executor: "local_cpu",
  technical: true,
  configured: true,
  certified: true,
  available: true,
  reasonCode: null,
  reasonMessage: null,
};

const selection: ExecutorSelection = {
  requestedMode: "auto",
  resolvedExecutor: "local_cpu",
  reasonCode: "AUTO_ONLY_RUNNABLE_EXECUTOR",
  reason: "Only local_cpu is runnable.",
  workloadClass: "SMALL",
  candidates: [candidate],
};

const recordingScope: ExecutionSelectionScope = { kind: "recording", recordingId: "rec_1" };
const datasetScope: ExecutionSelectionScope = {
  kind: "dataset",
  datasetName: "spacenet",
  datasetSplit: "test",
  datasetLabelSpace: "spacenet_14",
};

const autoRequest: AnalysisRunCreateRequest = {
  recordingId: "rec_1",
  pipelineId: "dummy",
  executionMode: mode,
  modelReleaseId: null,
  parameters: {},
};

const manualRequest: AnalysisRunCreateRequest = {
  recordingId: "rec_1",
  pipelineId: "dummy",
  executor: "local_cpu",
  parameters: {},
};

const item: DatasetExperimentItem = {
  id: "item_1",
  experimentId: "exp_1",
  manifestOrder: 0,
  recordingId: "rec_1",
  recordingName: "0",
  status: "queued",
  lastErrorType: null,
  lastErrorMessage: null,
  latestAnalysisRunId: null,
  createdAt: null,
  updatedAt: null,
};

const attempt: DatasetExperimentAttempt = {
  id: "att_1",
  experimentItemId: "item_1",
  attemptNumber: 1,
  analysisRunId: "run_1",
  launchRequestedAt: null,
  createdAt: null,
};

const experiment: DatasetExperiment = {
  id: "exp_1",
  name: "Exp",
  datasetName: "spacenet",
  datasetSplit: "test",
  datasetLabelSpace: "spacenet_14",
  recordingManifestHash: "a".repeat(64),
  pluginId: "dummy",
  pluginVersion: "1.0",
  modelReleaseId: null,
  assetManifestSha256: null,
  parameters: {},
  executor: "local_cpu",
  evaluationProtocol: "physical_tf_detection_ap_v2",
  maxConcurrency: 1,
  status: "pending",
  datasetEvaluationId: null,
  errorType: null,
  errorMessage: null,
  requestedExecutionMode: null,
  autoReasonCode: null,
  autoReason: null,
  workloadClass: null,
  expectedItems: 1,
  queuedItems: 1,
  runningItems: 0,
  completedItems: 0,
  failedItems: 0,
  attemptCount: 0,
  createdAt: null,
  startedAt: null,
  completedAt: null,
};

const experimentRequest: DatasetExperimentCreateRequest = {
  name: "Exp",
  datasetName: "spacenet",
  datasetSplit: "test",
  datasetLabelSpace: "spacenet_14",
  pluginId: "dummy",
  pluginVersion: "1.0",
  executionMode: "auto",
  parameters: {},
  maxConcurrency: 1,
};

export const _v1ContractFixture = {
  mode,
  selection,
  recordingScope,
  datasetScope,
  autoRequest,
  manualRequest,
  item,
  attempt,
  experiment,
  experimentRequest,
} as const;
