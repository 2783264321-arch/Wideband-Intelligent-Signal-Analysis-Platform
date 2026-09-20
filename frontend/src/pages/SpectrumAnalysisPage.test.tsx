import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SpectrumAnalysisPage } from "./SpectrumAnalysisPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const recording = {
  id: "rec_1",
  name: "Burst Demo",
  data_format: "complex64_le",
  sample_rate_hz: 1000000,
  center_frequency_hz: 2441000000,
  frequency_low_hz: 2440500000,
  frequency_high_hz: 2441500000,
  num_samples: 200000,
  duration_s: 0.2,
  dataset_name: null,
  dataset_split: null,
  label_space: "spacenet_14",
  has_ground_truth: false,
};

const spectrogram = {
  representation: "stft",
  image_url: "/media/spectrograms/key.png",
  t_start_s: 0.0,
  t_end_s: 0.2,
  f_low_hz: 2440500000,
  f_high_hz: 2441500000,
};

const localPipeline = {
  id: "dummy",
  name: "Dummy Pipeline",
  version: "1.0",
  label_space: "spacenet_14",
  recommended_device: "CPU",
  cpu_supported: true,
  executors_supported: ["local_cpu"],
  recommended_executor: "local_cpu",
  stages: [],
  inspectable_stages: [],
  task_capability: "classification",
};

const detectorPipeline = {
  id: "stft_energy_detector",
  name: "STFT Energy Detector",
  version: "1.0",
  label_space: "signal_presence_v1",
  recommended_device: "CPU",
  cpu_supported: true,
  executors_supported: ["local_cpu"],
  recommended_executor: "local_cpu",
  stages: [],
  inspectable_stages: [],
  task_capability: "detection_localization",
};

const remotePipeline = {
  id: "zoomspec_yolo26n_aug_combined_frn_v3",
  name: "ZoomSpec Frozen V3",
  version: "1.0.0",
  label_space: "spacenet_14",
  recommended_device: "GPU",
  cpu_supported: false,
  stages: [],
  inspectable_stages: [],
  task_capability: "detection_classification",
  executors_supported: ["remote_gpu"],
  recommended_executor: "remote_gpu",
};

const candidate = (
  executor: string,
  overrides: Record<string, unknown> = {},
) => ({
  executor,
  technical: true,
  configured: true,
  certified: true,
  available: true,
  reason_code: null,
  reason_message: null,
  ...overrides,
});

const selectionRemoteAvailable = {
  requested_mode: "auto",
  resolved_executor: "remote_gpu",
  reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
  reason: "Only remote_gpu is runnable.",
  workload_class: "SMALL",
  candidates: [candidate("remote_gpu")],
};

const selectionRemoteUnavailable = {
  requested_mode: "auto",
  resolved_executor: null,
  reason_code: "AUTO_NO_RUNNABLE_EXECUTOR",
  reason: "No runnable executor.",
  workload_class: "UNKNOWN",
  candidates: [
    candidate("remote_gpu", {
      available: false,
      reason_code: "REMOTE_TRANSPORT_UNAVAILABLE",
      reason_message: "Remote GPU executor is unavailable.",
    }),
  ],
};

const selectionLocalAvailable = {
  requested_mode: "auto",
  resolved_executor: "local_cpu",
  reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
  reason: "Only local_cpu is runnable.",
  workload_class: "SMALL",
  candidates: [candidate("local_cpu")],
};

const selectionDual = {
  requested_mode: "auto",
  resolved_executor: "local_cpu",
  reason_code: "AUTO_LOCAL_CPU_PREFERRED",
  reason: "Local CPU preferred for this workload.",
  workload_class: "SMALL",
  candidates: [candidate("local_cpu"), candidate("local_gpu")],
};

function runWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "run_1",
    recording_id: "rec_1",
    pipeline_id: "dummy",
    pipeline_version: "1.0",
    executor: "local_cpu",
    status: "running",
    parameters_json: {},
    hardware_info_json: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    worker_pid: 1,
    created_at: "2026-09-05T00:00:00",
    execution_metadata_json: null,
    ...overrides,
  };
}

