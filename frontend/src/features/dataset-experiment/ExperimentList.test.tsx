import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentList } from "./ExperimentList";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

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
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentList />
      </MemoryRouter>,
    ),
  );
  expect(await screen.findByText("Exp A")).toBeInTheDocument();
  expect(screen.getByText(/spacenet \/ test/)).toBeInTheDocument();
  expect(screen.getByText(/local_cpu/)).toBeInTheDocument();
});

test("shows an empty state when there are no experiments", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentList />
      </MemoryRouter>,
    ),
  );
  expect(await screen.findByText(/No dataset experiments/i)).toBeInTheDocument();
});

test("surfaces a bounded platform error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ error: { code: "DATASET_EXPERIMENT_NOT_FOUND", message: "nope", details: {} } }),
    { status: 404 },
  )));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentList />
      </MemoryRouter>,
    ),
  );
  await waitFor(() => expect(screen.getByText(/DATASET_EXPERIMENT_NOT_FOUND/)).toBeInTheDocument());
});

// ---------------------------------------------------------------------------
// F3.5 — Run / Retry Failed lifecycle (exact gating)
// ---------------------------------------------------------------------------

function actionSetup(experiment: Record<string, unknown>) {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(`${options?.method ?? "GET"} ${url}`);
    if (url.endsWith("/api/dataset-experiments") && (options?.method ?? "GET") === "GET") {
      return new Response(JSON.stringify([experiment]));
    }
    return new Response(JSON.stringify(experiment));
  }));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentList />
      </MemoryRouter>,
    ),
  );
  return { urls };
}

test("a pending experiment exposes Run and starts it", async () => {
  const { urls } = actionSetup(experimentWire({ status: "pending" }));
  await screen.findByText("Exp A");
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await waitFor(() => expect(urls.some((u) => u.includes("/api/dataset-experiments/exp_1/run"))).toBe(true));
});

test("completed_with_failures exposes Retry Failed and triggers it", async () => {
  const { urls } = actionSetup(experimentWire({ status: "completed_with_failures", failed_items: 1, completed_items: 2 }));
  await screen.findByText("Exp A");
  fireEvent.click(screen.getByRole("button", { name: "Retry Failed" }));
  await waitFor(() => expect(urls.some((u) => u.includes("/api/dataset-experiments/exp_1/retry-failed"))).toBe(true));
});

test("a generic failed experiment exposes neither Run nor Retry Failed", async () => {
  const { urls } = actionSetup(experimentWire({ status: "failed", failed_items: 1 }));
  await screen.findByText("Exp A");
  expect(screen.queryByRole("button", { name: "Retry Failed" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  expect(urls.some((u) => u.includes("/retry-failed"))).toBe(false);
});
