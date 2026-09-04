export function spectrumPathForRun(recordingId: string, runId: string, selectedDetectionId?: string): string {
  const params = new URLSearchParams({ run: runId });
  if (selectedDetectionId) params.set("selected", selectedDetectionId);
  return `/spectrum/${recordingId}?${params}`;
}
