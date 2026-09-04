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
