import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { NoRunnableExecutorPanel } from "./NoRunnableExecutorPanel";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { ExecutorSelection } from "../../api/types";

const selection: ExecutorSelection = {
  requestedMode: "auto",
  resolvedExecutor: null,
  reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
  reason: "No runnable executor.",
  workloadClass: "small",
  candidates: [
    { executor: "local_cpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
    { executor: "local_gpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
    { executor: "remote_gpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
  ],
};

test("lists candidate states instead of a generic sentence", () => {
  render(renderWithLocalization(<NoRunnableExecutorPanel selection={selection} />));
  expect(screen.getByTestId("no-runnable-executor-panel")).toBeInTheDocument();
  expect(screen.getByText(/Local CPU/)).toBeInTheDocument();
  expect(screen.getByText(/Local GPU/)).toBeInTheDocument();
  expect(screen.getByText(/Remote GPU/)).toBeInTheDocument();
});

test("returns null when an executor is runnable", () => {
  const runnable: ExecutorSelection = {
    ...selection,
    resolvedExecutor: "local_cpu",
    candidates: [
      { ...selection.candidates[0], configured: true, certified: true, available: true },
      selection.candidates[1],
      selection.candidates[2],
    ],
  };
  const { container } = render(renderWithLocalization(<NoRunnableExecutorPanel selection={runnable} />));
  expect(container).toBeEmptyDOMElement();
});

test("returns null without a selection", () => {
  const { container } = render(renderWithLocalization(<NoRunnableExecutorPanel selection={null} />));
  expect(container).toBeEmptyDOMElement();
});
