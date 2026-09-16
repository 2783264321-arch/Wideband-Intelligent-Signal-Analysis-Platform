# WISA V1.1 UX-D Spectrogram Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the spectrogram viewer stop hijacking page scroll, provide
explicit zoom controls, render clearly distinguishable Ground-Truth and
prediction overlays with a visible legend, honor real spectrogram/display
geometry, and stay readable in light and dark themes — without a broad DSP API
redesign.

**Architecture:** Rework only
`frontend/src/features/spectrum/SpectrogramViewer.tsx` and its tests, plus a
new pure geometry helper and a few localization keys. Geometry uses the loaded
image's intrinsic dimensions (no backend read-model change). No backend change.
No analytical change to detections or coordinates.

**Tech Stack:** React + TypeScript + Vite + Ant Design + Vitest +
@testing-library/react.

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

## Global Constraints

```text
Mouse wheel performs NORMAL PAGE SCROLLING; it must not zoom the viewer.
Viewer zoom uses explicit controls only.
Controls: zoom out, zoom percentage, zoom in, fit, reset.
Zoom range is MIN_ZOOM = 0.5, FIT/DEFAULT = 1.0, MAX_ZOOM = 8, factor 1.2, so
    zoom out works from the default view.
Ground Truth uses a clear green treatment; prediction uses a clear orange
    treatment; the selected prediction uses a high-contrast thicker highlight.
GT and prediction must also differ by line style/weight, not color alone.
A legend is visible.
Semantic distinction must remain clear in both light and dark modes. Overlay
    strokes derive from Ant Design `theme.useToken()` semantic values; do not
    introduce a separate CSS file or scattered `.wisa-*` stylesheet rules.
Keep testability attributes: data-overlay="ground-truth",
    data-overlay="prediction", data-selected="true|false".
UX-D is frontend-only and MUST NOT modify the API client or API types.
Distinguish explicitly: model input size, spectrogram raster size, browser
    display size.
Never force the viewer to 640x640 because a detection model uses 640x640 input.
Honor spectrogram/display geometry instead of the current hard-coded 2:1
    assumption where the image provides a better basis.
No broad DSP API redesign. No backend change. No GPU. No sealed-baseline change.
```

---

## File Map

Create:

```text
frontend/src/features/spectrum/viewerGeometry.ts
frontend/src/features/spectrum/viewerGeometry.test.ts
```

Modify:

```text
frontend/src/features/spectrum/SpectrogramViewer.tsx
frontend/src/features/spectrum/SpectrogramViewer.test.tsx
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
```

---

## Interfaces

Consumes:

```ts
// existing, unchanged
export interface SpectrogramViewerProps {
  meta: SpectrogramMeta;
  detections: DetectionResult[];
  groundTruth?: GroundTruthResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
}
timeToPercent(t, start, end): number
frequencyToPercentFromTop(f, low, high): number
```

Produces:

```ts
// viewerGeometry.ts
export const DEFAULT_VIEWER_ASPECT_RATIO: number;              // 16 / 8
export const MIN_ZOOM: number;                                 // 0.5
export const FIT_ZOOM: number;                                 // 1.0
export const MAX_ZOOM: number;                                 // 8
export const ZOOM_FACTOR: number;                              // 1.2
export interface ViewerNaturalSize { width: number; height: number; }
export function viewerAspectRatio(natural: ViewerNaturalSize | null): number;
export function zoomStep(current: number, direction: "in" | "out"): number;  // clamp [0.5, 8]
```

New message keys:

```ts
"spectrum.zoomIn": "Zoom in",                 // zh: "放大"
"spectrum.zoomOut": "Zoom out",               // zh: "缩小"
"spectrum.zoomFit": "Fit",                    // zh: "适应窗口"
"spectrum.zoomLevel": "Zoom {percent}%",      // zh: "缩放 {percent}%"
"spectrum.legend": "Legend",                  // zh: "图例"
"spectrum.legendGroundTruth": "Ground Truth", // zh: "真值标注（GT）"
"spectrum.legendPrediction": "Prediction",    // zh: "检测结果"
"spectrum.legendSelected": "Selected prediction", // zh: "已选检测结果"
```

