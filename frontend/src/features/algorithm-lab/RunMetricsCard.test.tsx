import { render, screen } from "@testing-library/react";
import { RunMetricsCard } from "./RunMetricsCard";
import type { RunComparison } from "../../api/types";

function makeRun(overrides: Partial<RunComparison>): RunComparison {
  return {
    runId: "run_x",
    pipelineId: "zoomspec",
    pipelineName: "ZoomSpec",
    metrics: { tp: 6, fp: 7, fn: 0, precision: 0.4615, recall: 1.0, f1: 0.6316, meanMatchedIou: 0.8921 },
    classificationApplicable: true,
    classificationReason: null,
    classification: {
      matchedCount: 6,
      classCorrect: 6,
      classWrong: 0,
      matchedAccuracy: 1.0,
      confusions: [],
    },
    classAware: { tp: 6, fp: 7, fn: 0, precision: 0.4615, recall: 1.0, f1: 0.6316 },
    ...overrides,
  };
}

test("renders localization, classification, and end-to-end sections when available", () => {
  render(<RunMetricsCard run={makeRun({})} side="B" />);
  expect(screen.getByText(/Run B:/)).toBeInTheDocument();
  // Localization
  expect(screen.getByText("Precision")).toBeInTheDocument();
  expect(screen.getByText("Recall")).toBeInTheDocument();
  expect(screen.getByText("Mean IoU")).toBeInTheDocument();
  // Classification
  expect(screen.getByText("Matched")).toBeInTheDocument();
  expect(screen.getByText("Correct")).toBeInTheDocument();
  expect(screen.getByText("Wrong")).toBeInTheDocument();
  expect(screen.getByText("Matched Accuracy")).toBeInTheDocument();
  // Confusions empty
  expect(screen.getByText("None")).toBeInTheDocument();
  // End-to-End
  expect(screen.getByText("Class-aware Precision")).toBeInTheDocument();
  expect(screen.getByText("Class-aware Recall")).toBeInTheDocument();
  expect(screen.getByText("Class-aware F1")).toBeInTheDocument();
});

test("renders confusions when present", () => {
  const run = makeRun({
    classification: {
      matchedCount: 6,
      classCorrect: 5,
      classWrong: 1,
      matchedAccuracy: 5 / 6,
      confusions: [
        { gtClassId: 9, gtClassName: "LoRa 250kHz", predClassId: 13, predClassName: "FM", count: 1 },
      ],
    },
  });
  render(<RunMetricsCard run={run} side="A" />);
  expect(screen.getByText(/LoRa 250kHz/)).toBeInTheDocument();
  expect(screen.getByText(/FM/)).toBeInTheDocument();
});

test("renders Not applicable when classification unavailable", () => {
  const run = makeRun({
    classificationApplicable: false,
    classificationReason: "detection_only_pipeline",
    classification: null,
    classAware: null,
  });
  render(<RunMetricsCard run={run} side="A" />);
  expect(screen.getAllByText(/Not applicable/).length).toBeGreaterThan(0);
  expect(screen.getByText(/detection_only_pipeline/)).toBeInTheDocument();
  // localization still shown
  expect(screen.getByText("Precision")).toBeInTheDocument();
});

test("renders zero-match accuracy as em dash not zero", () => {
  const run = makeRun({
    classification: {
      matchedCount: 0,
      classCorrect: 0,
      classWrong: 0,
      matchedAccuracy: null,
      confusions: [],
    },
  });
  render(<RunMetricsCard run={run} side="A" />);
  expect(screen.getByText("—")).toBeInTheDocument();
});