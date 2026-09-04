import type { DetectionResult } from "../../api/types";

export const centerFrequencyHz = (detection: DetectionResult) => (detection.fLowHz + detection.fHighHz) / 2;
export const bandwidthHz = (detection: DetectionResult) => detection.fHighHz - detection.fLowHz;
export const durationS = (detection: DetectionResult) => detection.tEndS - detection.tStartS;
