import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DatasetDetailPage } from "../../pages/DatasetDetailPage";
import { DatasetEvaluationComparePanel } from "./DatasetEvaluationComparePanel";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

const datasetWire = {
  id: "ds_1",
  name: "SpaceNet",
  split: "test",
  adapter_id: "spacenet",
  label_space: "signal_presence_v1",
  local_root: "D:\\SpaceNet",
  portable_fingerprint: "a".repeat(64),
  sample_count: 4,
  ground_truth_sample_count: 4,
  created_at: "2026-09-17T00:00:00",
};

function aggregateWire(ap50: number, ap5095: number, precision: number, recall: number, f1: number) {
  return {
    classification_applicable: true,
    classification_reason: null,
    localization: { ap50, ap50_95: ap5095, operating: { tp: 3, fp: 1, fn: 1, precision, recall, f1 } },
    classification_on_matched: { matched_count: 3, class_correct: 3, class_wrong: 0, matched_accuracy: 1 },
    class_aware: null,
  };
}

const aggregateA = aggregateWire(0.5, 0.4, 0.8, 0.7, 0.75);
const aggregateB = aggregateWire(0.4, 0.25, 0.6, 0.5, 0.55);

function evaluationWire(id: string, name: string, aggregate: unknown) {
  return {
    id,
    name,
    dataset_name: "SpaceNet",
    dataset_split: "test",
    label_space: "signal_presence_v1",
    dataset_projection_id: null,
    dataset_id: "ds_1",
    pipeline_id: "stft_energy_detector",
    pipeline_version: "1.0",
    status: "completed",
    expected_recordings: 4,
    evaluated_recordings: 4,
    missing_recordings: 0,
    coverage: 1,
    comparable: true,
    recording_manifest_hash: "a".repeat(64),
    evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: {},
    aggregate_metrics_json: aggregate,
    per_class_metrics_json: [],
    confusion_json: [],
    progress_stage: null,
    progress_current: null,
    progress_total: null,
    error_type: null,
    error_message: null,
    created_at: null,
    completed_at: null,
  };
}

const evalA = evaluationWire("eval_a", "Pipeline A run", aggregateA);
const evalB = evaluationWire("eval_b", "Pipeline B run", aggregateB);

function experimentWire(id: string, name: string, evaluationId: string | null) {
  return {
    id,
    name,
    dataset_name: "SpaceNet",
    dataset_split: "test",
    dataset_label_space: "signal_presence_v1",
    dataset_projection_id: null,
    dataset_id: "ds_1",
    recording_manifest_hash: "a".repeat(64),
    plugin_id: "stft_energy_detector",
    plugin_version: "1.0",
    model_release_id: null,
    asset_manifest_sha256: null,
    parameters_json: {},
    executor: "local_cpu",
    evaluation_protocol: "physical_tf_detection_ap_v2",
    max_concurrency: 1,
    status: "completed",
    dataset_evaluation_id: evaluationId,
    error_type: null,
    error_message: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    requested_execution_mode: "auto",
    auto_reason_code: null,
    auto_reason: null,
    workload_class: null,
    expected_items: 4,
    queued_items: 0,
    running_items: 0,
    completed_items: 4,
    failed_items: 0,
    attempt_count: 1,
  };
}

const expA = experimentWire("exp_a", "Pipeline A run", "eval_a");
const expB = experimentWire("exp_b", "Pipeline B run", "eval_b");
const expNoGt = experimentWire("exp_nogt", "No GT run", null);

const compareWire = {
  comparable: true,
  reasons: [],
  evaluation_a_id: "eval_a",
  evaluation_b_id: "eval_b",
  aggregate_a: aggregateA,
  aggregate_b: aggregateB,
  deltas: { localization_ap50: -0.1, localization_ap50_95: -0.15 },
  recordings: [
    { recording_id: "rec_1", recording_name: "a1", evaluation_a_run_id: "run_a1", evaluation_b_run_id: "run_b1", comparison: "both_detected" },
    { recording_id: "rec_2", recording_name: "a2", evaluation_a_run_id: "run_a2", evaluation_b_run_id: null, comparison: "a_only" },
    { recording_id: "rec_3", recording_name: "a3", evaluation_a_run_id: null, evaluation_b_run_id: "run_b3", comparison: "b_only" },
    { recording_id: "rec_4", recording_name: "a4", evaluation_a_run_id: "run_a4", evaluation_b_run_id: "run_b4", comparison: "both_missed" },
  ],
};

