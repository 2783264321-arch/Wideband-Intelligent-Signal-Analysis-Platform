import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CompareDeltaTable } from "./CompareDeltaTable";
import type { DatasetBenchmarkCompareResult } from "../../api/types";

const result: DatasetBenchmarkCompareResult = {
  comparable: true,
  reasons: [],
  evaluationAId: "eval_a",
  evaluationBId: "eval_b",
  aggregateA: null,
  aggregateB: null,
  deltas: {
    localization_ap50: 0.07,
    localization_ap50_95: null,
    class_aware_map50: null,
    class_aware_map50_95: null,
    matched_accuracy: null,
  },
};

function itemWire(id: string, evaluationId: string, recordingId: string, runId: string | null) {
  return {
    id, evaluation_id: evaluationId, manifest_order: 0, recording_id: recordingId,
    recording_name: recordingId, analysis_run_id: runId, status: "included", gt_count: 1,
    prediction_count: 1, error_reason: null,
  };
}

afterEach(() => { vi.unstubAllGlobals(); });

test("renders deltas with N/A for null and a shared-recording drilldown link", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/eval_a/items")) {
      return new Response(JSON.stringify([itemWire("i1", "eval_a", "rec_1", "run_a")]));
    }
    if (url.includes("/eval_b/items")) {
      return new Response(JSON.stringify([itemWire("i2", "eval_b", "rec_1", "run_b")]));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));

  render(
    <MemoryRouter>
      <CompareDeltaTable result={result} />
    </MemoryRouter>,
  );

  const table = await screen.findByTestId("compare-delta-table");
  expect(table).toHaveTextContent("0.07");
  expect(table).toHaveTextContent("N/A");
  expect(table).not.toHaveTextContent("0 ");

  const link = await screen.findByRole("link", { name: /Algorithm Lab/i });
  expect(link.getAttribute("href")).toBe("/algorithm-lab?recording=rec_1&runA=run_a&runB=run_b");
});

test("a new result clears the stale shared-recording drilldown immediately", async () => {
  const r1: DatasetBenchmarkCompareResult = { ...result, evaluationAId: "eval_a", evaluationBId: "eval_b" };
  const r2: DatasetBenchmarkCompareResult = { ...result, evaluationAId: "eval_c", evaluationBId: "eval_d" };
  const item = (id: string, evaluationId: string, recordingId: string, runId: string) => ({
    id, evaluation_id: evaluationId, manifest_order: 0, recording_id: recordingId,
    recording_name: recordingId, analysis_run_id: runId, status: "included", gt_count: 1,
    prediction_count: 1, error_reason: null,
  });
  let deferred = false;
  let resolveDeferred: ((value: Response) => void) | undefined;
  const pending = new Promise<Response>((resolve) => { resolveDeferred = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (deferred) return pending;
    if (url.includes("/eval_a/items")) return new Response(JSON.stringify([item("i1", "eval_a", "rec_1", "run_a")]));
    if (url.includes("/eval_b/items")) return new Response(JSON.stringify([item("i2", "eval_b", "rec_1", "run_b")]));
    throw new Error(`Unexpected request: ${url}`);
  }));

  const { rerender } = render(<MemoryRouter><CompareDeltaTable result={r1} /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: /Algorithm Lab/i })).toBeInTheDocument();

  deferred = true;
  rerender(<MemoryRouter><CompareDeltaTable result={r2} /></MemoryRouter>);
  expect(screen.queryByRole("link", { name: /Algorithm Lab/i })).toBeNull();

  // Later authoritative R2 items resolve -> link may reappear.
  await act(async () => {
    resolveDeferred?.(new Response(JSON.stringify([])));
    await Promise.resolve();
  });
});
