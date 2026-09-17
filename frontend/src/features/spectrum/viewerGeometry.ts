export const MIN_ZOOM = 0.5;
export const FIT_ZOOM = 1.0;
export const MAX_ZOOM = 8;
export const ZOOM_FACTOR = 1.2;

/**
 * Raster-independent analysis viewport height.
 *
 * The browser display is an analysis workspace, not an image viewer: its height
 * is a responsive user-oriented size, NOT derived from the spectrogram raster's
 * natural pixel aspect ratio (time and frequency are different physical
 * dimensions). The image is allowed to stretch to fill this viewport.
 */
export const VIEWER_VIEWPORT_HEIGHT = "clamp(400px, 55vh, 650px)";

export function zoomStep(current: number, direction: "in" | "out"): number {
  const next = direction === "in" ? current * ZOOM_FACTOR : current / ZOOM_FACTOR;
  return Math.min(Math.max(next, MIN_ZOOM), MAX_ZOOM);
}
