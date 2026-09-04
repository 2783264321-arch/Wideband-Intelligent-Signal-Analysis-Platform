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