let compareWireOverride: Record<string, unknown> | null = null;

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status });
}

function route(url: string, init?: RequestInit): Response {
  const u = String(url);
  if (u.includes("/api/dataset-benchmarks/compare")) return json(compareWireOverride ?? compareWire);
  if (u.includes("/api/dataset-benchmarks/")) {
    return json(u.endsWith("eval_b") ? evalB : evalA);
  }
  if (u.includes("/api/dataset-benchmarks")) return json([evalA, evalB]);
  if (u.includes("/api/dataset-experiments")) return json([expA, expB, expNoGt]);
  if (u.includes("/api/datasets/ds_1")) return json(datasetWire);
  return json([]);
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderPage() {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library/datasets/ds_1"]}>
        <Routes>
          <Route path="/data-library/datasets/:datasetId" element={<DatasetDetailPage />} />
          <Route path="/samples/:recordingId" element={<LocationProbe />} />
          <Route path="/spectrum/:recordingId" element={<LocationProbe />} />
          <Route path="/experiments/:experimentId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

function fetchCalls(): unknown[][] {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
}

async function openAnalyses() {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analyses" }));
  await screen.findAllByTestId("dataset-analysis-item");
}

async function selectPairAndCompare() {
  fireEvent.click(await screen.findByLabelText("Select Pipeline A run for comparison"));
  fireEvent.click(await screen.findByLabelText("Select Pipeline B run for comparison"));
  fireEvent.click(screen.getByTestId("compare-evaluations-button"));
  await screen.findByTestId("sample-differences");
}

beforeEach(() => {
  compareWireOverride = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

test("dataset Analyses requests dataset-scoped analyses and evaluations", async () => {
  await openAnalyses();
  const calls = fetchCalls().map((call) => String(call[0]));
  expect(calls.some((url) => url.includes("/api/dataset-experiments?dataset_id=ds_1"))).toBe(true);
  expect(calls.some((url) => url.includes("/api/dataset-benchmarks?dataset_id=ds_1"))).toBe(true);
});

test("a completed linked evaluation opens the Dataset Evaluation view reusing EvaluationMetricsView", async () => {
  await openAnalyses();
  fireEvent.click(await screen.findByTestId("view-evaluation-exp_a"));
  const view = await screen.findByTestId("dataset-evaluation-view");
  const summary = screen.getByTestId("dataset-evaluation-summary");
  expect(summary).toHaveTextContent("mAP@0.5");
  expect(summary).toHaveTextContent("mAP@0.5:0.95");
  expect(summary).toHaveTextContent("Precision");
  expect(summary).toHaveTextContent("Recall");
  expect(summary).toHaveTextContent("F1");
  expect(summary).toHaveTextContent("0.5");
  expect(screen.getByTestId("dataset-evaluation-coverage")).toHaveTextContent("4 / 4");
  // The shared metrics view is reused, not reimplemented.
  expect(await screen.findByTestId("evaluation-metrics-view")).toBeInTheDocument();
  expect(view).toContainElement(screen.getByTestId("evaluation-metrics-view"));
});

test("two evaluations can be selected and comparison uses their ids and renders metrics", async () => {
  await openAnalyses();
  await selectPairAndCompare();
  const compareCall = fetchCalls().find((call) => String(call[0]).includes("/api/dataset-benchmarks/compare"));
  expect(compareCall).toBeDefined();
  expect(JSON.parse(String((compareCall![1] as RequestInit).body))).toMatchObject({
    evaluation_a_id: "eval_a",
    evaluation_b_id: "eval_b",
  });
  const panel = screen.getByTestId("pipeline-compare");
  expect(panel).toHaveTextContent("Pipeline A");
  expect(panel).toHaveTextContent("Pipeline B");
  expect(panel).toHaveTextContent("0.5");
  expect(panel).toHaveTextContent("0.4");
  expect(panel).toHaveTextContent("0.75");
});

test("sample differences renders all four comparison states", async () => {
  await openAnalyses();
  await selectPairAndCompare();
  const table = screen.getByTestId("sample-differences");
  expect(within(table).getByTestId("sample-difference-state-rec_1")).toHaveTextContent("Both detected");
  expect(within(table).getByTestId("sample-difference-state-rec_2")).toHaveTextContent("Only A");
  expect(within(table).getByTestId("sample-difference-state-rec_3")).toHaveTextContent("Only B");
  expect(within(table).getByTestId("sample-difference-state-rec_4")).toHaveTextContent("Both missed");
  expect(within(table).getByTestId("sample-difference-row-rec_1")).toHaveTextContent("a1");
});

test("A-only and B-only filtering isolates the matching samples", async () => {
  await openAnalyses();
  await selectPairAndCompare();

  fireEvent.click(screen.getByRole("radio", { name: "Only A" }));
  expect(screen.getByTestId("sample-difference-row-rec_2")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_1")).toBeNull();
  expect(screen.queryByTestId("sample-difference-row-rec_3")).toBeNull();

  fireEvent.click(screen.getByRole("radio", { name: "Only B" }));
  expect(screen.getByTestId("sample-difference-row-rec_3")).toBeInTheDocument();
  expect(screen.queryByTestId("sample-difference-row-rec_2")).toBeNull();

  fireEvent.click(screen.getByRole("radio", { name: "All" }));
  expect(screen.getByTestId("sample-difference-row-rec_1")).toBeInTheDocument();
});

test("null run ids disable only the corresponding View action", async () => {
  await openAnalyses();
  await selectPairAndCompare();
  // a_only has no B run; b_only has no A run.
  expect(within(screen.getByTestId("sample-difference-actions-rec_2")).getByTestId("view-b-rec_2")).toBeDisabled();
  expect(within(screen.getByTestId("sample-difference-actions-rec_2")).getByTestId("view-a-rec_2")).not.toBeDisabled();
  expect(within(screen.getByTestId("sample-difference-actions-rec_3")).getByTestId("view-a-rec_3")).toBeDisabled();
  expect(within(screen.getByTestId("sample-difference-actions-rec_3")).getByTestId("view-b-rec_3")).not.toBeDisabled();
});

test("Open Sample, View A and View B drill into the correct routes", async () => {
  await openAnalyses();
  await selectPairAndCompare();

  fireEvent.click(within(screen.getByTestId("sample-difference-actions-rec_1")).getByTestId("open-sample-rec_1"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/samples/rec_1");
});

test("View A opens the spectrum workspace with the A run id", async () => {
  await openAnalyses();
  await selectPairAndCompare();
  fireEvent.click(within(screen.getByTestId("sample-difference-actions-rec_1")).getByTestId("view-a-rec_1"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/spectrum/rec_1?run=run_a1");
});

test("View B opens the spectrum workspace with the B run id", async () => {
  await openAnalyses();
  await selectPairAndCompare();
  fireEvent.click(within(screen.getByTestId("sample-difference-actions-rec_1")).getByTestId("view-b-rec_1"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/spectrum/rec_1?run=run_b1");
});

test("a non-comparable response shows an informational alert", async () => {
  compareWireOverride = { ...compareWire, comparable: false, reasons: ["dataset_id_mismatch"], recordings: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/api/dataset-benchmarks/compare")) {
      return json(compareWireOverride);
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <DatasetEvaluationComparePanel evaluationAId="eval_a" evaluationBId="eval_b" onBack={() => undefined} />
      </MemoryRouter>,
    ),
  );
  const alert = await screen.findByTestId("pipeline-compare-not-comparable");
  expect(alert).toHaveTextContent("cannot be compared");
  expect(alert).toHaveTextContent("dataset_id_mismatch");
  expect(screen.queryByTestId("sample-differences")).toBeNull();
});

test("an analysis without an evaluation remains valid and offers no View Evaluation", async () => {
  await openAnalyses();
  expect(screen.getAllByTestId("dataset-analysis-item")).toHaveLength(3);
  expect(screen.getByText("No GT run")).toBeInTheDocument();
  expect(screen.queryByTestId("view-evaluation-exp_nogt")).toBeNull();
  expect(screen.queryByText(/Unable/)).toBeNull();
});