interface SetupOptions {
  pipelines?: unknown[];
  selection?: unknown;
  selectionDeferredFor?: string;
  runFixture?: Record<string, unknown>;
  readbackFixture?: Record<string, unknown>;
  initialPath?: string;
  locale?: "zh-CN" | "en-US";
}

function setup(options: SetupOptions = {}) {
  const posted: Record<string, unknown>[] = [];
  const selectionCalls: string[] = [];
  let resolveDeferred: ((value: Response) => void) | undefined;
  const deferred = new Promise<Response>((resolve) => { resolveDeferred = resolve; });
  const {
    pipelines = [remotePipeline],
    selection = selectionRemoteAvailable,
    selectionDeferredFor,
    runFixture = runWire(),
    readbackFixture = runWire({ status: "completed" }),
    initialPath = "/spectrum/rec_1",
    locale = "en-US",
    recordingFixture = recording,
    spectrogramHandler,
  } = options;

  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recordingFixture));
    if (url.includes("/spectrogram")) {
      return spectrogramHandler ? spectrogramHandler(url) : new Response(JSON.stringify(spectrogram));
    }
    if (url.includes("/api/executor-selection")) {
      const pipelineId = new URL(url, "http://x").searchParams.get("pipeline_id") ?? "";
      selectionCalls.push(pipelineId);
      if (selectionDeferredFor !== undefined && pipelineId === selectionDeferredFor) return deferred;
      return new Response(JSON.stringify(selection));
    }
    if (url.includes("/api/executor-availability")) {
      throw new Error("executor-availability must not be called by the spectrum page");
    }
    if (url.endsWith("/api/analysis-runs") && fetchOptions?.method === "POST") {
      posted.push(JSON.parse(String(fetchOptions.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(runFixture), { status: 201 });
    }
    if (url.endsWith("/api/analysis-runs/run_1")) return new Response(JSON.stringify(readbackFixture));
    if (url.endsWith("/api/analysis-runs/run_1/detections")) return new Response(JSON.stringify([]));
    if (url.endsWith("/api/analysis-runs/run_r")) return new Response(JSON.stringify(readbackFixture));
    if (url.endsWith("/api/analysis-runs/run_r/detections")) return new Response(JSON.stringify([]));
    throw new Error(`Unexpected request: ${url}`);
  }));

  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
          <Route path="/samples/:recordingId" element={<LocationProbe />} />
          <Route path="/data-library" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
      { locale },
    ),
  );
  return { posted, selectionCalls, resolveDeferred: () => resolveDeferred };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

test("the workspace offers back-to-library and back-to-sample", async () => {
  setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");

  fireEvent.click(screen.getByTestId("spectrum-back"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/samples/rec_1");
});

test("back-to-library targets the Standalone tab for a standalone sample", async () => {
  setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");

  fireEvent.click(screen.getByTestId("spectrum-back-library"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/data-library?tab=standalone",
  );
});

test("the spectrum page resolves execution internally and never shows an environment selector", async () => {
  const { selectionCalls } = setup({ selection: selectionLocalAvailable, pipelines: [localPipeline, detectorPipeline] });
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(selectionCalls.length).toBeGreaterThan(0));
  expect(screen.queryByTestId("execution-environment-selector")).toBeNull();
  expect(screen.queryByTestId("execution-environment-select")).toBeNull();
});

test("default Auto submits execution_mode auto with no executor", async () => {
  const { posted } = setup();
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  const button = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(button).not.toBeDisabled());

  fireEvent.click(button);
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    execution_mode: "auto",
    parameters: {},
  });
  expect(posted[0]).not.toHaveProperty("executor");
});

test("an unavailable executor disables the run with the backend reason and no fallback", async () => {
  setup({ selection: selectionRemoteUnavailable, pipelines: [remotePipeline] });
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  const button = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(button).toBeDisabled());
  // The standing banner is compacted behind the why-link on this dense page.
  expect(screen.getByRole("button", { name: /Why can't this pipeline run/i })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Why can't this pipeline run/i }));
  expect(screen.getByTestId("execution-option-remote_gpu")).toBeInTheDocument();
});

