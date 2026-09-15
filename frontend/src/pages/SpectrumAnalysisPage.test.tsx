import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SpectrumAnalysisPage } from "./SpectrumAnalysisPage";

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
  } = options;

  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
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
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return { posted, selectionCalls, resolveDeferred: () => resolveDeferred };
}

test("the spectrum page renders the execution environment selector and never calls executor-availability", async () => {
  const { selectionCalls } = setup({ selection: selectionLocalAvailable, pipelines: [localPipeline, detectorPipeline] });
  await screen.findByText("Burst Demo");
  await waitFor(() => expect(screen.getByTestId("execution-environment-selector")).toBeInTheDocument());
  await waitFor(() => expect(selectionCalls.length).toBeGreaterThan(0));
});

test("default Auto submits execution_mode auto with no executor", async () => {
  const { posted } = setup();
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("Recommended: Remote GPU"));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    execution_mode: "auto",
    parameters: {},
  });
  expect(posted[0]).not.toHaveProperty("executor");
});

test("an explicit manual selection submits the exact executor and no auto mode", async () => {
  const { posted } = setup({ selection: selectionDual, pipelines: [remotePipeline] });
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByText("Local GPU")).toBeInTheDocument());

  fireEvent.click(screen.getByText("Local GPU"));
  const button = screen.getByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ executor: "local_gpu", parameters: {} });
  expect(posted[0]).not.toHaveProperty("execution_mode");
});

test("an unavailable executor disables the run with the backend reason and no fallback", async () => {
  setup({ selection: selectionRemoteUnavailable, pipelines: [remotePipeline] });
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  const button = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(button).toBeDisabled());
  expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("No runnable executor.");
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
  await waitFor(() => expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("No runnable executor."));
  expect(screen.getByRole("button", { name: "Run Analysis" })).toBeDisabled();

  // A's deferred selection resolves now, but must not be applied (stale).
  await act(async () => {
    resolveDeferred()?.(new Response(JSON.stringify(selectionRemoteAvailable)));
    await Promise.resolve();
  });
  expect(screen.getByTestId("execution-environment-summary")).toHaveTextContent("No runnable executor.");
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
    <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );

  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByRole("button", { name: "Run Analysis" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(screen.getByText(/LoRa 250kHz/)).toBeInTheDocument(), { timeout: 4000 });
  expect(screen.getByText("completed")).toBeInTheDocument();
});

test("completed remote run renders allowlisted metadata only", async () => {
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
  await screen.findByText("completed");

  expect(screen.getByText(/Executor: remote_gpu/)).toBeInTheDocument();
  expect(screen.getByText(/Profile: autodl_primary/)).toBeInTheDocument();
  expect(screen.getByText(/Device: NVIDIA GeForce RTX 5090/)).toBeInTheDocument();
  expect(screen.getByText(/Runtime commit: 6f24f379/)).toBeInTheDocument();
  expect(screen.getByText(/Payload SHA: 20b8130a/)).toBeInTheDocument();

  expect(screen.queryByText(/coordinator_token/)).toBeNull();
  expect(screen.queryByText("coord_abc123")).toBeNull();
  expect(screen.queryByText(/required_remote_runtime_commit/)).toBeNull();
  expect(screen.queryByText(/execution_metadata_json/)).toBeNull();
});

test("exposes STFT Energy Detector with detection-only copy and submits its id", async () => {
  const { posted } = setup({ pipelines: [localPipeline, detectorPipeline], selection: selectionLocalAvailable });
  await screen.findByText("Burst Demo");
  expect(screen.getByText("Dummy Pipeline · CPU")).toBeInTheDocument();

  fireEvent.mouseDown(screen.getByText("Dummy Pipeline · CPU"));
  const detectorOption = await screen.findByTitle("STFT Energy Detector · CPU · Detection & localization only");
  fireEvent.click(detectorOption);
  await waitFor(() => expect(screen.getAllByText("STFT Energy Detector · CPU · Detection & localization only").length).toBeGreaterThan(0));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ pipeline_id: "stft_energy_detector", execution_mode: "auto" });
});
