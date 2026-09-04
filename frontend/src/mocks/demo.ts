import type { DetectionResult, RecordingSummary, SpectrogramMeta } from "../api/types";

export const demoRecording: RecordingSummary = {
  id: "rec_demo",
  name: "SpaceNet Demo Recording",
  datasetName: "SpaceNet Advanced",
  sampleRateHz: 83_500_000,
  centerFrequencyHz: 2_441_750_000,
  durationS: 0.1,
  hasGroundTruth: true,
};

export const demoSpectrogram: SpectrogramMeta = {
  imageUrl: "",
  tStartS: 0,
  tEndS: 0.1,
  fLowHz: 2_400_000_000,
  fHighHz: 2_483_500_000,
  representation: "stft",
};

export const demoDetections: DetectionResult[] = [
  {
    id: "det_001",
    runId: "mock-run",
    recordingId: "rec_demo",
    tStartS: 0.015,
    tEndS: 0.078,
    fLowHz: 2_407_000_000,
    fHighHz: 2_427_000_000,
    classId: 2,
    className: "WiFi 20MHz 64QAM",
    confidence: 0.97,
  },
  {
    id: "det_002",
    runId: "mock-run",
    recordingId: "rec_demo",
    tStartS: 0.032,
    tEndS: 0.08,
    fLowHz: 2_417_973_850,
    fHighHz: 2_418_026_150,
    classId: 9,
    className: "LoRa 250kHz",
    confidence: 0.94,
  },
  {
    id: "det_003",
    runId: "mock-run",
    recordingId: "rec_demo",
    tStartS: 0.005,
    tEndS: 0.045,
    fLowHz: 2_451_000_000,
    fHighHz: 2_453_000_000,
    classId: 8,
    className: "Zigbee",
    confidence: 0.91,
  },
];
