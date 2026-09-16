export const ALGORITHM_LAB_ROUTE_KEY = "wisa.algorithmLab.lastRoute";

const ALGORITHM_LAB_PATH = "/algorithm-lab";

export function isMeaningfulAlgorithmLabSearch(search: string): boolean {
  const params = new URLSearchParams(search);
  return params.has("recording");
}

export function rememberAlgorithmLabRoute(search: string): void {
  if (!isMeaningfulAlgorithmLabSearch(search)) return;
  try {
    localStorage.setItem(ALGORITHM_LAB_ROUTE_KEY, `${ALGORITHM_LAB_PATH}${search}`);
  } catch {
    // Ignore storage failures.
  }
}

export function readAlgorithmLabRoute(): string | null {
  try {
    const stored = localStorage.getItem(ALGORITHM_LAB_ROUTE_KEY);
    if (!stored || !stored.startsWith(`${ALGORITHM_LAB_PATH}?`)) return null;
    return stored;
  } catch {
    return null;
  }
}
