export interface FrequencyScale {
  factor: number;
  unit: string;
  decimals: number;
}

/** Pick a readable absolute-frequency unit for an axis range. */
export function pickFrequencyScale(maxAbsHz: number): FrequencyScale {
  const magnitude = Math.abs(maxAbsHz);
  if (magnitude >= 1e9) return { factor: 1e9, unit: "GHz", decimals: 6 };
  if (magnitude >= 1e6) return { factor: 1e6, unit: "MHz", decimals: 3 };
  if (magnitude >= 1e3) return { factor: 1e3, unit: "kHz", decimals: 1 };
  return { factor: 1, unit: "Hz", decimals: 0 };
}

export function formatFrequency(value: number, scale: FrequencyScale): string {
  return (value / scale.factor).toFixed(scale.decimals);
}
