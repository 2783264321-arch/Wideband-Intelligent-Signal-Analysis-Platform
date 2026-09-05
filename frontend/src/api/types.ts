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