---

## Task D1: Wheel performs normal page scrolling

**Files:**
- Modify: `frontend/src/features/spectrum/SpectrogramViewer.tsx`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: the frame no longer registers a zooming `onWheel` handler.

- [ ] Replace the existing wheel-zoom test with the failing contract test:

```tsx
test("wheel scrolls the page and does not zoom or prevent default", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={[]} />));
  const viewer = screen.getByTestId("spectrogram-viewer");
  const event = new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true });
  viewer.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
});
```

- [ ] Run `npx vitest run src/features/spectrum/SpectrogramViewer.test.tsx`; observe the failure on `defaultPrevented` / unchanged zoom.
- [ ] Remove the `onWheel` handler (and the `WheelEvent` import) from
      `SpectrogramViewer.tsx`. Keep pan-on-drag only for a pressed pointer;
      do not call `event.preventDefault()` for wheel.
- [ ] Rerun focused test; observe pass.
- [ ] Commit: `fix(ux-d): stop spectrogram wheel zoom hijacking`

---

## Task D2: Explicit zoom controls

**Files:**
- Create: `frontend/src/features/spectrum/viewerGeometry.ts`, `viewerGeometry.test.ts`
- Modify: `frontend/src/features/spectrum/SpectrogramViewer.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Produces: `zoomStep(current, direction)` and the control UI.

- [ ] Write the failing helper test:

```ts
test("zoom out works from the default view and clamps at 0.5", () => {
  expect(zoomStep(1.0, "out")).toBeLessThan(1.0);
  expect(zoomStep(0.5, "out")).toBe(0.5);
  expect(zoomStep(1.0, "in")).toBeGreaterThan(1.0);
  expect(zoomStep(8, "in")).toBe(8);
});
```

- [ ] Implement `viewerGeometry.ts`:

```ts
export const MIN_ZOOM = 0.5;
export const FIT_ZOOM = 1.0;
export const MAX_ZOOM = 8;
export const ZOOM_FACTOR = 1.2;

export function zoomStep(current: number, direction: "in" | "out"): number {
  const next = direction === "in" ? current * ZOOM_FACTOR : current / ZOOM_FACTOR;
  return Math.min(Math.max(next, MIN_ZOOM), MAX_ZOOM);
}
```

- [ ] Write the failing control test:

```tsx
test("zoom controls change the zoom percentage and fit/reset restore 100%", async () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={[]} />));
  await user.click(screen.getByRole("button", { name: "Zoom out" }));
  expect(screen.getByTestId("zoom-readout")).not.toHaveTextContent("1.00×");
  await user.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
  await user.click(screen.getByRole("button", { name: "Zoom in" }));
  await user.click(screen.getByRole("button", { name: "Fit" }));
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
  await user.click(screen.getByRole("button", { name: "Zoom in" }));
  await user.click(screen.getByRole("button", { name: "Reset View" }));
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
});
```

- [ ] Add the message keys and implement the controls:
      `zoom-out`, percentage readout (`spectrum.zoomLevel`), `zoom-in`, `fit`,
      `reset` (`spectrum.resetView`). `fit` sets `zoom=1`, `pan=(0,0)`.
      `reset` does the same and additionally clears the cursor readout.
- [ ] Rerun focused test; observe pass.
- [ ] Commit: `feat(ux-d): explicit spectrogram zoom controls`

---

## Task D3: Overlay semantics and legend

**Files:**
- Modify: `frontend/src/features/spectrum/SpectrogramViewer.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Produces: semantic overlay attributes and a legend.

- [ ] Write the failing test:

