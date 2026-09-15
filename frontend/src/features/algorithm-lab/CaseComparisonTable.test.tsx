import { render, screen } from "@testing-library/react";
import { CaseComparisonTable } from "./CaseComparisonTable";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { AlgorithmLabCase } from "../../api/types";

function makeCase(overrides: Partial<AlgorithmLabCase>): AlgorithmLabCase {
  return {
    groundTruthId: "gt0",
    classId: 9,
    className: "LoRa 250kHz",
    bbox: { tStartS: 0.0, tEndS: 1.0, fLowHz: 0, fHighHz: 1e6 },
    comparison: "both_detected",
    runA: {
      matched: true,
      detectionId: "det_a",
      iou: 0.958,
      classId: 9,
      className: "LoRa 250kHz",
      confidence: 0.97,
      classCorrect: true,
      bbox: { tStartS: 0.0, tEndS: 1.0, fLowHz: 0, fHighHz: 1e6 },
    },
    runB: {
      matched: true,
      detectionId: "det_b",
      iou: 0.92,
      classId: 13,
      className: "FM",
      confidence: 0.6,
      classCorrect: false,
      bbox: { tStartS: 0.0, tEndS: 1.0, fLowHz: 0, fHighHz: 1e6 },
    },
    ...overrides,
  };
}

test("shows IoU, predicted class and class correctness for classification-capable runs", () => {
  render(renderWithLocalization(<CaseComparisonTable cases={[makeCase({})]} />));
  expect(screen.getAllByText(/LoRa 250kHz/).length).toBeGreaterThan(0);
  expect(screen.getByText(/0.958/)).toBeInTheDocument();
  expect(screen.getByText(/0.920/)).toBeInTheDocument();
  expect(screen.getByText(/FM/)).toBeInTheDocument();
});

test("shows Missed for unmatched runs", () => {
  const item = makeCase({
    comparison: "both_missed",
    runA: { matched: false, detectionId: null, iou: null, classId: null, className: null, confidence: null, classCorrect: null, bbox: null },
    runB: { matched: false, detectionId: null, iou: null, classId: null, className: null, confidence: null, classCorrect: null, bbox: null },
  });
  render(renderWithLocalization(<CaseComparisonTable cases={[item]} />));
  expect(screen.getAllByText(/Missed/).length).toBe(2);
});

test("shows Class N/A for detection-only matched predictions", () => {
  const item = makeCase({
    runA: {
      matched: true,
      detectionId: "det_a",
      iou: 0.92,
      classId: 0,
      className: "Signal",
      confidence: 0.9,
      classCorrect: null,
      bbox: { tStartS: 0.0, tEndS: 1.0, fLowHz: 0, fHighHz: 1e6 },
    },
  });
  render(renderWithLocalization(<CaseComparisonTable cases={[item]} />));
  expect(screen.getByText("Signal")).toBeInTheDocument();
  expect(screen.getByText("Class N/A")).toBeInTheDocument();
});

test("keeps localization comparison states unchanged", () => {
  render(
    renderWithLocalization(
      <CaseComparisonTable
        cases={[
          makeCase({ groundTruthId: "g1", comparison: "a_only" }),
          makeCase({ groundTruthId: "g2", comparison: "b_only" }),
          makeCase({ groundTruthId: "g3", comparison: "both_missed" }),
          makeCase({ groundTruthId: "g4", comparison: "both_detected" }),
        ]}
      />,
    ),
  );
  expect(screen.getByText("A only")).toBeInTheDocument();
  expect(screen.getByText("B only")).toBeInTheDocument();
  expect(screen.getByText("Both missed")).toBeInTheDocument();
  expect(screen.getByText("Both detected")).toBeInTheDocument();
});
test("localizes column headers and known comparison states in zh-CN while preserving raw identity", () => {
  render(renderWithLocalization(
    <CaseComparisonTable cases={[makeCase({ groundTruthId: "gt_42", comparison: "a_only" })]} />,
    { locale: "zh-CN" },
  ));
  expect(screen.getByText("GT / 信号类型")).toBeInTheDocument();
  expect(screen.getByText("任务 A（IoU / 类别）")).toBeInTheDocument();
  expect(screen.getByText("任务 B（IoU / 类别）")).toBeInTheDocument();
  expect(screen.getByText("对比结果")).toBeInTheDocument();
  expect(screen.getByText("仅 A 检测到")).toBeInTheDocument();
  expect(screen.queryByText("A only")).toBeNull();
  // Raw GT identity and IoU numbers are unchanged.
  expect(screen.getByText(/gt_42/)).toBeInTheDocument();
  expect(screen.getByText(/0.958/)).toBeInTheDocument();
});
