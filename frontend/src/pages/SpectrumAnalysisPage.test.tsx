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

const pipelines = [
  {
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
  },
  {
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
  },
];

function setup(postedPipelineIds: string[]) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.endsWith("/api/analysis-runs") && options?.method === "POST") {
      const body = JSON.parse(String(options.body)) as { pipeline_id: string };
      postedPipelineIds.push(body.pipeline_id);
      return new Response(JSON.stringify({
        id: "run_1",
        recording_id: "rec_1",
        pipeline_id: body.pipeline_id,
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
      }), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("exposes STFT Energy Detector with detection-only copy and submits its id", async () => {
  const postedPipelineIds: string[] = [];
  setup(postedPipelineIds);

  await screen.findByText("Burst Demo");
  expect(screen.getByText("Dummy Pipeline · CPU")).toBeInTheDocument();

  fireEvent.mouseDown(screen.getByText("Dummy Pipeline · CPU"));
  const detectorOption = await screen.findByTitle("STFT Energy Detector · CPU · Detection & localization only");
  fireEvent.click(detectorOption);

  await waitFor(() => expect(screen.getAllByText("STFT Energy Detector · CPU · Detection & localization only").length).toBeGreaterThan(0));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(postedPipelineIds).toContain("stft_energy_detector"));
});

// ---------------------------------------------------------------------------
// Task 12F-C Task 4 — remote GPU executor path
// ---------------------------------------------------------------------------

const zoomspecPipeline = {
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

const availabilityAvailable = {
  executor: "remote_gpu",
  available: true,
  reason_code: null,
  reason_message: null,
  remote_profile: "autodl_primary",
  recommended: true,
};

const availabilityUnavailable = {
  executor: "remote_gpu",
  available: false,
  reason_code: "REMOTE_TRANSPORT_UNAVAILABLE",
  reason_message: "Remote GPU executor is unavailable.",
  remote_profile: "autodl_primary",
  recommended: false,
};

function remoteRunWire(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: "run_r",
    recording_id: "rec_1",
    pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0",
    executor: "remote_gpu",
    status,
    parameters_json: {},
    hardware_info_json: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    worker_pid: 1,
    created_at: "2026-09-05T00:00:00",
    ...extra,
  };
}

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

interface RemoteSetupOptions {
  availability?: Record<string, unknown>;
  runFixture?: Record<string, unknown>;
  pipelines?: Record<string, unknown>[];
  readbackFixture?: Record<string, unknown>;
}

function remoteSetup(options: RemoteSetupOptions = {}, initialPath = "/spectrum/rec_1") {
  const posted: Record<string, unknown>[] = [];
  const {
    availability = availabilityAvailable,
    runFixture = remoteRunWire("pending"),
    pipelines = [zoomspecPipeline],
    readbackFixture = remoteRunWire("completed", {
      hardware_info_json: { device_type: "cuda", device_name: "Fake GPU", device_index: 0 },
      execution_metadata_json: { remote_profile: "autodl_primary" },
    }),
  } = options;
  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-availability")) return new Response(JSON.stringify(availability));
    if (url.endsWith("/api/analysis-runs") && fetchOptions?.method === "POST") {
      posted.push(JSON.parse(String(fetchOptions.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(runFixture), { status: 201 });
    }
    if (url.endsWith("/api/analysis-runs/run_r")) return new Response(JSON.stringify(readbackFixture));
    if (url.endsWith("/api/analysis-runs/run_r/detections")) return new Response(JSON.stringify([detectionWire]));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );
  return posted;
}

test("remote-only pipeline available submits remote_gpu executor", async () => {
  const posted = remoteSetup();
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByTestId("executor-availability")).toHaveTextContent("Remote GPU available"));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3", executor: "remote_gpu", parameters: {} });
});

