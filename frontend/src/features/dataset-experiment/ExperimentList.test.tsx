import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentList } from "./ExperimentList";

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
    status: "completed",
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
    queued_items: 0,
    running_items: 0,
    completed_items: 3,
    failed_items: 0,
    attempt_count: 3,
    ...overrides,
  };
}

afterEach(() => { vi.unstubAllGlobals(); });

test("renders experiments from the backend", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([experimentWire()]))));
  render(
    <MemoryRouter>
      <ExperimentList />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Exp A")).toBeInTheDocument();
  expect(screen.getByText(/spacenet \/ test/)).toBeInTheDocument();
  expect(screen.getByText(/local_cpu/)).toBeInTheDocument();
});

test("shows an empty state when there are no experiments", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(
    <MemoryRouter>
      <ExperimentList />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/No dataset experiments/i)).toBeInTheDocument();
});

test("surfaces a bounded platform error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: { code: "DATASET_EXPERIMENT_NOT_FOUND", message: "nope", details: {} } }),
    { status: 404 },
  )));
  render(
    <MemoryRouter>
      <ExperimentList />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/DATASET_EXPERIMENT_NOT_FOUND/)).toBeInTheDocument());
});
