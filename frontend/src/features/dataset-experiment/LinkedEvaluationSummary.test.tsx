import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LinkedEvaluationSummary } from "./LinkedEvaluationSummary";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import type { DatasetExperiment } from "../../api/types";

function experiment(overrides: Partial<DatasetExperiment> = {}): DatasetExperiment {
  return {
    id: "exp_1",
    name: "Exp A",
    datasetName: "spacenet",
    datasetSplit: "test",
    datasetLabelSpace: "spacenet_14",
    datasetProjectionId: null,
    datasetId: null,
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
    renderWithLocalization(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment()} />
    </MemoryRouter>,
    ),
  );
  const summary = await screen.findByTestId("linked-evaluation-summary");
  await waitFor(() => expect(summary).toHaveTextContent("eval_1"));
  expect(summary).toHaveTextContent("Failed");
  expect(summary).toHaveTextContent("3 / 3");
  expect(summary).toHaveTextContent("BENCHMARK_FAILED");

  fireEvent.click(screen.getByRole("button", { name: "Retry Evaluation" }));
  await waitFor(() => expect(urls.some((u) => u.includes("/api/dataset-experiments/exp_1/retry-evaluation"))).toBe(true));
});

test("Retry Evaluation is disabled while the linked evaluation is not failed/interrupted", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(evaluationWire({ status: "completed", error_type: null, error_message: null })))));
  render(
    renderWithLocalization(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment()} />
    </MemoryRouter>,
    ),
  );
  const summary = await screen.findByTestId("linked-evaluation-summary");
  await waitFor(() => expect(summary).toHaveTextContent("Completed"));
  expect(screen.queryByRole("button", { name: "Retry Evaluation" })).toBeNull();
});

test("renders nothing when there is no linked evaluation", () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(evaluationWire()))));
  render(
    renderWithLocalization(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment({ datasetEvaluationId: null })} />
    </MemoryRouter>,
    ),
  );
  expect(screen.queryByTestId("linked-evaluation-summary")).toBeNull();
});

test("a successful retry notifies the parent with the backend-returned experiment", async () => {
  const retryWire = {
    ...(() => {
      const base = experiment();
      return {
        id: base.id, name: base.name, dataset_name: base.datasetName, dataset_split: base.datasetSplit,
        dataset_label_space: base.datasetLabelSpace, recording_manifest_hash: base.recordingManifestHash,
        plugin_id: base.pluginId, plugin_version: base.pluginVersion, model_release_id: null, asset_manifest_sha256: null,
        parameters_json: {}, executor: base.executor, evaluation_protocol: base.evaluationProtocol,
        max_concurrency: base.maxConcurrency, status: "evaluating", dataset_evaluation_id: "eval_1",
        error_type: null, error_message: null, created_at: null, started_at: null, completed_at: null,
        requested_execution_mode: "auto", auto_reason_code: null, auto_reason: null, workload_class: null,
        expected_items: 3, queued_items: 0, running_items: 0, completed_items: 3, failed_items: 0, attempt_count: 3,
      };
    })(),
  };
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(`${options?.method ?? "GET"} ${url}`);
    if (url.includes("/retry-evaluation")) return new Response(JSON.stringify(retryWire));
    return new Response(JSON.stringify(evaluationWire()));
  }));
  const onRetryAccepted = vi.fn();
  render(
    renderWithLocalization(
    <MemoryRouter>
      <LinkedEvaluationSummary experiment={experiment()} onRetryAccepted={onRetryAccepted} />
    </MemoryRouter>,
    ),
  );
  await screen.findByTestId("linked-evaluation-summary");
  fireEvent.click(screen.getByRole("button", { name: "Retry Evaluation" }));
  await waitFor(() => expect(onRetryAccepted).toHaveBeenCalledTimes(1));
  expect(onRetryAccepted.mock.calls[0][0].status).toBe("evaluating");
  expect(urls.some((u) => u.includes("/api/dataset-experiments/exp_1/retry-evaluation"))).toBe(true);
});

// ---------------------------------------------------------------------------
// Review corrective A1 — bind linked evaluation to the experiment lifecycle
// ---------------------------------------------------------------------------

