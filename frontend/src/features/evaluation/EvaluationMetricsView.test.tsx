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
  expect(screen.getByText(/label_space_mismatch/)).toBeInTheDocument();
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
