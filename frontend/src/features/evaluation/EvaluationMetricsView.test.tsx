import { render, screen } from "@testing-library/react";
import { EvaluationMetricsView } from "./EvaluationMetricsView";
import type { DatasetEvaluation } from "../../api/types";

function evaluation(aggregate: unknown): DatasetEvaluation {
  return { id: "eval_1", aggregateMetrics: aggregate } as unknown as DatasetEvaluation;
}

const localization = {
  ap50: 0.61,
  ap50_95: 0.42,
  operating: { tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 },
};

test("renders localization metrics", () => {
  render(<EvaluationMetricsView evaluation={evaluation({
    classificationApplicable: false,
    classificationReason: "detection_only_pipeline",
    localization,
    classificationOnMatched: null,
    classAware: null,
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent("Localization");
  expect(view).toHaveTextContent("0.61");
  expect(view).toHaveTextContent("0.42");
});

test("classification inapplicable renders N/A plus the bounded reason", () => {
  render(<EvaluationMetricsView evaluation={evaluation({
    classificationApplicable: false,
    classificationReason: "label_space_mismatch",
    localization,
    classificationOnMatched: null,
    classAware: null,
  })} />);
  expect(screen.getAllByText(/N\/A/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/label_space_mismatch/).length).toBeGreaterThan(0);
});

test("null metrics render N/A and never 0", () => {
  render(<EvaluationMetricsView evaluation={evaluation({
    classificationApplicable: true,
    classificationReason: null,
    localization: {
      ap50: null,
      ap50_95: null,
      operating: { tp: 0, fp: 0, fn: 0, precision: null, recall: null, f1: null },
    },
    classificationOnMatched: null,
    classAware: null,
  })} />);
  expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  expect(screen.queryByText("0")).toBeNull();
});

test("a null aggregateMetrics shows an explicit unavailable state", () => {
  render(<EvaluationMetricsView evaluation={evaluation(null)} />);
  expect(screen.getByTestId("evaluation-metrics-view")).toHaveTextContent(/not available|unavailable/i);
});

// ---------------------------------------------------------------------------
// Review corrective A2 — per-class + confusion sections
// ---------------------------------------------------------------------------

const applicableAggregate = {
  classificationApplicable: true,
  classificationReason: null,
  localization,
  classificationOnMatched: null,
  classAware: null,
};

function evaluationWith(fields: Record<string, unknown>): DatasetEvaluation {
  return { id: "eval_1", ...fields } as unknown as DatasetEvaluation;
}

test("renders per-class rows including counts and metrics", () => {
  render(<EvaluationMetricsView evaluation={evaluationWith({
    aggregateMetrics: applicableAggregate,
    perClassMetrics: [
      { classId: 9, className: "LoRa", gtCount: 5, predictionCount: 6, ap50: 0.8, ap50_95: 0.6, operating: { tp: 4, fp: 2, fn: 1, precision: 0.66, recall: 0.8, f1: 0.72 } },
    ],
    confusion: null,
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent("LoRa");
  expect(view).toHaveTextContent("0.8");
  expect(view).toHaveTextContent("0.66");
});

test("renders confusion rows", () => {
  render(<EvaluationMetricsView evaluation={evaluationWith({
    aggregateMetrics: applicableAggregate,
    perClassMetrics: [],
    confusion: [{ gtClassId: 9, gtClassName: "LoRa", predClassId: 4, predClassName: "WiFi", count: 3 }],
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent("LoRa");
  expect(view).toHaveTextContent("WiFi");
  expect(view).toHaveTextContent("3");
});

test("null per-class metric renders N/A, never 0", () => {
  render(<EvaluationMetricsView evaluation={evaluationWith({
    aggregateMetrics: applicableAggregate,
    perClassMetrics: [
      { classId: 9, className: "LoRa", gtCount: 0, predictionCount: 0, ap50: null, ap50_95: null, operating: { tp: 0, fp: 0, fn: 0, precision: null, recall: null, f1: null } },
    ],
    confusion: [],
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent("N/A");
  expect(view).not.toHaveTextContent("AP50: 0");
});

test("empty per-class/confusion arrays show explicit empty states", () => {
  render(<EvaluationMetricsView evaluation={evaluationWith({
    aggregateMetrics: applicableAggregate,
    perClassMetrics: [],
    confusion: [],
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent(/No per-class metrics available/i);
  expect(view).toHaveTextContent(/No confusion data available/i);
});

test("classification inapplicable shows N/A + reason for the classification-oriented sections", () => {
  render(<EvaluationMetricsView evaluation={evaluationWith({
    aggregateMetrics: { ...applicableAggregate, classificationApplicable: false, classificationReason: "label_space_mismatch" },
    perClassMetrics: [],
    confusion: [],
  })} />);
  const view = screen.getByTestId("evaluation-metrics-view");
  expect(view).toHaveTextContent(/label_space_mismatch/);
  expect(view).toHaveTextContent(/N\/A/);
});