test("refetches the linked evaluation when the experiment lifecycle changes (same id)", async () => {
  let evalStatus = "failed";
  let benchmarkFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/dataset-benchmarks/")) {
      benchmarkFetches += 1;
      return new Response(JSON.stringify(evaluationWire({
        status: evalStatus,
        error_type: evalStatus === "failed" ? "BENCHMARK_FAILED" : null,
        error_message: null,
      })));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));

  const { rerender } = render(
    renderWithLocalization(
      <MemoryRouter><LinkedEvaluationSummary experiment={experiment({ status: "failed" })} /></MemoryRouter>,
    ),
  );
  await waitFor(() => expect(screen.getByTestId("linked-evaluation-summary")).toHaveTextContent("BENCHMARK_FAILED"));
  const before = benchmarkFetches;

  evalStatus = "completed";
  rerender(
    renderWithLocalization(
      <MemoryRouter><LinkedEvaluationSummary experiment={experiment({ status: "evaluating" })} /></MemoryRouter>,
    ),
  );
  await waitFor(() => expect(benchmarkFetches).toBeGreaterThan(before));
  await waitFor(() => expect(screen.getByTestId("linked-evaluation-summary")).toHaveTextContent("Completed"));
});

test("a lifecycle change clears a transient linked-evaluation error on success", async () => {
  let failOnce = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/dataset-benchmarks/")) {
      if (failOnce) {
        failOnce = false;
        return new Response(JSON.stringify({ error: { code: "BOOM", message: "transient" } }), { status: 503 });
      }
      return new Response(JSON.stringify(evaluationWire({ status: "completed", error_type: null, error_message: null })));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));

  const { rerender } = render(
    renderWithLocalization(
      <MemoryRouter><LinkedEvaluationSummary experiment={experiment({ status: "failed" })} /></MemoryRouter>,
    ),
  );
  await waitFor(() => expect(screen.getByText(/BOOM: transient/)).toBeInTheDocument());

  rerender(
    renderWithLocalization(
      <MemoryRouter><LinkedEvaluationSummary experiment={experiment({ status: "evaluating" })} /></MemoryRouter>,
    ),
  );
  await waitFor(() => expect(screen.getByTestId("linked-evaluation-summary")).toHaveTextContent("Completed"));
});

// ---------------------------------------------------------------------------
// Review corrective C — zh-CN ordinary error shell + raw identity preserved
// ---------------------------------------------------------------------------

test("localizes the linked-evaluation error shell in zh-CN and preserves the raw backend identity", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: { code: "BOOM", message: "transient" } }),
    { status: 503 },
  )));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <LinkedEvaluationSummary experiment={experiment()} />
      </MemoryRouter>,
      { locale: "zh-CN" },
    ),
  );
  expect(await screen.findByText("无法加载关联评测")).toBeInTheDocument();
  expect(screen.queryByText("Unable to load linked evaluation")).not.toBeInTheDocument();
  expect(screen.getByText(/BOOM: transient/)).toBeInTheDocument();
});

test("localizes a known linked-evaluation status in zh-CN", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(evaluationWire({
    status: "interrupted",
    error_type: "BENCHMARK_INTERRUPTED",
    error_message: "stopped",
  })))));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <LinkedEvaluationSummary experiment={experiment()} />
      </MemoryRouter>,
      { locale: "zh-CN" },
    ),
  );
  const summary = await screen.findByTestId("linked-evaluation-summary");
  await waitFor(() => expect(summary).toHaveTextContent("已中断"));
  expect(summary).toHaveTextContent("BENCHMARK_INTERRUPTED");
});

test("a completed analysis without complete GT shows the Evaluation-unavailable state", () => {
  render(
    renderWithLocalization(
      <MemoryRouter>
        <LinkedEvaluationSummary experiment={experiment({ status: "completed", datasetEvaluationId: null })} />
      </MemoryRouter>,
    ),
  );
  expect(screen.getByTestId("evaluation-unavailable")).toHaveTextContent(/complete Ground Truth/i);
  expect(screen.queryByTestId("linked-evaluation-summary")).toBeNull();
});

test("a running analysis without evaluation renders nothing (no misleading panel)", () => {
  const { container } = render(
    renderWithLocalization(
      <MemoryRouter>
        <LinkedEvaluationSummary experiment={experiment({ status: "running", datasetEvaluationId: null })} />
      </MemoryRouter>,
    ),
  );
  expect(container).toBeEmptyDOMElement();
});