test("remote-only pipeline unavailable disables run with reason", async () => {
  remoteSetup({ availability: availabilityUnavailable });
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  const button = await screen.findByRole("button", { name: "Run Analysis" });
  await waitFor(() => expect(button).toBeDisabled());
  expect(screen.getByTestId("executor-availability")).toHaveTextContent("Remote GPU executor is unavailable.");
});

test("stale availability response from previous pipeline is ignored", async () => {
  let resolveFirst: ((value: Response) => void) | undefined;
  const firstDeferred = new Promise<Response>((resolve) => { resolveFirst = resolve; });
  const availabilityCalls: string[] = [];
  const pipelineA = { ...zoomspecPipeline, id: "pA", name: "Remote A" };
  const pipelineB = { ...zoomspecPipeline, id: "pB", name: "Remote B" };

  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify([pipelineA, pipelineB]));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.includes("/api/executor-availability")) {
      const pipelineId = new URL(url, "http://x").searchParams.get("pipeline_id");
      availabilityCalls.push(pipelineId ?? "");
      if (pipelineId === "pA") return firstDeferred;
      return new Response(JSON.stringify(availabilityUnavailable));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );

  // Default pipeline A -> availability request for A is deferred (in flight).
  await screen.findByText("Remote A · GPU");
  await waitFor(() => expect(availabilityCalls).toEqual(["pA"]));

  // Switch to pipeline B -> fresh availability (unavailable), stale A ignored.
  fireEvent.mouseDown(screen.getByText("Remote A · GPU"));
  fireEvent.click(await screen.findByTitle("Remote B · GPU"));
  await waitFor(() => expect(screen.getByTestId("executor-availability")).toHaveTextContent("Remote GPU executor is unavailable."));
  await waitFor(() => expect(availabilityCalls).toEqual(["pA", "pB"]));
  expect(screen.getByRole("button", { name: "Run Analysis" })).toBeDisabled();

  // A's deferred availability resolves NOW, but must not be applied (stale).
  await act(async () => {
    resolveFirst?.(new Response(JSON.stringify(availabilityAvailable)));
    await Promise.resolve();
  });
  expect(screen.getByTestId("executor-availability")).toHaveTextContent("Remote GPU executor is unavailable.");
  expect(screen.getByRole("button", { name: "Run Analysis" })).toBeDisabled();
});

test("remote pending run polls to completed and renders detections", async () => {
  remoteSetup({}, "/spectrum/rec_1");
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await waitFor(() => expect(screen.getByTestId("executor-availability")).toHaveTextContent("Remote GPU available"));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  // createAnalysisRun returns pending -> polling readback returns completed -> detections render.
  await waitFor(() => expect(screen.getByText(/LoRa 250kHz/)).toBeInTheDocument(), { timeout: 4000 });
  expect(screen.getByText("completed")).toBeInTheDocument();
});

const completedRemoteRun = remoteRunWire("completed", {
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

test("completed remote run renders allowlisted metadata only", async () => {
  remoteSetup(
    { readbackFixture: completedRemoteRun, runFixture: completedRemoteRun },
    "/spectrum/rec_1?run=run_r",
  );
  await screen.findByText("ZoomSpec Frozen V3 · GPU");
  await screen.findByText("completed");

  expect(screen.getByText(/Executor: remote_gpu/)).toBeInTheDocument();
  expect(screen.getByText(/Profile: autodl_primary/)).toBeInTheDocument();
  expect(screen.getByText(/Device: NVIDIA GeForce RTX 5090/)).toBeInTheDocument();
  expect(screen.getByText(/Runtime commit: 6f24f379/)).toBeInTheDocument();
  expect(screen.getByText(/Payload SHA: 20b8130a/)).toBeInTheDocument();

  // Never render the coordinator token or raw execution metadata JSON.
  expect(screen.queryByText(/coordinator_token/)).toBeNull();
  expect(screen.queryByText("coord_abc123")).toBeNull();
  expect(screen.queryByText(/required_remote_runtime_commit/)).toBeNull();
  expect(screen.queryByText(/execution_metadata_json/)).toBeNull();
});