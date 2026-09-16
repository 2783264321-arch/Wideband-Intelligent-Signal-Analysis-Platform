import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { SpectrogramViewer } from "./SpectrogramViewer";
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

test("frame aspect follows the intrinsic image aspect", () => {
  render(
    renderWithLocalization(
      <SpectrogramViewer meta={{ ...meta, imageUrl: "/x.png" }} detections={[]} />,
    ),
  );
  const img = screen.getByAltText("STFT spectrogram") as HTMLImageElement;
  Object.defineProperty(img, "naturalWidth", { value: 1200 });
  Object.defineProperty(img, "naturalHeight", { value: 400 });
  fireEvent.load(img);
  expect(screen.getByTestId("spectrogram-viewer").style.aspectRatio).toBe("3 / 1");
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
