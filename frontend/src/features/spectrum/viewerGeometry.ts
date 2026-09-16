export const DEFAULT_VIEWER_ASPECT_RATIO = 16 / 8;

export const MIN_ZOOM = 0.5;
export const FIT_ZOOM = 1.0;
export const MAX_ZOOM = 8;
export const ZOOM_FACTOR = 1.2;

export interface ViewerNaturalSize {
  width: number;
  height: number;
}

export function viewerAspectRatio(natural: ViewerNaturalSize | null): number {
  if (!natural || natural.width <= 0 || natural.height <= 0) {
    return DEFAULT_VIEWER_ASPECT_RATIO;
  }
  return natural.width / natural.height;
}

export function zoomStep(current: number, direction: "in" | "out"): number {
  const next = direction === "in" ? current * ZOOM_FACTOR : current / ZOOM_FACTOR;
  return Math.min(Math.max(next, MIN_ZOOM), MAX_ZOOM);
}
