import { apiGet, apiPostJson, PlatformApiError, createAnalysisRun, getExecutorAvailability, getExecutorSelection, listPipelines } from "./client";

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

test("createAnalysisRun translates a manual domain request into the snake_case wire body", async () => {
  let posted: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    expect(url).toBe("http://127.0.0.1:8000/api/analysis-runs");
    posted = JSON.parse(String(options?.body)) as Record<string, unknown>;
    return new Response(JSON.stringify({
      id: "run_r",
      recording_id: "rec_1",
      pipeline_id: "dummy",
      pipeline_version: "1.0",
      executor: "remote_gpu",
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

  const run = await createAnalysisRun({
    recordingId: "rec_1",
    pipelineId: "dummy",
    executor: "remote_gpu",
    parameters: {},
  });

  expect(posted).toMatchObject({
    recording_id: "rec_1",
    pipeline_id: "dummy",
    executor: "remote_gpu",
    parameters: {},
  });
  expect(posted).not.toHaveProperty("execution_mode");
  expect(run.executor).toBe("remote_gpu");
});

test("createAnalysisRun translates an auto domain request and omits executor", async () => {
  let posted: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    posted = JSON.parse(String(options?.body)) as Record<string, unknown>;
    return new Response(JSON.stringify({
      id: "run_r",
      recording_id: "rec_1",
      pipeline_id: "dummy",
      pipeline_version: "1.0",
      executor: "local_gpu",
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

  await createAnalysisRun({
    recordingId: "rec_1",
    pipelineId: "dummy",
    executionMode: "auto",
    parameters: {},
  });

  expect(posted).toMatchObject({ recording_id: "rec_1", pipeline_id: "dummy", execution_mode: "auto" });
  expect(posted).not.toHaveProperty("executor");
});
test("getExecutorSelection builds a recording-scoped query and maps candidates", async () => {
  let requestedUrl = "";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    requestedUrl = url;
    return new Response(JSON.stringify({
      requested_mode: "auto",
      resolved_executor: "local_cpu",
      reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
      reason: "Only local_cpu is runnable.",
      workload_class: "SMALL",
      candidates: [
        {
          executor: "local_cpu",
          technical: true,
          configured: true,
          certified: true,
          available: true,
          reason_code: null,
          reason_message: null,
        },
        {
          executor: "remote_gpu",
          technical: true,
          configured: false,
          certified: false,
          available: false,
          reason_code: "EXECUTION_CAPABILITY_UNAVAILABLE",
          reason_message: "No executor provider is registered for 'remote_gpu'.",
        },
      ],
    }));
  }));

  const selection = await getExecutorSelection({
    scope: { kind: "recording", recordingId: "rec_1" },
    pipelineId: "dummy",
  });

  expect(requestedUrl).toContain("/api/executor-selection");
  expect(requestedUrl).toContain("recording_id=rec_1");
  expect(requestedUrl).toContain("pipeline_id=dummy");
  expect(selection.requestedMode).toBe("auto");
  expect(selection.resolvedExecutor).toBe("local_cpu");
  expect(selection.reasonCode).toBe("AUTO_ONLY_RUNNABLE_EXECUTOR");
  expect(selection.reason).toBe("Only local_cpu is runnable.");
  expect(selection.workloadClass).toBe("SMALL");
  expect(selection.candidates[0]).toEqual({
    executor: "local_cpu",
    technical: true,
    configured: true,
    certified: true,
    available: true,
    reasonCode: null,
    reasonMessage: null,
  });
  expect(selection.candidates[1].configured).toBe(false);
  expect(selection.candidates[1].reasonCode).toBe("EXECUTION_CAPABILITY_UNAVAILABLE");
  expect(selection.candidates[1].reasonMessage).toBe(
    "No executor provider is registered for 'remote_gpu'.",
  );
});

test("getExecutorSelection builds a dataset-scoped query and never calls executor-availability", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return new Response(JSON.stringify({
      requested_mode: "manual",
      resolved_executor: null,
      reason_code: "AUTO_NO_RUNNABLE_EXECUTOR",
      reason: "No runnable executor.",
      workload_class: "UNKNOWN",
      candidates: [],
    }));
  }));

  const selection = await getExecutorSelection({
    scope: {
      kind: "dataset",
      datasetName: "spacenet",
      datasetSplit: "test",
      datasetLabelSpace: "spacenet_14",
    },
    pipelineId: "dummy",
  });

  expect(urls).toHaveLength(1);
  expect(urls[0]).toContain("/api/executor-selection");
  expect(urls[0]).toContain("dataset_name=spacenet");
  expect(urls[0]).toContain("dataset_split=test");
  expect(urls[0]).toContain("dataset_label_space=spacenet_14");
  expect(urls[0]).toContain("pipeline_id=dummy");
  expect(urls[0]).not.toContain("executor-availability");
  expect(selection.resolvedExecutor).toBeNull();
});

test("getExecutorSelection includes model_release_id only when provided", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return new Response(JSON.stringify({
      requested_mode: "manual",
      resolved_executor: null,
      reason_code: "AUTO_NO_RUNNABLE_EXECUTOR",
      reason: "none",
      workload_class: "UNKNOWN",
      candidates: [],
    }));
  }));

  await getExecutorSelection({
    scope: { kind: "recording", recordingId: "rec_1" },
    pipelineId: "dummy",
  });
  await getExecutorSelection({
    scope: { kind: "recording", recordingId: "rec_1" },
    pipelineId: "dummy",
    modelReleaseId: "golden",
  });

  expect(urls[0]).not.toContain("model_release_id");
  expect(urls[1]).toContain("model_release_id=golden");
});

test("getExecutorSelection rejects a malformed scope before fetching", async () => {
  const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchSpy);

  const mixed = {
    kind: "recording",
    recordingId: "rec_1",
    datasetName: "spacenet",
    datasetSplit: "test",
    datasetLabelSpace: "spacenet_14",
  } as unknown as import("./types").ExecutionSelectionScope;
  await expect(
    getExecutorSelection({ scope: mixed, pipelineId: "dummy" }),
  ).rejects.toThrow();
  await expect(
    getExecutorSelection({
      scope: { kind: "dataset", datasetName: "", datasetSplit: "test", datasetLabelSpace: "spacenet_14" },
      pipelineId: "dummy",
    }),
  ).rejects.toThrow();
  expect(fetchSpy).not.toHaveBeenCalled();
});