test("stale executor-selection response from the previous pipeline is ignored", async () => {
  const pipelineA = { ...remotePipeline, id: "pA", name: "Remote A" };
  const pipelineB = { ...remotePipeline, id: "pB", name: "Remote B" };
  const { selectionCalls, resolveDeferred } = setup({
    pipelines: [pipelineA, pipelineB],
    selection: selectionRemoteUnavailable,
    selectionDeferredFor: "pA",
  });

  await screen.findByText("Remote A · GPU");
  await waitFor(() => expect(selectionCalls).toEqual(["pA"]));

  fireEvent.mouseDown(screen.getByText("Remote A · GPU"));
  fireEvent.click(await screen.findByTitle("Remote B · GPU"));
  await waitFor(() => expect(selectionCalls).toEqual(["pA", "pB"]));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Why can't this pipeline run/i })).toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: "Run Analysis" })).toBeDisabled();

  // A's deferred selection resolves now, but must not be applied (stale).
  await act(async () => {
    resolveDeferred()?.(new Response(JSON.stringify(selectionRemoteAvailable)));
    await Promise.resolve();
  });
  expect(screen.getByRole("button", { name: /Why can't this pipeline run/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run Analysis" })).toBeDisabled();
});

test("remote pending run polls to completed and renders detections", async () => {
  const detectionWire = {
    id: "det_1",
    run_id: "run_r",
    recording_id: "rec_1",
    t_start_s: 0.01,
    t_end_s: 0.02,
    f_low_hz: 2440600000,
    f_high_hz: 2440700000,
    class_id: 9,
    class_name: "LoRa 250kHz",
    confidence: 0.94,
    scores_json: null,
  };
  const runFixture = runWire({ id: "run_r", executor: "remote_gpu", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3", pipeline_version: "1.0.0", status: "pending" });
  const readbackFixture = runWire({ id: "run_r", executor: "remote_gpu", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3", pipeline_version: "1.0.0", status: "completed" });
  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([remotePipeline]));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionRemoteAvailable));
    if (url.endsWith("/api/analysis-runs") && fetchOptions?.method === "POST") return new Response(JSON.stringify(runFixture), { status: 201 });
    if (url.endsWith("/api/analysis-runs/run_r")) return new Response(JSON.stringify(readbackFixture));
    if (url.endsWith("/api/analysis-runs/run_r/detections")) return new Response(JSON.stringify([detectionWire]));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );

  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByRole("button", { name: "Run Analysis" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(screen.getByText(/LoRa 250kHz/)).toBeInTheDocument(), { timeout: 4000 });
  expect(screen.getByText("Completed")).toBeInTheDocument();
});

test("starting an analysis refreshes detections once the run completes", async () => {
  // A pending run genuinely has NO detections yet; only the completed run does.
  // The page's own post-create fetch therefore returns [] and must never win over
  // the polling result.
  const detectionWire = {
    id: "det_1",
    run_id: "run_r",
    recording_id: "rec_1",
    t_start_s: 0.01,
    t_end_s: 0.02,
    f_low_hz: 2440600000,
    f_high_hz: 2440700000,
    class_id: 9,
    class_name: "LoRa 250kHz",
    confidence: 0.94,
    scores_json: null,
  };
  const pendingRun = runWire({ id: "run_r", status: "pending" });
  const completedRun = runWire({ id: "run_r", status: "completed" });
  let completed = false;
  let runPolls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([localPipeline, detectorPipeline]));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionLocalAvailable));
    if (url.endsWith("/api/analysis-runs") && fetchOptions?.method === "POST") {
      return new Response(JSON.stringify(pendingRun), { status: 201 });
    }
    if (url.endsWith("/api/analysis-runs/run_r")) {
      runPolls += 1;
      if (runPolls >= 2) completed = true;
      return new Response(JSON.stringify(completed ? completedRun : pendingRun));
    }
    if (url.endsWith("/api/analysis-runs/run_r/detections")) {
      // Emulate a real round-trip so React can re-render (and tear down the
      // polling effect) before this resolves.
      await new Promise((resolve) => setTimeout(resolve, 60));
      return new Response(JSON.stringify(completed ? [detectionWire] : []));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );

  await screen.findByText("Burst Demo");
  const runButton = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(runButton).not.toBeDisabled());
  fireEvent.click(runButton);

  expect(await screen.findByText(/LoRa 250kHz/, {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getByText("Completed")).toBeInTheDocument();
});

test("a selected detection stays highlighted with the detection overlay switched off", async () => {
  const detectionWire = {
    id: "det_1",
    run_id: "run_r",
    recording_id: "rec_1",
    t_start_s: 0.01,
    t_end_s: 0.02,
    f_low_hz: 2440600000,
    f_high_hz: 2440700000,
    class_id: 9,
    class_name: "LoRa 250kHz",
    confidence: 0.94,
    scores_json: null,
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([localPipeline, detectorPipeline]));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionLocalAvailable));
    if (url.endsWith("/api/analysis-runs/run_r")) {
      return new Response(JSON.stringify(runWire({ id: "run_r", status: "completed" })));
    }
    if (url.endsWith("/api/analysis-runs/run_r/detections")) {
      return new Response(JSON.stringify([detectionWire]));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/spectrum/rec_1?run=run_r"]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );

  await screen.findByText("Burst Demo");
  // Turn the general detection overlay OFF: no orange boxes remain.
  fireEvent.click(await screen.findByRole("checkbox", { name: "Prediction" }));
  await waitFor(() => expect(screen.queryByTestId("overlay-det-det_1")).toBeNull());

  // Selecting the row must still show the selected (red) box.
  fireEvent.click((await screen.findAllByText(/LoRa 250kHz/))[0]);
  await waitFor(() =>
    expect(screen.getByTestId("overlay-det-det_1")).toHaveAttribute("data-selected", "true"),
  );
});

test("a completed run shows status and export, and no execution-environment chrome", async () => {
  const completedRemoteRun = runWire({
    id: "run_r",
    executor: "remote_gpu",
    pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0",
    status: "completed",
    hardware_info_json: {
      device_index: 0,
      device_type: "cuda",
      device_name: "NVIDIA GeForce RTX 5090",
      torch_version: "2.8.0+cu128",
      cuda_version: "12.8",
    },
    execution_metadata_json: {
      remote_profile: "autodl_primary",
      required_remote_runtime_commit: "6f24f3796efa99ca0c0f1099462f450127f25737",
      payload_sha256: "20b8130acb8cf9b92f7c95d640b12448790e34c7c5e01d2ab1858875fac4e7b3",
      remote_started_at: "2026-09-09T15:28:41.632869+00:00",
      remote_finished_at: "2026-09-09T15:28:54.924629+00:00",
      coordinator_token: "coord_abc123",
      request_id: "id_abc",
    },
  });
  setup({ pipelines: [remotePipeline], selection: selectionRemoteAvailable, readbackFixture: completedRemoteRun, initialPath: "/spectrum/rec_1?run=run_r" });

  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await screen.findByText("Completed");

  // The execution environment is an internal detail: neither the selector nor any
  // provenance chrome (executor / remote profile / payload hash) reaches the user.
  expect(screen.queryByTestId("run-provenance-card")).toBeNull();
  expect(screen.queryByText(/Executor:/)).toBeNull();
  expect(screen.queryByText(/autodl_primary/)).toBeNull();
  expect(screen.queryByText(/Payload SHA/)).toBeNull();
  expect(screen.queryByText(/coordinator_token/)).toBeNull();

  // The result export action stays available for a completed run.
  expect(await screen.findByTestId("export-analysis-run-button")).toBeInTheDocument();
});

test("exposes STFT Energy Detector with detection-only copy and submits its id", async () => {
  const { posted } = setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");
  expect(screen.getByText("STFT Energy Detector · CPU · Detection & localization only")).toBeInTheDocument();

  const runButton = screen.getByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(runButton).not.toBeDisabled());
  fireEvent.click(runButton);
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ pipeline_id: "stft_energy_detector", execution_mode: "auto" });
});