```tsx
test("renders distinct ground-truth, prediction, and selected overlays with a legend", () => {
  render(renderWithLocalization(<SpectrogramViewer
    meta={meta}
    detections={detections}
    groundTruth={[groundTruth("gt_1")]}
    selectedDetectionId="det_002"
  />));
  expect(screen.getByTestId("overlay-gt-gt_1")).toHaveAttribute("data-overlay", "ground-truth");
  expect(screen.getByTestId("overlay-det-det_002")).toHaveAttribute("data-overlay", "prediction");
  expect(screen.getByTestId("overlay-det-det_002")).toHaveAttribute("data-selected", "true");
  const legend = screen.getByTestId("spectrogram-legend");
  expect(legend).toHaveTextContent("Ground Truth");
  expect(legend).toHaveTextContent("Prediction");
  expect(legend).toHaveTextContent("Selected prediction");
});
```

- [ ] Render GT rects with `data-overlay="ground-truth"`; predictions with
      `data-overlay="prediction"`; the selected prediction additionally sets
      `data-selected="true"`.
- [ ] Derive overlay strokes from Ant Design tokens and semantic constants via
      `const { token } = theme.useToken();`. Do not add a stylesheet:

```tsx
const GT_STROKE = token.colorSuccess;          // clear green
const PRED_STROKE = token.colorWarning;        // clear orange
const PRED_SELECTED_STROKE = token.colorError; // high-contrast highlight
```

```text
Ground Truth           stroke = GT_STROKE,   strokeWidth 0.45, strokeDasharray "1.4 1"
Prediction             stroke = PRED_STROKE, strokeWidth 0.45, solid
Selected prediction    stroke = PRED_SELECTED_STROKE, strokeWidth 0.9, solid
```

- [ ] Keep GT dashed and predictions solid (plus the weight difference for the
      selected prediction) so the distinction survives color-vision
      differences. Do not rely on color alone.
- [ ] Add a `data-testid="spectrogram-legend"` legend listing GT, prediction,
      and selected-prediction labels.
- [ ] Rerun focused test; observe pass.
- [ ] Commit: `feat(ux-d): distinct GT and prediction overlays with legend`

---

## Task D4: Responsive geometry from intrinsic image size

**Files:**
- Modify: `frontend/src/features/spectrum/viewerGeometry.ts`,
  `SpectrogramViewer.tsx`
- Test: `frontend/src/features/spectrum/viewerGeometry.test.ts`,
  `SpectrogramViewer.test.tsx`

**Interfaces:**
- Produces: `viewerAspectRatio(natural)`.

- [ ] Write the failing helper test:

```ts
test("uses the default aspect when the image size is unknown", () => {
  expect(viewerAspectRatio(null)).toBe(DEFAULT_VIEWER_ASPECT_RATIO);
  expect(viewerAspectRatio({ width: 0, height: 0 })).toBe(DEFAULT_VIEWER_ASPECT_RATIO);
});

test("uses the intrinsic image aspect when known", () => {
  expect(viewerAspectRatio({ width: 1200, height: 400 })).toBe(3);
});
```

- [ ] Implement `viewerAspectRatio`:

```ts
export const DEFAULT_VIEWER_ASPECT_RATIO = 16 / 8;

export function viewerAspectRatio(natural: ViewerNaturalSize | null): number {
  if (!natural || natural.width <= 0 || natural.height <= 0) {
    return DEFAULT_VIEWER_ASPECT_RATIO;
  }
  return natural.width / natural.height;
}
```

- [ ] In `SpectrogramViewer.tsx`, track image intrinsic size on the `<img>`
      `onLoad` event (`event.currentTarget.naturalWidth/naturalHeight`) and set
      the frame `aspectRatio` from `viewerAspectRatio(naturalSize)` instead of
      the hard-coded `"16 / 8"`.
- [ ] Write the failing component test using a synthetic image load:

