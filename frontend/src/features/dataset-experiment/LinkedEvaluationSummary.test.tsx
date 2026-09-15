import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LinkedEvaluationSummary } from "./LinkedEvaluationSummary";
import type { DatasetExperiment } from "../../api/types";

function experiment(overrides: Partial<DatasetExperiment> = {}): DatasetExperiment {
  return {
    id: "exp_1",
    name: "Exp A",
    datasetName: "spacenet",
    datasetSplit: "test",
    datasetLabelSpace: "spacenet_14",
    recordingManifestHash: "a".repeat(64),
    pluginId: "dummy",
    pluginVersion: "1.0",
    modelReleaseId: null,
    assetManifestSha256: null,
    parameters: {},
    executor: "local_cpu",
    evaluationProtocol: "physical_tf_detection_ap_v2",
    maxConcurrency: 1,
    status: "failed",
    datasetEvaluationId: "eval_1",
    errorType: null,
    errorMessage: null,
    requestedExecutionMode: "auto",
    autoReasonCode: null,
    autoReason: null,
    workloadClass: null,
    expectedItems: 3,
    queuedItems: 0,
    runningItems: 0,
    completedItems: 3,
    failedItems: 0,
    attemptCount: 3,
    createdAt: null,
    startedAt: null,
    completedAt: null,
    ...overrides,
  };
}

function evaluationWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "eval_1",
    name: "Eval",
    dataset_name: "spacenet",
    dataset_split: "test",
    label_space: "spacenet_14",
    pipeline_id: "dummy",
    pipeline_version: "1.0",
    status: "failed",
    expected_recordings: 3,
    evaluated_recordings: 3,
    missing_recordings: 0,
    coverage: 1.0,
    comparable: true,
    recording_manifest_hash: "a".repeat(64),
    evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: {},
    aggregate_metrics_json: null,
    per_class_metrics_json: null,
    confusion_json: null,
    progress_stage: null,
    progress_current: null,
    progress_total: null,
    error_type: "BENCHMARK_FAILED",
    error_message: "boom",
    created_at: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

test("renders the linked evaluation summary and enables Retry Evaluation when eligible", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(`${options?.method ?? "GET"} ${url}`);
    if (url.includes("/retry-evaluation")) return new Response(JSON.stringify(experiment({ status: "evaluating" })));
    return new Response(JSON.stringify(evaluationWire()));
  }));
  render(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment()} />
    </MemoryRouter>,
  );
  const summary = await screen.findByTestId("linked-evaluation-summary");
  await waitFor(() => expect(summary).toHaveTextContent("eval_1"));
  expect(summary).toHaveTextContent("failed");
  expect(summary).toHaveTextContent("3 / 3");
  expect(summary).toHaveTextContent("BENCHMARK_FAILED");

  fireEvent.click(screen.getByRole("button", { name: "Retry Evaluation" }));
  await waitFor(() => expect(urls.some((u) => u.includes("/api/dataset-experiments/exp_1/retry-evaluation"))).toBe(true));
});

test("Retry Evaluation is disabled while the linked evaluation is not failed/interrupted", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(evaluationWire({ status: "completed", error_type: null, error_message: null })))));
  render(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment()} />
    </MemoryRouter>,
  );
  const summary = await screen.findByTestId("linked-evaluation-summary");
  await waitFor(() => expect(summary).toHaveTextContent("completed"));
  expect(screen.queryByRole("button", { name: "Retry Evaluation" })).toBeNull();
});

test("renders nothing when there is no linked evaluation", () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(evaluationWire()))));
  render(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment({ datasetEvaluationId: null })} />
    </MemoryRouter>,
  );
  expect(screen.queryByTestId("linked-evaluation-summary")).toBeNull();
});
