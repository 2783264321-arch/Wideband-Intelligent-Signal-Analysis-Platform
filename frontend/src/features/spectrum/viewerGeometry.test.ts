import { expect, test } from "vitest";
import {
  FIT_ZOOM,
  MAX_ZOOM,
  MIN_ZOOM,
  VIEWER_VIEWPORT_HEIGHT,
  zoomStep,
} from "./viewerGeometry";

test("zoom out works from the default view and clamps at 0.5", () => {
  expect(zoomStep(FIT_ZOOM, "out")).toBeLessThan(1.0);
  expect(zoomStep(MIN_ZOOM, "out")).toBe(MIN_ZOOM);
  expect(zoomStep(FIT_ZOOM, "in")).toBeGreaterThan(1.0);
  expect(zoomStep(MAX_ZOOM, "in")).toBe(MAX_ZOOM);
});

test("the analysis viewport height is substantial and responsive, not raster-derived", () => {
  expect(VIEWER_VIEWPORT_HEIGHT).toContain("clamp(");
  expect(VIEWER_VIEWPORT_HEIGHT).toContain("400px");
  expect(VIEWER_VIEWPORT_HEIGHT).toContain("55vh");
  expect(VIEWER_VIEWPORT_HEIGHT).toContain("650px");
});