```tsx
test("frame aspect follows the intrinsic image aspect", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={{ ...meta, imageUrl: "/x.png" }} detections={[]} />));
  const img = screen.getByAltText("STFT spectrogram") as HTMLImageElement;
  Object.defineProperty(img, "naturalWidth", { value: 1200 });
  Object.defineProperty(img, "naturalHeight", { value: 400 });
  fireEvent.load(img);
  expect(screen.getByTestId("spectrogram-viewer")).toHaveStyle({ aspectRatio: "3" });
});
```

- [ ] Add an explicit comment in `SpectrogramViewer.tsx` naming the three
      distinct sizes and stating the viewer never assumes a model input size:

```text
// Size concepts (do not conflate):
//   model input size       owned by the detection model; never applied here
//   spectrogram raster size the intrinsic image dimensions (naturalWidth/Height)
//   browser display size    the CSS box of this frame
```

- [ ] Rerun focused tests; observe pass.
- [ ] Commit: `feat(ux-d): honor intrinsic spectrogram geometry`

---

## Task D5: Light/dark readability

**Files:**
- Modify: `frontend/src/features/spectrum/SpectrogramViewer.tsx`
- Test: `frontend/src/features/spectrum/SpectrogramViewer.test.tsx`

**Interfaces:**
- Consumes: the theme `data-theme` attribute set by UX-A `ThemeProvider`.
- Produces: theme-aware overlay framing.

- [ ] Write the failing test:

```tsx
test("overlay semantics use theme tokens and are not color-only", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={detections}
    groundTruth={[groundTruth("gt_1")]} selectedDetectionId="det_002" />));
  const gt = screen.getByTestId("overlay-gt-gt_1");
  const selected = screen.getByTestId("overlay-det-det_002");
  expect(gt).toHaveAttribute("data-overlay", "ground-truth");
  expect(gt).toHaveAttribute("stroke-dasharray");        // GT differs by style
  expect(selected).toHaveAttribute("data-selected", "true");
  expect(Number(selected.getAttribute("stroke-width"))).toBeGreaterThan(
    Number(screen.getByTestId("overlay-gt-gt_1").getAttribute("stroke-width")),
  );
});
```

- [ ] Ensure the viewer chrome (frame border, footer text, legend, cursor
      readout) uses Ant Design `theme.useToken()` values rather than fixed hex
      values, while the spectrogram raster background stays dark for contrast.
- [ ] Confirm the legend and overlay semantics remain distinguishable under both
      `data-theme="light"` and `data-theme="dark"` by matching on
      `data-overlay` / `data-selected` and stroke weight, not exact colors.
- [ ] Rerun focused test; observe pass.
- [ ] Commit: `feat(ux-d): theme-readable viewer chrome and legend`

---

## Task D6: Track boundary

- [ ] Focused viewer tests: `npx vitest run src/features/spectrum`
- [ ] Full frontend: `npm test -- --run`
- [ ] Production build: `npm run build`
- [ ] Confirm no wheel hijacking regression and no backend files changed.
- [ ] Commit: `chore(ux-d): track boundary verification`

---

## Self-Review Checklist

```text
[ ] Wheel no longer zooms or calls preventDefault.
[ ] Explicit zoom out / percentage / zoom in / fit / reset exist.
[ ] GT is clearly green; prediction clearly orange; selected thicker/high-contrast.
[ ] Legend visible.
[ ] Light/dark readability addressed via theme tokens.
[ ] Geometry respects intrinsic image aspect; no forced 640x640.
[ ] Model input size vs raster size vs display size explicitly distinguished.
[ ] No backend change; no DSP API redesign; no GPU; no sealed-baseline change.
[ ] No API client/types change (intrinsic image geometry only).
[ ] Zoom out works from the default 100% view; range is [0.5, 8].
[ ] Overlay semantics use theme tokens with no separate stylesheet.
[ ] Overlay rendering keeps physical-coordinate mapping unchanged.
```