test("gives the spectrogram dominant desktop space and lets the results panel stack responsively", async () => {
  setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");

  const workspace = await screen.findByTestId("spectrum-workspace");
  expect(workspace).toHaveStyle({ display: "flex", flexWrap: "wrap" });

  const viewerPane = screen.getByTestId("spectrum-viewer-pane");
  const resultsPane = screen.getByTestId("spectrum-results-pane");
  expect(workspace.contains(viewerPane)).toBe(true);
  expect(workspace.contains(resultsPane)).toBe(true);

  // The viewer grows to fill the remaining width; the results panel is a bounded sidebar.
  expect(viewerPane.style.flexGrow).toBe("1");
  expect(Number.parseInt(resultsPane.style.flexBasis, 10)).toBeGreaterThanOrEqual(280);
  expect(Number.parseInt(resultsPane.style.flexBasis, 10)).toBeLessThanOrEqual(340);
});

// ---------------------------------------------------------------------------
// F2.4 — Signals / Signal Detail / deep-link preservation (regression only)
// ---------------------------------------------------------------------------

const deepLinkDetection = {
  id: "det_1",
  run_id: "run_2",
  recording_id: "rec_1",
  t_start_s: 0.01,
  t_end_s: 0.02,
  f_low_hz: 2440600000,
  f_high_hz: 2440700000,
  class_id: 9,
  class_name: "LoRa 250kHz",
  confidence: 0.94,
  scores_json: null,
};

