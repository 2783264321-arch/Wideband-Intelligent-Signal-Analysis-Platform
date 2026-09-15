export interface DetectionResult {
  id: string;
  runId: string;
  recordingId: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  classId: number;
  className: string;
  confidence: number;
  scores?: Record<string, number> | null;
}

export interface GroundTruthResult {
  id: string;
  recordingId: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  classId: number;
  className: string;
}

export interface SpectrogramMeta {
  imageUrl: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  representation: "stft" | "ls-stft";
}

export interface RecordingSummary {
  id: string;
  name: string;
  datasetName: string | null;
  sampleRateHz: number;
  centerFrequencyHz: number;
  durationS: number;
  hasGroundTruth: boolean;
}

export interface RecordingDetail extends RecordingSummary {
  dataFormat: string;
  source: string;
  externalPath: string | null;
  frequencyLowHz: number;
  frequencyHighHz: number;
  numSamples: number;
  datasetSplit: string | null;
  labelSpace: string | null;
}

export interface WaveformData {
  timeS: number[];
  i: number[];
  q: number[];
}

export interface FFTData {
  frequencyHz: number[];
  magnitudeDb: number[];
}

export interface PipelineDefinition {
  id: string;
  name: string;
  version: string;
  labelSpace: string;
  recommendedDevice: string;
  cpuSupported: boolean;
  executorsSupported: string[];
  recommendedExecutor: string | null;
  stages: string[];
  inspectableStages: string[];
  taskCapability: string;
  pluginApiVersion?: number;
  outputLabelSpace?: string;
  inputCompatibility?: string[];
  datasetAdapters?: string[];
  modelReleaseRequired?: boolean;
  technicalExecutionCapabilities?: {
    executor: string;
    deviceType: string;
    precision: string;
  }[];
  recommendedExecution?: string | null;
}

export type AnalysisRunStatus = "pending" | "running" | "completed" | "failed" | "interrupted";

export interface AnalysisRun {
  id: string;
  recordingId: string;
  pipelineId: string;
  pipelineVersion: string;
  executor: string;
  status: AnalysisRunStatus;
  parameters: Record<string, unknown>;
  hardwareInfo?: Record<string, unknown> | null;
  executionMetadata?: Record<string, unknown> | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  errorType?: string | null;
  errorMessage?: string | null;
  workerPid?: number | null;
  createdAt?: string | null;
}

export interface ExecutorAvailability {
  executor: string;
  available: boolean;
  reasonCode: string | null;
  reasonMessage: string | null;
  remoteProfile: string | null;
  recommended: boolean;
}

export interface DetectionMetrics {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
  meanMatchedIou: number | null;
}

export interface PhysicalBox {
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
}

export interface RunMatchState {
  matched: boolean;
  detectionId: string | null;
  iou: number | null;
  classId: number | null;
  className: string | null;
  confidence: number | null;
  classCorrect: boolean | null;
  bbox: PhysicalBox | null;
}

export type ComparisonState = "both_detected" | "a_only" | "b_only" | "both_missed";

export interface AlgorithmLabCase {
  groundTruthId: string;
  classId: number;
  className: string;
  bbox: PhysicalBox;
  comparison: ComparisonState;
  runA: RunMatchState;
  runB: RunMatchState;
}

export interface ClassificationConfusion {
  gtClassId: number;
  gtClassName: string;
  predClassId: number;
  predClassName: string;
  count: number;
}

export interface ClassificationMetrics {
  matchedCount: number;
  classCorrect: number;
  classWrong: number;
  matchedAccuracy: number | null;
  confusions: ClassificationConfusion[];
}

