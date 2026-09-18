import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SampleDifferencesTable } from "./SampleDifferencesTable";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type {
  DatasetBenchmarkCompareResult,
  DatasetRecordingComparison,
} from "../../api/types";

const STATES: DatasetRecordingComparison[] = ["both_detected", "a_only", "b_only", "both_missed"];

function makeResult(total: number): DatasetBenchmarkCompareResult {
  const recordings = Array.from({ length: total }, (_, index) => ({
    recordingId: `rec_${index}`,
    recordingName: `sample-${index}`,
    evaluationARunId: `run_a_${index}`,
    evaluationBRunId: `run_b_${index}`,
    comparison: STATES[index % STATES.length],
  }));
  return {
    comparable: true,
    reasons: [],
    evaluationAId: "eval_a",
    evaluationBId: "eval_b",
    aggregateA: null,
    aggregateB: null,
    deltas: {},
    recordings,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderTable(result: DatasetBenchmarkCompareResult) {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/"]}>
        <SampleDifferencesTable result={result} />
        <Routes>
          <Route path="/samples/:recordingId" element={<LocationProbe />} />
          <Route path="/spectrum/:recordingId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

function renderedRowCount(): number {
  return screen.queryAllByTestId(/^sample-difference-row-/).length;
}

test("2500 logical rows do not all render on the first page", () => {
  renderTable(makeResult(2500));
  expect(renderedRowCount()).toBeLessThan(2500);
  expect(screen.queryByTestId("sample-difference-row-rec_2499")).toBeNull();
});

test("the default first page contains at most 50 rows and reports the total", () => {
  renderTable(makeResult(2500));
  expect(renderedRowCount()).toBe(50);
  expect(screen.getByTestId("sample-difference-row-rec_0")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_50")).toBeNull();
  expect(screen.getByText("2500 samples")).toBeInTheDocument();
});

test("filtering still works and reports the filtered total", () => {
  renderTable(makeResult(2500));
  fireEvent.click(screen.getByRole("radio", { name: "Only A" }));
  expect(renderedRowCount()).toBeLessThanOrEqual(50);
  expect(screen.getByTestId("sample-difference-row-rec_1")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_0")).toBeNull();
  expect(screen.queryByTestId("sample-difference-row-rec_2")).toBeNull();
  expect(screen.getByText("625 samples")).toBeInTheDocument();
});

test("changing the filter returns to a valid first page", () => {
  renderTable(makeResult(2500));
  fireEvent.click(screen.getByTitle("2"));
  expect(screen.getByTestId("sample-difference-row-rec_50")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_0")).toBeNull();

  fireEvent.click(screen.getByRole("radio", { name: "Only A" }));
  // First a_only sample is on page 1 again; rec_201 (position 51) is not.
  expect(screen.getByTestId("sample-difference-row-rec_1")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_201")).toBeNull();
  expect(renderedRowCount()).toBeLessThanOrEqual(50);
});

test("drill-down actions still work after pagination", () => {
  renderTable(makeResult(2500));
  fireEvent.click(screen.getByTestId("open-sample-rec_0"));
  expect(screen.getByTestId("location-probe")).toHaveTextContent("/samples/rec_0");
});

test("View A / View B keep the run ids", () => {
  renderTable(makeResult(2500));
  fireEvent.click(screen.getByTestId("view-a-rec_0"));
  expect(screen.getByTestId("location-probe")).toHaveTextContent("/spectrum/rec_0?run=run_a_0");
});
