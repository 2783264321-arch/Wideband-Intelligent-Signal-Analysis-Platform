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
  stages: string[];
  inspectableStages: string[];
  taskCapability: string;
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
  startedAt?: string | null;
  finishedAt?: string | null;
  errorType?: string | null;
  errorMessage?: string | null;
  workerPid?: number | null;
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
