import { expect, test } from "vitest";
import {
  DEFAULT_VIEWER_ASPECT_RATIO,
  FIT_ZOOM,
  MAX_ZOOM,
  MIN_ZOOM,
  viewerAspectRatio,
  zoomStep,
} from "./viewerGeometry";

test("zoom out works from the default view and clamps at 0.5", () => {
  expect(zoomStep(FIT_ZOOM, "out")).toBeLessThan(1.0);
  expect(zoomStep(MIN_ZOOM, "out")).toBe(MIN_ZOOM);
  expect(zoomStep(FIT_ZOOM, "in")).toBeGreaterThan(1.0);
  expect(zoomStep(MAX_ZOOM, "in")).toBe(MAX_ZOOM);
});

test("uses the default aspect when the image size is unknown", () => {
  expect(viewerAspectRatio(null)).toBe(DEFAULT_VIEWER_ASPECT_RATIO);
  expect(viewerAspectRatio({ width: 0, height: 0 })).toBe(DEFAULT_VIEWER_ASPECT_RATIO);
});

test("uses the intrinsic image aspect when known", () => {
  expect(viewerAspectRatio({ width: 1200, height: 400 })).toBe(3);
});
