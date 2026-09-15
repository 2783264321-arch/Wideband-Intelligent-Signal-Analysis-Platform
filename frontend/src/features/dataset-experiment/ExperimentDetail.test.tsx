import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LocalizationProvider } from "../../localization/LocalizationProvider";
import { ExperimentDetail } from "./ExperimentDetail";

function localized(children: React.ReactNode) {
  return <LocalizationProvider initialLocale="en-US">{children}</LocalizationProvider>;
}


function experimentWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "exp_1",
    name: "Exp A",
    dataset_name: "spacenet",
    dataset_split: "test",
    dataset_label_space: "spacenet_14",
    recording_manifest_hash: "a".repeat(64),
    plugin_id: "dummy",
    plugin_version: "1.0",
    model_release_id: null,
    asset_manifest_sha256: null,
    parameters_json: {},
    executor: "local_cpu",
    evaluation_protocol: "physical_tf_detection_ap_v2",
    max_concurrency: 1,
    status: "running",
    dataset_evaluation_id: null,
    error_type: null,
    error_message: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    requested_execution_mode: "auto",
    auto_reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
    auto_reason: "Only local_cpu is runnable.",
    workload_class: "SMALL",
    expected_items: 3,
    queued_items: 1,
    running_items: 1,
    completed_items: 1,
    failed_items: 0,
    attempt_count: 2,
    ...overrides,
  };
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

test("renders status, executor, counters and auto provenance", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(experimentWire()))));
  render(
    localized(
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>
)
  );
  expect(await screen.findByTestId("experiment-progress-header")).toHaveTextContent("Running");
  const header = screen.getByTestId("experiment-progress-header");
  expect(header).toHaveTextContent("local_cpu");
  expect(header).toHaveTextContent("1 / 3");
  expect(header).toHaveTextContent("AUTO_ONLY_RUNNABLE_EXECUTOR");
});

test("a failed experiment shows the bounded error type", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(
    experimentWire({ status: "failed", error_type: "DATASET_EXPERIMENT_ORCHESTRATION_FAILED", error_message: "boom" }),
  ))));
  render(
    localized(
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>
)
  );
  const header = await screen.findByTestId("experiment-progress-header");
  expect(header).toHaveTextContent("Failed");
  expect(header).toHaveTextContent("DATASET_EXPERIMENT_ORCHESTRATION_FAILED");
});

test("polls while non-terminal and stops on a terminal status", async () => {
  vi.useFakeTimers();
  let calls = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    calls += 1;
    return new Response(JSON.stringify(experimentWire({ status: calls >= 2 ? "completed" : "running" })));
  }));
  render(
    localized(
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>
)
  );
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByTestId("experiment-progress-header")).toHaveTextContent("Running");

  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(screen.getByTestId("experiment-progress-header")).toHaveTextContent("Completed");

  const settled = calls;
  await act(async () => { vi.advanceTimersByTime(5000); });
  expect(calls).toBe(settled);
});

test("recovers from a transient polling error when a later poll succeeds", async () => {
  let calls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/items") || url.includes("/attempts")) {
      return new Response(JSON.stringify([]));
    }
    calls += 1;
    if (calls === 2) {
      return new Response(JSON.stringify({ error: { code: "BOOM", message: "transient" } }), { status: 503 });
    }
    return new Response(JSON.stringify(experimentWire({ status: "running" })));
  }));
  render(
    localized(
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>
)
  );
  await screen.findByTestId("experiment-progress-header");

  // A transient poll failure becomes visible.
  await waitFor(() => expect(screen.getByText(/BOOM: transient/)).toBeInTheDocument(), { timeout: 3000 });
  await waitFor(() => expect(screen.queryByTestId("experiment-progress-header")).not.toBeInTheDocument(), { timeout: 3000 });

  // A later successful poll clears the error and the detail UI is visible again.
  await waitFor(() => expect(screen.getByTestId("experiment-progress-header")).toBeInTheDocument(), { timeout: 3000 });
});


const linkedEvaluationWire = {
  id: "eval_1", name: "Eval", dataset_name: "spacenet", dataset_split: "test", label_space: "spacenet_14",
  pipeline_id: "dummy", pipeline_version: "1.0", status: "failed",
  expected_recordings: 3, evaluated_recordings: 3, missing_recordings: 0, coverage: 1,
  comparable: true, recording_manifest_hash: "a".repeat(64), evaluation_protocol: "physical_tf_detection_ap_v2",
  protocol_config_json: {}, aggregate_metrics_json: null, per_class_metrics_json: null, confusion_json: null,
  progress_stage: null, progress_current: null, progress_total: null, error_type: "BENCHMARK_FAILED", error_message: "boom",
  created_at: null, started_at: null, completed_at: null,
};

test("retry evaluation restarts the detail workflow from backend state", async () => {
  let experimentStatus = "failed";
  let retryCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/items") || url.includes("/attempts")) return new Response(JSON.stringify([]));
    if (url.includes("/retry-evaluation")) {
      retryCalls += 1;
      experimentStatus = "evaluating";
      return new Response(JSON.stringify(experimentWire({ status: "evaluating", dataset_evaluation_id: "eval_1" })));
    }
    if (url.includes("/api/dataset-benchmarks/")) return new Response(JSON.stringify(linkedEvaluationWire));
    if (url.includes("/api/dataset-experiments/exp_1")) {
      return new Response(JSON.stringify(experimentWire({
        status: experimentStatus,
        dataset_evaluation_id: "eval_1",
        queued_items: 0,
        running_items: 0,
        completed_items: 3,
        failed_items: 0,
      })));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    localized(
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>
)
  );
  expect(await screen.findByTestId("experiment-progress-header")).toHaveTextContent("Failed");

  fireEvent.click(screen.getByRole("tab", { name: "Evaluation" }));
  const retry = await screen.findByRole("button", { name: "Retry Evaluation" });
  fireEvent.click(retry);

  await waitFor(() => expect(retryCalls).toBe(1));
  await waitFor(() => expect(screen.getByTestId("experiment-progress-header")).toHaveTextContent("Evaluating"));
});
