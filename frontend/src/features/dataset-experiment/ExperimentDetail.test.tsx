import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentDetail } from "./ExperimentDetail";

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
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>,
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
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>,
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
    <MemoryRouter>
      <ExperimentDetail experimentId="exp_1" />
    </MemoryRouter>,
  );
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByTestId("experiment-progress-header")).toHaveTextContent("Running");

  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(screen.getByTestId("experiment-progress-header")).toHaveTextContent("Completed");

  const settled = calls;
  await act(async () => { vi.advanceTimersByTime(5000); });
  expect(calls).toBe(settled);
});