const deepLinkRun = {
  id: "run_2",
  recording_id: "rec_1",
  pipeline_id: "dummy",
  pipeline_version: "1.0",
  executor: "local_cpu",
  status: "completed",
  parameters_json: {},
  hardware_info_json: null,
  execution_metadata_json: null,
  started_at: null,
  finished_at: null,
  error_type: null,
  error_message: null,
  worker_pid: null,
  created_at: null,
};

function deepLinkSetup(initialPath: string, locale: "zh-CN" | "en-US" = "en-US") {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([localPipeline]));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionLocalAvailable));
    if (url.endsWith("/api/analysis-runs/run_2")) return new Response(JSON.stringify(deepLinkRun));
    if (url.endsWith("/api/analysis-runs/run_2/detections")) return new Response(JSON.stringify([deepLinkDetection]));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
          <Route path="/signals/:runId" element={<div>Signals Page</div>} />
          <Route path="/signals/:runId/:detectionId" element={<div>Signal Detail Page</div>} />
        </Routes>
      </MemoryRouter>,
      { locale },
    ),
  );
}

test("deep link ?run= loads the specified AnalysisRun", async () => {
  deepLinkSetup("/spectrum/rec_1?run=run_2");
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(screen.getByTestId("run-status-badge")).toHaveTextContent("Completed"));
});

test("deep link ?selected= preserves the selected detection", async () => {
  deepLinkSetup("/spectrum/rec_1?run=run_2&selected=det_1");
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(screen.getByText(/Selected: LoRa 250kHz/)).toBeInTheDocument());
});

test("View All navigates to /signals/:runId", async () => {
  deepLinkSetup("/spectrum/rec_1?run=run_2");
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(screen.getByTestId("run-status-badge")).toHaveTextContent("Completed"));
  fireEvent.click(screen.getByRole("button", { name: "View All" }));
  await screen.findByText("Signals Page");
});

test("View Details navigates to /signals/:runId/:detectionId", async () => {
  deepLinkSetup("/spectrum/rec_1?run=run_2");
  await screen.findByText("Burst Demo");
  await screen.findByText(/LoRa 250kHz/);
  fireEvent.click(screen.getByRole("button", { name: "View Details" }));
  await screen.findByText("Signal Detail Page");
});

// ---------------------------------------------------------------------------
// L4 residual corrective — zh-CN coverage for the spectrum workspace
// ---------------------------------------------------------------------------

