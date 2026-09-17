import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DatasetDetailPage } from "./DatasetDetailPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const datasetWire = {
  id: "ds_1",
  name: "SpaceNet",
  split: "test",
  adapter_id: "spacenet",
  label_space: "spacenet_14",
  local_root: "D:\\SpaceNet",
  portable_fingerprint: "a".repeat(64),
  sample_count: 2500,
  ground_truth_sample_count: 2500,
  created_at: "2026-09-17T00:00:00",
};

const sampleWire = {
  id: "rec_1",
  name: "a1",
  sample_key: "a1",
  data_format: "float16_interleaved_le",
  sample_rate_hz: 1e6,
  center_frequency_hz: 0,
  frequency_low_hz: -5e5,
  frequency_high_hz: 5e5,
  num_samples: 1000,
  duration_s: 0.001,
  has_ground_truth: true,
  analysis_count: 0,
};

const pipelineWire = {
  id: "stft_energy_detector",
  name: "STFT Energy Detector",
  version: "1.0",
  label_space: "signal_presence_v1",
  recommended_device: "CPU",
  cpu_supported: true,
  executors_supported: ["local_cpu"],
  recommended_executor: null,
  stages: [],
  inspectable_stages: [],
  task_capability: "detection_localization",
};

const selectionWire = {
  requested_mode: "auto",
  resolved_executor: "local_cpu",
  reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
  reason: "Only local_cpu is runnable.",
  workload_class: "SMALL",
  candidates: [{
    executor: "local_cpu", technical: true, configured: true, certified: true,
    available: true, reason_code: null, reason_message: null,
  }],
};

const experimentWire = {
  id: "exp_1",
  name: "SpaceNet · STFT Energy Detector",
  dataset_name: "SpaceNet",
  dataset_split: "test",
  dataset_label_space: "spacenet_14",
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
  status: "running",
  dataset_evaluation_id: null,
  error_type: null,
  error_message: null,
  created_at: null,
  started_at: null,
  completed_at: null,
  requested_execution_mode: "auto",
  auto_reason_code: null,
  auto_reason: null,
  workload_class: null,
  expected_items: 2500,
  queued_items: 2499,
  running_items: 1,
  completed_items: 0,
  failed_items: 0,
  attempt_count: 1,
};

let deleteMode: "ok" | "blocked" = "ok";
const posted: Record<string, unknown>[] = [];

function route(url: string, init?: RequestInit): Response {
  const method = init?.method ?? "GET";
  if (method === "DELETE" && url.includes("/api/datasets/ds_1")) {
    if (deleteMode === "blocked") {
      return new Response(JSON.stringify({
        error: {
          code: "DATASET_REMOVE_BLOCKED", message: "blocked",
          details: { blockers: [{ kind: "active_analysis_run", resource_id: "run_1", reference: "analysis_run" }] },
        },
      }), { status: 409 });
    }
    return new Response(null, { status: 204 });
  }
  if (url.includes("/api/dataset-experiments/exp_1/run")) {
    return new Response(JSON.stringify(experimentWire), { status: 202 });
  }
  if (url.includes("/api/dataset-experiments") && method === "POST") {
    posted.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    return new Response(JSON.stringify(experimentWire), { status: 201 });
  }
  if (url.includes("/api/dataset-experiments")) {
    return new Response(JSON.stringify([experimentWire]), { status: 200 });
  }
  if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionWire), { status: 200 });
  if (url.includes("/api/pipelines")) return new Response(JSON.stringify([pipelineWire]), { status: 200 });
  if (url.includes("/samples")) {
    return new Response(JSON.stringify({ dataset_id: "ds_1", items: [sampleWire], total: 1 }), { status: 200 });
  }
  if (url.includes("/api/datasets/ds_1")) return new Response(JSON.stringify(datasetWire), { status: 200 });
  return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
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
          <Route path="/experiments/:experimentId" element={<LocationProbe />} />
          <Route path="/data-library" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  deleteMode = "ok";
  posted.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

function fetchCalls(): unknown[][] {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
}

test("overview shows first-class dataset metadata", async () => {
  renderPage();
  const overview = await screen.findByTestId("dataset-overview");
  expect(overview).toHaveTextContent("SpaceNet");
  expect(overview).toHaveTextContent("2500");
  expect(overview).toHaveTextContent("D:\\SpaceNet");
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/datasets/ds_1"))).toBe(true);
});

test("samples tab uses the first-class samples endpoint and opens the Sample page", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Samples" }));
  expect(await screen.findByText("a1")).toBeInTheDocument();
  const open = await screen.findByRole("button", { name: "Open Sample" });
  fireEvent.click(open);
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/samples/rec_1");
});

test("Analyses tab lists dataset analyses filtered by dataset_id", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analyses" }));
  const item = await screen.findByTestId("dataset-analysis-item");
  expect(item).toHaveTextContent("SpaceNet · STFT Energy Detector");
  expect(screen.getByTestId("dataset-analysis-progress")).toHaveTextContent("0 / 2500");
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/dataset-experiments?dataset_id=ds_1"))).toBe(true);
});

test("normal dataset UI uses Dataset Analysis wording, not projection/experiment terms", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analyses" }));
  expect(await screen.findByTestId("analyze-dataset-button")).toHaveTextContent("Analyze Dataset");
  expect(screen.queryByText(/Dataset Experiment/)).toBeNull();
  expect(screen.queryByText(/Projection/)).toBeNull();
});

test("Analyze Dataset modal defaults to STFT Energy + Auto · Local CPU and starts create then run", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analyses" }));
  fireEvent.click(await screen.findByTestId("analyze-dataset-button"));

  // Default pipeline and execution control.
  expect(await screen.findByTitle("STFT Energy Detector")).toBeInTheDocument();
  expect(await screen.findByText("Auto · Local CPU")).toBeInTheDocument();

  fireEvent.click(await screen.findByRole("button", { name: "Start Analysis" }));
  await screen.findByTestId("location-probe");
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    dataset_id: "ds_1",
    plugin_id: "stft_energy_detector",
    execution_mode: "auto",
  });
  const runCall = fetchCalls().find(
    (call) => String(call[0]).includes("/api/dataset-experiments/exp_1/run")
      && (call[1] as RequestInit | undefined)?.method === "POST",
  );
  expect(runCall).toBeDefined();
  expect(screen.getByTestId("location-probe")).toHaveTextContent("/experiments/exp_1");
});

test("Remove Dataset confirms external-file preservation and navigates back on success", async () => {
  renderPage();
  await screen.findByTestId("dataset-overview");
  fireEvent.click(screen.getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByText(/NOT deleted/)).toBeInTheDocument();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library");
});

test("blocked removal stays on the page and renders the blocker", async () => {
  deleteMode = "blocked";
  renderPage();
  await screen.findByTestId("dataset-overview");
  fireEvent.click(screen.getByRole("button", { name: "Remove Dataset" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("delete-conflict-alert")).toHaveTextContent("run_1");
});
