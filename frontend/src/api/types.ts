export interface DetectionResult {
  id: string;
  tStartS: number;
  tEndS: number;
  fLowHz: number;
  fHighHz: number;
  classId: number;
  className: string;
  confidence: number;
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
