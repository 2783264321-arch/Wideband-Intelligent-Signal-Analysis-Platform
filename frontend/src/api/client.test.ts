import { apiGet, apiPostJson, PlatformApiError, createAnalysisRun, getExecutorAvailability, listPipelines } from "./client";

afterEach(() => vi.unstubAllGlobals());

test("apiGet preserves the backend error code, message and details", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({
      error: {
        code: "INVALID_BENCHMARK_TRANSITION",
        message: "Only pending evaluations can be started.",
        details: { stage: "running" },
      },
    }),
    { status: 409 },
  )));

  let thrown: unknown;
  try {
    await apiGet("/api/dataset-benchmarks/abc");
  } catch (e) {
    thrown = e;
  }
  expect(thrown).toBeInstanceOf(PlatformApiError);
  const err = thrown as PlatformApiError;
  expect(err.status).toBe(409);
  expect(err.code).toBe("INVALID_BENCHMARK_TRANSITION");
  expect(err.message).toBe("Only pending evaluations can be started.");
  expect(err.details).toEqual({ stage: "running" });
  expect(err.display).toBe("INVALID_BENCHMARK_TRANSITION: Only pending evaluations can be started.");
});

test("apiPostJson preserves an incomplete-batch backend error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({
      error: {
        code: "IMPORTED_BATCH_DATASET_INCOMPLETE",
        message: "Imported batch does not cover the current frozen Recording manifest exactly.",
        details: {},
      },
    }),
    { status: 422 },
  )));

  let thrown: unknown;
  try {
    await apiPostJson("/api/dataset-benchmarks/resolve-imported-batch", { import_fingerprint: "a".repeat(64) });
  } catch (e) {
    thrown = e;
  }
  const err = thrown as PlatformApiError;
  expect(err.status).toBe(422);
  expect(err.code).toBe("IMPORTED_BATCH_DATASET_INCOMPLETE");
  expect(err.message).toBe("Imported batch does not cover the current frozen Recording manifest exactly.");
});

test("non-JSON error body falls back to a generic HTTP error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("internal error", { status: 500 })));
  let thrown: unknown;
  try {
    await apiGet("/api/dataset-benchmarks");
  } catch (e) {
    thrown = e;
  }
  const err = thrown as PlatformApiError;
  expect(err).toBeInstanceOf(PlatformApiError);
  expect(err.status).toBe(500);
  expect(err.code).toBe("HTTP_500");
  expect(err.message).toBe("API request failed: 500");
});

test("getExecutorAvailability maps the backend availability contract", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    expect(url).toContain("/api/executor-availability");
    expect(url).toContain("recording_id=rec_1");
    expect(url).toContain("pipeline_id=zoomspec_yolo26n_aug_combined_frn_v3");
    return new Response(JSON.stringify({
      executor: "remote_gpu",
      available: true,
      reason_code: null,
      reason_message: null,
      remote_profile: "autodl_primary",
      recommended: true,
    }));
  }));
  const result = await getExecutorAvailability("rec_1", "zoomspec_yolo26n_aug_combined_frn_v3");
  expect(result.executor).toBe("remote_gpu");
  expect(result.available).toBe(true);
  expect(result.reasonCode).toBeNull();
  expect(result.remoteProfile).toBe("autodl_primary");
  expect(result.recommended).toBe(true);
});

test("listPipelines maps the wire capability contract into the camelCase domain model", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    expect(url).toContain("/api/pipelines");
    return new Response(JSON.stringify([
      {
        id: "zoomspec_yolo26n_aug_combined_frn_v3",
        name: "ZoomSpec",
        version: "1.0.0",
        label_space: "spacenet_14",
        recommended_device: "GPU",
        cpu_supported: false,
        executors_supported: ["remote_gpu"],
        recommended_executor: "remote_gpu",
        stages: ["ls_stft"],
        inspectable_stages: [],
        task_capability: "detection_classification",
        plugin_api_version: 1,
        output_label_space: "spacenet_14",
        input_compatibility: ["spacenet_14"],
        dataset_adapters: ["SpaceNet"],
        model_release_required: true,
        technical_execution_capabilities: [
          { executor: "remote_gpu", device_type: "cuda", precision: "float16" },
        ],
        recommended_execution: "remote_gpu",
      },
    ]));
  }));

  const [pipeline] = await listPipelines();
  expect(pipeline.labelSpace).toBe("spacenet_14");
  expect(pipeline.pluginApiVersion).toBe(1);
  expect(pipeline.outputLabelSpace).toBe("spacenet_14");
  expect(pipeline.inputCompatibility).toEqual(["spacenet_14"]);
  expect(pipeline.datasetAdapters).toEqual(["SpaceNet"]);
  expect(pipeline.modelReleaseRequired).toBe(true);
  expect(pipeline.technicalExecutionCapabilities).toEqual([
    { executor: "remote_gpu", deviceType: "cuda", precision: "float16" },
  ]);
  expect(pipeline.recommendedExecution).toBe("remote_gpu");
});

test("listPipelines tolerates a legacy backend without capability fields", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
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
  ]))));

  const [pipeline] = await listPipelines();
  expect(pipeline.pluginApiVersion).toBeUndefined();
  expect(pipeline.outputLabelSpace).toBeUndefined();
  expect(pipeline.technicalExecutionCapabilities).toBeUndefined();
  expect(pipeline.labelSpace).toBe("spacenet_14");
});

test("createAnalysisRun forwards the requested executor", async () => {
  let posted: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    expect(url).toBe("http://127.0.0.1:8000/api/analysis-runs");
    posted = JSON.parse(String(options?.body)) as Record<string, unknown>;
    return new Response(JSON.stringify({
      id: "run_r",
      recording_id: "rec_1",
      pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
      pipeline_version: "1.0.0",
      executor: posted.executor,
      status: "pending",
      parameters_json: {},
      hardware_info_json: null,
      started_at: null,
      finished_at: null,
      error_type: null,
      error_message: null,
      worker_pid: 1,
      created_at: "2026-09-05T00:00:00",
      execution_metadata_json: null,
    }), { status: 201 });
  }));
  const run = await createAnalysisRun("rec_1", "zoomspec_yolo26n_aug_combined_frn_v3", "remote_gpu");
  expect(posted).toMatchObject({ executor: "remote_gpu", parameters: {} });
  expect(run.executor).toBe("remote_gpu");
});