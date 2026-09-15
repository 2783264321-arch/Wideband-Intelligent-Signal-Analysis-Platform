import { render, screen } from "@testing-library/react";
import { SignalSummary } from "./SignalSummary";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { DetectionResult } from "../../api/types";

const detection: DetectionResult = {
  id: "det_1",
  runId: "run_42",
  recordingId: "rec_7",
  tStartS: 0.25,
  tEndS: 0.5,
  fLowHz: 2_420_000_000,
  fHighHz: 2_440_000_000,
  classId: 2,
  className: "WiFi 20MHz 64QAM",
  confidence: 0.93,
};

test("renders localized field labels in en-US", () => {
  render(renderWithLocalization(<SignalSummary detection={detection} />));
  expect(screen.getByText("Signal Type")).toBeInTheDocument();
  expect(screen.getByText("Center Frequency")).toBeInTheDocument();
});

test("localizes field labels in zh-CN without changing physical values or raw class identity", () => {
  render(renderWithLocalization(<SignalSummary detection={detection} />, { locale: "zh-CN" }));

  expect(screen.getByText("信号类型")).toBeInTheDocument();
  expect(screen.getByText("置信度")).toBeInTheDocument();
  expect(screen.getByText("中心频率")).toBeInTheDocument();
  expect(screen.getByText("带宽")).toBeInTheDocument();
  expect(screen.getByText("时间")).toBeInTheDocument();
  expect(screen.getByText("时长")).toBeInTheDocument();
  expect(screen.queryByText("Signal Type")).toBeNull();

  // Raw class identity, units and numeric values are unchanged.
  expect(screen.getByText("WiFi 20MHz 64QAM")).toBeInTheDocument();
  expect(screen.getByText("93.0%")).toBeInTheDocument();
  expect(screen.getByText("2430.000 MHz")).toBeInTheDocument();
  expect(screen.getByText("20.000 MHz")).toBeInTheDocument();
  expect(screen.getByText("0.250000–0.500000 s")).toBeInTheDocument();
  expect(screen.getByText("250.000 ms")).toBeInTheDocument();
});
