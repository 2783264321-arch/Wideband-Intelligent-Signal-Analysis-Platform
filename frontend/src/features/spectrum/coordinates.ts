export const timeToPercent = (t: number, start: number, end: number) =>
  ((t - start) / (end - start)) * 100;

export const frequencyToPercentFromTop = (f: number, low: number, high: number) =>
  ((high - f) / (high - low)) * 100;