export interface ClassAwareMetrics {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

export interface RunComparison {
  runId: string;
  pipelineId: string;
  pipelineName: string;
  metrics: DetectionMetrics;
  classificationApplicable: boolean;
  classificationReason: string | null;
  classification: ClassificationMetrics | null;
  classAware: ClassAwareMetrics | null;
}

export interface AlgorithmLabCompareResponse {
  recordingId: string;
  iouThreshold: number;
  runA: RunComparison;
  runB: RunComparison;
  cases: AlgorithmLabCase[];
}

export type DatasetEvaluationStatus = "pending" | "running" | "completed" | "failed" | "interrupted";

export interface GroundTruthProvenance {
  rawCount: number;
  canonicalCount: number;
  duplicatesRemoved: number;
  duplicatePolicy: string;
}

export interface OperatingMetrics {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

export interface DatasetBenchmarkAggregateMetrics {
  groundTruth?: GroundTruthProvenance;
  classificationApplicable: boolean;
  classificationReason: string | null;
  localization: {
    ap50: number | null;
    ap50_95: number | null;
    operating: OperatingMetrics;
  };
  classificationOnMatched: {
    matchedCount: number;
    classCorrect: number;
    classWrong: number;
    matchedAccuracy: number | null;
  } | null;
  classAware: {
    map50: number | null;
    map50_95: number | null;
    operating: OperatingMetrics;
  } | null;
}

export interface DatasetBenchmarkPerClassMetric {
  classId: number;
  className: string;
  gtCount: number;
  predictionCount: number;
  ap50: number | null;
  ap50_95: number | null;
  operating: OperatingMetrics;
}

export interface DatasetBenchmarkConfusion {
  gtClassId: number;
  gtClassName: string;
  predClassId: number;
  predClassName: string;
  count: number;
}

export interface DatasetEvaluation {
  id: string;
  name: string;
  datasetName: string;
  datasetSplit: string;
  labelSpace: string;
  pipelineId: string;
  pipelineVersion: string;
  status: DatasetEvaluationStatus;
  expectedRecordings: number;
  evaluatedRecordings: number;
  missingRecordings: number;
  coverage: number;
  comparable: boolean;
  recordingManifestHash: string;
  evaluationProtocol: string;
  protocolConfig: Record<string, unknown>;
  aggregateMetrics: DatasetBenchmarkAggregateMetrics | null;
  perClassMetrics: DatasetBenchmarkPerClassMetric[] | null;
  confusion: DatasetBenchmarkConfusion[] | null;
  progressStage: string | null;
  progressCurrent: number | null;
  progressTotal: number | null;
  errorType: string | null;
  errorMessage: string | null;
  createdAt: string | null;
  completedAt: string | null;
}

export interface DatasetEvaluationItem {
  id: string;
  evaluationId: string;
  manifestOrder: number;
  recordingId: string;
  recordingName: string;
  analysisRunId: string | null;
  status: string;
  gtCount: number;
  predictionCount: number;
  errorReason: string | null;
}

export interface ImportedBenchmarkBatch {
  importFingerprint: string;
  pipelineId: string | null;
  pipelineVersion: string | null;
  datasetName: string | null;
  datasetSplit: string | null;
  labelSpace: string | null;
  runCount: number;
  detectionCount: number;
  archiveSha256: string | null;
  resultProvenance: Record<string, unknown>;
  transportProvenance: Record<string, unknown>;
  ready: boolean;
  inconsistencyReasons: string[];
}

export interface ImportedBatchResolution {
  importFingerprint: string;
  datasetName: string;
  datasetSplit: string;
  labelSpace: string;
  pipelineId: string;
  pipelineVersion: string;
  recordingManifestHash: string;
  expectedRecordings: number;
  resolvedRecordings: number;
  missingRecordings: number;
  conflictCount: number;
  entries: Array<{
    manifestOrder: number;
    recordingId: string;
    recordingName: string;
    analysisRunId: string;
    itemKey: string;
  }>;
}

export interface DatasetBenchmarkCompareResult {
  comparable: boolean;
  reasons: string[];
  evaluationAId: string;
  evaluationBId: string;
  aggregateA: DatasetBenchmarkAggregateMetrics | null;
  aggregateB: DatasetBenchmarkAggregateMetrics | null;
  deltas: Record<string, number | null>;
}

// ---------------------------------------------------------------------------
// Frontend V1 contracts (F0.2)
//
// Domain types are camelCase; the snake_case wire shapes live privately inside
// api/client.ts. These were added per the approved Frontend V1 design/plan
// Interface Ledger and are compile-verified by src/api/v1Contract.ts.
// ---------------------------------------------------------------------------

export type ExecutionMode = "manual" | "auto";

export interface ExecutionCandidate {
  executor: string; // "local_cpu" | "local_gpu" | "remote_gpu"
  technical: boolean;
  configured: boolean;
  certified: boolean;
  available: boolean;
  reasonCode: string | null;
  reasonMessage: string | null;
}

export interface ExecutorSelection {
  requestedMode: ExecutionMode;
  resolvedExecutor: string | null;
  reasonCode: string;
  reason: string;
  workloadClass: string; // "SMALL" | "GPU_BENEFICIAL" | "UNKNOWN"
  candidates: ExecutionCandidate[];
}

export type ExecutionSelectionScope =
  | { kind: "recording"; recordingId: string }
  | { kind: "dataset"; datasetName: string; datasetSplit: string; datasetLabelSpace: string };

export interface AnalysisRunCreateRequest {
  recordingId: string;
  pipelineId: string;
  executor?: string;
  executionMode?: ExecutionMode;
  modelReleaseId?: string | null;
  parameters: Record<string, unknown>;
}

export interface DatasetExperimentItem {
  id: string;
  experimentId: string;
  manifestOrder: number;
  recordingId: string;
  recordingName: string;
  status: string; // "queued" | "running" | "completed" | "failed"
  lastErrorType: string | null;
  lastErrorMessage: string | null;
  latestAnalysisRunId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface DatasetExperimentAttempt {
  id: string;
  experimentItemId: string;
  attemptNumber: number;
  analysisRunId: string; // mandatory in the backend Attempt read model
  launchRequestedAt: string | null;
  createdAt: string | null;
}

export interface DatasetExperiment {
  id: string;
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  recordingManifestHash: string;
  pluginId: string;
  pluginVersion: string;
  modelReleaseId: string | null;
  assetManifestSha256: string | null;
  parameters: Record<string, unknown>;
  executor: string; // frozen concrete execution identity
  evaluationProtocol: string;
  maxConcurrency: number;
  status: string; // pending|running|evaluating|completed|completed_with_failures|failed
  datasetEvaluationId: string | null;
  errorType: string | null;
  errorMessage: string | null;
  requestedExecutionMode: ExecutionMode | null;
  autoReasonCode: string | null;
  autoReason: string | null;
  workloadClass: string | null;
  expectedItems: number;
  queuedItems: number;
  runningItems: number;
  completedItems: number;
  failedItems: number;
  attemptCount: number;
  createdAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
}

export interface DatasetExperimentCreateRequest {
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  pluginId: string;
  pluginVersion: string;
  executionMode: ExecutionMode;
  executor?: string;
  modelReleaseId?: string | null;
  parameters: Record<string, unknown>;
  evaluationProtocol?: string; // omit to let the backend apply its default
  maxConcurrency: number;
}

// ---------------------------------------------------------------------------
// Batch Analysis Package import (BAPv1) — POST /api/imported-runs/batch
// ---------------------------------------------------------------------------

export interface BatchRunMapping {
  recordingId: string;
  recordingName: string;
  analysisRunId: string;
}

export interface BatchImportSummary {
  batchId: string;
  importFingerprint: string;
  archiveSha256: string;
  datasetName: string;
  datasetSplit: string;
  pipelineId: string;
  pipelineVersion: string;
  labelSpace: string;
  itemCount: number;
  detectionCount: number;
  alreadyImported: boolean;
  createdRuns: number;
  existingRuns: number;
  createdDetections: number;
  matchedRecordings: number;
  missingRecordings: number;
  ambiguousRecordings: number;
  fingerprintMismatches: number;
  recordingRunMapping: BatchRunMapping[];
}
