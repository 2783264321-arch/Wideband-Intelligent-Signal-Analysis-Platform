import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SpectrogramViewer } from "./SpectrogramViewer";
import { VIEWER_VIEWPORT_HEIGHT } from "./viewerGeometry";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { DetectionResult, GroundTruthResult, SpectrogramMeta } from "../../api/types";

const meta: SpectrogramMeta = {
  imageUrl: "",
  tStartS: 0,
  tEndS: 1,
  fLowHz: 2_400_000_000,
  fHighHz: 2_480_000_000,
  representation: "stft",
};

const detections: DetectionResult[] = [
  {
    id: "det_002",
    runId: "run",
    recordingId: "rec",
    tStartS: 0.2,
    tEndS: 0.5,
    fLowHz: 2_420_000_000,
    fHighHz: 2_440_000_000,
    classId: 2,
    className: "WiFi 20MHz 64QAM",
    confidence: 0.93,
  },
];

const groundTruth = (id: string): GroundTruthResult => ({
  id,
  recordingId: "rec",
  tStartS: 0.1,
  tEndS: 0.3,
  fLowHz: 2_410_000_000,
  fHighHz: 2_430_000_000,
  classId: 2,
  className: "WiFi 20MHz 64QAM",
});

test("selects a detection from its physical-coordinate overlay", () => {
  const onSelectDetection = vi.fn();
  render(
    renderWithLocalization(
      <SpectrogramViewer
        meta={meta}
        detections={detections}
        onSelectDetection={onSelectDetection}
      />,
    ),
  );

  fireEvent.click(screen.getByLabelText("Select det_002"));
  expect(onSelectDetection).toHaveBeenCalledWith("det_002");
});

test("reports physical time and frequency under the pointer", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={[]} />));
  const viewer = screen.getByTestId("spectrogram-viewer");
  vi.spyOn(viewer, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 1000,
    bottom: 500,
    width: 1000,
    height: 500,
    toJSON: () => ({}),
  });

  fireEvent.mouseMove(viewer, { clientX: 250, clientY: 125 });
  expect(screen.getByTestId("cursor-readout")).toHaveTextContent("0.250000 s");
  expect(screen.getByTestId("cursor-readout")).toHaveTextContent("2460.000 MHz");
});

test("wheel scrolls the page and does not zoom or prevent default", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={[]} />));
  const viewer = screen.getByTestId("spectrogram-viewer");
  const event = new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true });
  viewer.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
});

test("zoom controls change the zoom percentage and fit/reset restore 100%", async () => {
  const user = userEvent.setup();
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

test("renders distinct ground-truth, prediction, and selected overlays with a legend", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer
        meta={meta}
        detections={detections}
        groundTruth={[groundTruth("gt_1")]}
        selectedDetectionId="det_002"
      />,
    ),
  );
  const gt = screen.getByTestId("overlay-gt-gt_1");
  const selected = screen.getByTestId("overlay-det-det_002");
  expect(gt).toHaveAttribute("data-overlay", "ground-truth");
  expect(gt).toHaveAttribute("stroke-dasharray");
  // Ground truth must read as white, not the spectrogram's own green.
  expect(gt.getAttribute("stroke")).toMatch(/fff|white/i);
  expect(selected).toHaveAttribute("data-overlay", "prediction");
  expect(selected).toHaveAttribute("data-selected", "true");
  expect(Number(selected.getAttribute("stroke-width"))).toBeGreaterThan(
    Number(gt.getAttribute("stroke-width")),
  );
  const legend = screen.getByTestId("spectrogram-legend");
  expect(legend).toHaveTextContent("Ground Truth");
  expect(legend).toHaveTextContent("Prediction");
  expect(legend).toHaveTextContent("Selected prediction");
});

