import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { SpectrogramViewer } from "./SpectrogramViewer";
import type { DetectionResult, SpectrogramMeta } from "../../api/types";

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
    tStartS: 0.2,
    tEndS: 0.5,
    fLowHz: 2_420_000_000,
    fHighHz: 2_440_000_000,
    classId: 2,
    className: "WiFi 20MHz 64QAM",
    confidence: 0.93,
  },
];

test("selects a detection from its physical-coordinate overlay", () => {
  const onSelectDetection = vi.fn();
  render(
    <SpectrogramViewer
      meta={meta}
      detections={detections}
      onSelectDetection={onSelectDetection}
    />,
  );

  fireEvent.click(screen.getByLabelText("Select det_002"));
  expect(onSelectDetection).toHaveBeenCalledWith("det_002");
});

test("reports physical time and frequency under the pointer", () => {
  render(<SpectrogramViewer meta={meta} detections={[]} />);
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

test("supports zoom and reset without changing physical overlays", () => {
  render(<SpectrogramViewer meta={meta} detections={detections} />);
  const viewer = screen.getByTestId("spectrogram-viewer");

  fireEvent.wheel(viewer, { deltaY: -100 });
  expect(screen.getByTestId("zoom-readout")).not.toHaveTextContent("1.00×");

  fireEvent.click(screen.getByRole("button", { name: "Reset View" }));
  expect(screen.getByTestId("zoom-readout")).toHaveTextContent("1.00×");
  expect(screen.getByLabelText("Select det_002")).toBeInTheDocument();
});