test("localizes the spectrum workspace shell and run controls in zh-CN while preserving technical identity", async () => {
  setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable, locale: "zh-CN" });
  await screen.findByText("Burst Demo");
  expect(screen.getByRole("button", { name: "开始分析" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run Analysis" })).toBeNull();
  expect(screen.getByText("检测结果")).toBeInTheDocument();
  expect(screen.getByText("真值标注（GT）")).toBeInTheDocument();
  // Technical presentation is unchanged: Fs/Fc units retain their tokens.
  expect(screen.getByText(/Fs 1\.000 MHz/)).toBeInTheDocument();
  expect(screen.getByText(/Fc 2\.441000 GHz/)).toBeInTheDocument();
  // The STFT representation selector is removed; the pipeline select is labeled
  // 算法流水线 and there is exactly one visible pipeline select control.
  expect(screen.getByText("算法流水线")).toBeInTheDocument();
  expect(screen.queryByText("STFT")).toBeNull();
});

test("localizes the active run control in zh-CN", async () => {
  const { posted } = setup({
    pipelines: [localPipeline, detectorPipeline],
    selection: selectionLocalAvailable,
    locale: "zh-CN",
    runFixture: runWire({ status: "running" }),
  });
  await screen.findByText("Burst Demo");
  const runButton = screen.getByRole("button", { name: "开始分析" });
  await waitFor(() => expect(runButton).not.toBeDisabled());
  fireEvent.click(runButton);
  await waitFor(() => expect(posted.length).toBe(1));
  expect(await screen.findByRole("button", { name: "分析中…" })).toBeInTheDocument();
});

test("localizes the detection-only pipeline capability copy in zh-CN and preserves pipeline identity", async () => {
  setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable, locale: "zh-CN" });
  await screen.findByText("Burst Demo");
  expect(await screen.findByText("STFT Energy Detector · CPU · 仅检测与定位")).toBeInTheDocument();
  expect(screen.queryByText(/Detection & localization only/)).toBeNull();
});

test("localizes the selected-detection label in zh-CN while preserving raw class identity", async () => {
  deepLinkSetup("/spectrum/rec_1?run=run_2&selected=det_1", "zh-CN");
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(screen.getByText(/已选: LoRa 250kHz/)).toBeInTheDocument());
});

test("localizes the spectrum error shell in zh-CN and preserves the raw backend detail", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/recordings/")) {
      return new Response(JSON.stringify({ error: { code: "BOOM", message: "transient" } }), { status: 503 });
    }
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([localPipeline]));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionLocalAvailable));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
        <Routes>
          <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
      { locale: "zh-CN" },
    ),
  );
  expect(await screen.findByText("无法打开频谱工作台")).toBeInTheDocument();
  expect(screen.queryByText("Unable to open spectrum workspace")).toBeNull();
  expect(screen.getByText(/BOOM: transient/)).toBeInTheDocument();
});

test("defaults to STFT Energy Detector instead of a placeholder pipeline", async () => {
  const { posted } = setup({ pipelines: [remotePipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");
  const runButton = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(runButton).not.toBeDisabled());
  fireEvent.click(runButton);
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ pipeline_id: "stft_energy_detector", execution_mode: "auto" });
});

test("long recordings offer a time window that refetches a higher-resolution slice", async () => {
  const longRecording = { ...recording, duration_s: 6.0, num_samples: 6000000 };
  const requested: string[] = [];
  setup({
    pipelines: [localPipeline, detectorPipeline],
    selection: selectionLocalAvailable,
    recordingFixture: longRecording,
    spectrogramHandler: (url) => {
      requested.push(url);
      const params = new URL(url, "http://x").searchParams;
      const tStart = Number(params.get("t_start_s") ?? 0);
      const tEnd = Number(params.get("t_end_s") ?? 6);
      return new Response(
        JSON.stringify({
          ...spectrogram,
          t_start_s: tStart,
          t_end_s: tEnd,
          image_url: `/media/spectrograms/key_${tStart}_${tEnd}.png`,
          num_frames: 2048,
        }),
      );
    },
  });
  await screen.findByText("Burst Demo");

  // Full-duration overview is fetched first, so the selector is offered.
  const selector = await screen.findByTestId("spectrum-time-window");
  expect(selector).toBeInTheDocument();
  expect(requested.some((url) => !url.includes("t_start_s"))).toBe(true);

  // Picking the 2.0-4.0 s slice must refetch that window (higher resolution per column).
  fireEvent.mouseDown(selector);
  const option = await screen.findByTitle("2.0-4.0 s");
  fireEvent.click(option);
  await waitFor(() => expect(requested.some((url) => url.includes("t_start_s=2") && url.includes("t_end_s=4"))).toBe(true));
});
