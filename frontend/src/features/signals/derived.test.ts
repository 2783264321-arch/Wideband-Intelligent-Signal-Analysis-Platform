import { bandwidthHz, centerFrequencyHz, durationS } from "./derived";
import type { DetectionResult } from "../../api/types";

const detection: DetectionResult = {
  id: "det",
  runId: "run",
  recordingId: "rec",
  tStartS: 1,
  tEndS: 1.25,
  fLowHz: 2_400_000_000,
  fHighHz: 2_420_000_000,
  classId: 0,
  className: "WiFi 20MHz QPSK",
  confidence: 0.9,
};

test("derives physical summary values from detection bounds", () => {
  expect(centerFrequencyHz(detection)).toBe(2_410_000_000);
  expect(bandwidthHz(detection)).toBe(20_000_000);
  expect(durationS(detection)).toBe(0.25);
});