test("numbers every ground-truth box so the signal count is visible", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer
        meta={meta}
        detections={[]}
        groundTruth={[groundTruth("gt_1"), groundTruth("gt_2"), groundTruth("gt_3")]}
      />,
    ),
  );
  const layer = screen.getByTestId("ground-truth-index-layer");
  expect(layer).toBeInTheDocument();
  expect(screen.getByTestId("overlay-gt-index-gt_1")).toHaveTextContent("1");
  expect(screen.getByTestId("overlay-gt-index-gt_2")).toHaveTextContent("2");
  expect(screen.getByTestId("overlay-gt-index-gt_3")).toHaveTextContent("3");
});

test("no ground-truth index layer when ground truth is hidden", () => {
  render(renderWithLocalization(<SpectrogramViewer meta={meta} detections={[]} groundTruth={[]} />));
  expect(screen.queryByTestId("ground-truth-index-layer")).toBeNull();
});

test("the detection toggle hides orange boxes but never the selected highlight", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer
        meta={meta}
        detections={detections}
        groundTruth={[]}
        selectedDetectionId="det_002"
        showDetections={false}
      />,
    ),
  );
  // The selected box survives the toggle and keeps its selected (red) styling.
  const selected = screen.getByTestId("overlay-det-det_002");
  expect(selected).toHaveAttribute("data-selected", "true");
  expect(screen.getByTestId("overlay-det-glow-det_002")).toBeInTheDocument();
  // The prediction legend entry is not advertised while predictions are hidden.
  expect(screen.getByTestId("spectrogram-legend")).not.toHaveTextContent("Prediction");
});

test("with the detection toggle on, non-selected boxes are drawn as predictions", () => {
  render(
    renderWithLocalization(<SpectrogramViewer meta={meta} detections={detections} groundTruth={[]} />),
  );
  const box = screen.getByTestId("overlay-det-det_002");
  expect(box).toHaveAttribute("data-selected", "false");
  expect(screen.getByTestId("spectrogram-legend")).toHaveTextContent("Prediction");
});

test("analysis viewport height is substantial and independent of the raster natural aspect", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer meta={{ ...meta, imageUrl: "/x.png" }} detections={[]} />,
    ),
  );
  const viewer = screen.getByTestId("spectrogram-viewer");
  expect(viewer.style.height).toBe(VIEWER_VIEWPORT_HEIGHT);
  expect(viewer.style.aspectRatio).toBe("");

  // A wide/short raster must NOT drive the browser display geometry.
  const img = screen.getByAltText("STFT spectrogram") as HTMLImageElement;
  Object.defineProperty(img, "naturalWidth", { value: 4096 });
  Object.defineProperty(img, "naturalHeight", { value: 128 });
  fireEvent.load(img);
  expect(viewer.style.height).toBe(VIEWER_VIEWPORT_HEIGHT);
  expect(viewer.style.aspectRatio).toBe("");
});

test("image and prediction/ground-truth overlays share the same display viewport", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer
        meta={{ ...meta, imageUrl: "/x.png" }}
        detections={detections}
        groundTruth={[groundTruth("gt_1")]}
      />,
    ),
  );
  const viewer = screen.getByTestId("spectrogram-viewer");
  const img = screen.getByAltText("STFT spectrogram");
  const overlayLayer = viewer.querySelector('svg[aria-label="Detection overlays"]') as SVGSVGElement | null;
  expect(overlayLayer).not.toBeNull();
  expect(viewer.contains(img)).toBe(true);
  expect(viewer.contains(overlayLayer as SVGSVGElement)).toBe(true);
  expect(img).toHaveStyle({ position: "absolute", width: "100%", height: "100%" });
  expect(overlayLayer as SVGSVGElement).toHaveStyle({ position: "absolute", width: "100%", height: "100%" });
});

test("localizes ordinary controls and hints in zh-CN without changing physical values", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer meta={meta} detections={detections} />,
      { locale: "zh-CN" },
    ),
  );
  expect(screen.getByRole("button", { name: "重置视图" })).toBeInTheDocument();
  expect(screen.getByText("移动指针查看时间 / 频率")).toBeInTheDocument();
  expect(screen.getByLabelText("选择 det_002")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reset View" })).toBeNull();
  expect(screen.getByText("0.000000 s")).toBeInTheDocument();
});
