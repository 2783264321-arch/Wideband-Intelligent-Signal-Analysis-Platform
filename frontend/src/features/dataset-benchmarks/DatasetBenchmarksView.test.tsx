import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DatasetBenchmarksView } from "./DatasetBenchmarksView";

afterEach(() => vi.unstubAllGlobals());

test("resolves a ready imported batch and creates then runs a v2 benchmark", async () => {
  const onBenchmarkOpen = vi.fn();
  const onOpenCase = vi.fn();
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const fingerprint = "a".repeat(64);
  const manifest = "b".repeat(64);
  const entries = Array.from({ length: 2500 }, (_, index) => ({
    manifest_order: index,
    recording_id: `rec_${index}`,
    recording_name: String(index),
    analysis_run_id: `run_${index}`,
    item_key: String(index),
  }));
  const catalog = [{
    import_fingerprint: fingerprint, pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", run_count: 2500, detection_count: 33373,
    archive_sha256: "c".repeat(64), result_provenance: {}, transport_provenance: {},
    ready: true, inconsistency_reasons: [],
  }];
  const resolution = {
    import_fingerprint: fingerprint, dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", recording_manifest_hash: manifest,
    expected_recordings: 2500, resolved_recordings: 2500, missing_recordings: 0, conflict_count: 0,
    entries,
  };
  const evaluation = {
    id: "eval_real", name: "Real benchmark", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", status: "pending", expected_recordings: 2500,
    evaluated_recordings: 2500, missing_recordings: 0, coverage: 1, comparable: true,
    recording_manifest_hash: manifest, evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: null, per_class_metrics_json: null, confusion_json: null,
    progress_stage: null, progress_current: null, progress_total: null, worker_pid: null,
    error_type: null, error_message: null, created_at: "2026-09-06T00:00:00Z",
    started_at: null, completed_at: null,
  };

  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    const method = init?.method ?? "GET";
    requests.push({ url: urlStr, method, body: init?.body as string | undefined });
    if (urlStr.endsWith("/api/dataset-benchmarks/imported-batches")) return new Response(JSON.stringify(catalog));
    if (urlStr.endsWith("/api/dataset-benchmarks/resolve-imported-batch")) return new Response(JSON.stringify(resolution));
    if (urlStr.endsWith("/api/dataset-benchmarks") && method === "GET") return new Response("[]");
    if (urlStr.endsWith("/api/dataset-benchmarks") && method === "POST") return new Response(JSON.stringify(evaluation), { status: 201 });
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/run")) {
      return new Response(JSON.stringify({ ...evaluation, status: "running", progress_stage: "loading" }), { status: 202 });
    }
    throw new Error(`Unexpected request: ${method} ${urlStr}`);
  }));

  render(
    <MemoryRouter>
      <DatasetBenchmarksView
        onBenchmarkOpen={onBenchmarkOpen}
        onOpenCase={onOpenCase}
      />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "New Benchmark" }));
  fireEvent.mouseDown(await screen.findByLabelText("Imported Analysis Batch"));
  fireEvent.click(await screen.findByText(/zoomspec_yolo26n_aug_combined_frn_v3/));
  expect(screen.getByText("physical_tf_detection_ap_v2")).toBeInTheDocument();
  expect(screen.queryByLabelText(/Protocol selector/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
  expect(await screen.findByText("2500 / 2500")).toBeInTheDocument();
  expect(screen.getByText(manifest)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Benchmark Name"), { target: { value: "Real benchmark" } });
  const createRunButton = await screen.findByRole("button", { name: /Create & Run/ });
  await waitFor(() => expect(createRunButton).toBeEnabled());
  fireEvent.click(createRunButton);
  await waitFor(() => expect(onBenchmarkOpen).toHaveBeenCalledWith("eval_real"));

  const createCall = requests.find((item) => item.url.endsWith("/api/dataset-benchmarks") && item.method === "POST");
  expect(createCall).toBeDefined();
  const body = JSON.parse(createCall!.body!);
  expect(body.recording_manifest_hash).toBe(manifest);
  expect(body.items).toHaveLength(2500);
  expect(body.evaluation_protocol).toBeUndefined();
  expect(requests.some((item) => item.url.endsWith("/api/dataset-benchmarks/eval_real/run"))).toBe(true);
});
import { act } from "@testing-library/react";
import { BenchmarkDetailView } from "./BenchmarkDetailView";

function benchmarkWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "eval_real", name: "Real benchmark", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", status: "running", expected_recordings: 2500, evaluated_recordings: 2500,
    missing_recordings: 0, coverage: 1, comparable: true, recording_manifest_hash: "b".repeat(64),
    evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: null, per_class_metrics_json: null, confusion_json: null,
    progress_stage: "class_aware_ap", progress_current: null, progress_total: null, worker_pid: null,
    error_type: null, error_message: null, created_at: "2026-09-06T00:00:00Z",
    started_at: "2026-09-06T00:00:01Z", completed_at: null,
    ...overrides,
  };
}

test("polls only while pending/running and stops after completed", async () => {
  vi.useFakeTimers();
  try {
    let detailCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const urlStr = String(url);
      if (urlStr.endsWith("/api/dataset-benchmarks/eval_real")) {
        detailCalls += 1;
        return new Response(JSON.stringify(
          detailCalls === 1
            ? benchmarkWire()
            : benchmarkWire({
                status: "completed", progress_stage: "completed", completed_at: "2026-09-06T00:01:00Z",
                aggregate_metrics_json: {
                  ground_truth: { raw_count: 20018, canonical_count: 19962, duplicates_removed: 56, duplicate_policy: "exact_physical_class_dedup" },
                  classification_applicable: true, classification_reason: null,
                  localization: { ap50: 0.7, ap50_95: 0.5, operating: { tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 } },
                  classification_on_matched: { matched_count: 1, class_correct: 1, class_wrong: 0, matched_accuracy: 1 },
                  class_aware: { map50: 0.6, map50_95: 0.4, operating: { tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 } },
                },
                per_class_metrics_json: [], confusion_json: [],
              }),
        ));
      }
      if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/items")) return new Response("[]");
      throw new Error(`Unexpected request: ${urlStr}`);
    }));

    render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("class_aware_ap")).toBeInTheDocument();
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(detailCalls).toBe(2);
    const callsAtCompletion = detailCalls;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(detailCalls).toBe(callsAtCompletion);
  } finally {
    vi.useRealTimers();
  }
});

const operating = { tp: 10, fp: 2, fn: 3, precision: 0.8333, recall: 0.7692, f1: 0.8 };
const perClassWire = Array.from({ length: 14 }, (_, classId) => ({
  class_id: classId, class_name: `Class ${classId}`, gt_count: 10 + classId, prediction_count: 12 + classId,
  ap50: 0.5, ap50_95: 0.4, operating,
}));

function completedBenchmarkWire({ v1 = false, detectionOnly = false } = {}) {
  return benchmarkWire({
    status: "completed", progress_stage: "completed", completed_at: "2026-09-06T00:01:00Z",
    evaluation_protocol: v1 ? "physical_tf_detection_ap_v1" : "physical_tf_detection_ap_v2",
    protocol_config_json: v1 ? {} : { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: {
      ...(v1 ? {} : { ground_truth: { raw_count: 20018, canonical_count: 19962, duplicates_removed: 56, duplicate_policy: "exact_physical_class_dedup" } }),
      classification_applicable: !detectionOnly,
      classification_reason: detectionOnly ? "detection_only_pipeline" : null,
      localization: { ap50: 0.6, ap50_95: 0.45, operating },
      classification_on_matched: detectionOnly ? null : { matched_count: 100, class_correct: 80, class_wrong: 20, matched_accuracy: 0.8 },
      class_aware: detectionOnly ? null : { map50: 0.49706861157413673, map50_95: 0.37325127587379914, operating },
    },
    per_class_metrics_json: detectionOnly ? [] : perClassWire,
    confusion_json: detectionOnly ? null : [{ gt_class_id: 9, gt_class_name: "LoRa", pred_class_id: 8, pred_class_name: "Zigbee", count: 7 }],
  });
}

function stubCompletedDetail(payload: object) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const urlStr = String(url);
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real")) return new Response(JSON.stringify(payload));
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/items")) {
      return new Response(JSON.stringify([{
        id: "item_0", evaluation_id: "eval_real", manifest_order: 0, recording_id: "rec_0",
        recording_name: "0", analysis_run_id: "run_0", status: "included", gt_count: 6, prediction_count: 13, error_reason: null,
      }]));
    }
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
}

test("renders completed v2 GT provenance, metrics, per-class rows, confusions and protocol", async () => {
  stubCompletedDetail(completedBenchmarkWire());
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  expect(await screen.findByText("End-to-End Class-aware mAP50:95")).toBeInTheDocument();
  expect(screen.getByText("20018")).toBeInTheDocument();
  expect(screen.getByText("19962")).toBeInTheDocument();
  expect(screen.getByText("56")).toBeInTheDocument();
  expect(await screen.findByText("Class 13")).toBeInTheDocument();
  expect(screen.getByText("Top Classification Confusions")).toBeInTheDocument();
  expect(screen.getByText("physical_tf_detection_ap_v2")).toBeInTheDocument();
  expect(screen.getByText("b".repeat(64))).toBeInTheDocument();
});

test("renders old v1 as raw-GT protocol without inventing dedup provenance", async () => {
  stubCompletedDetail(completedBenchmarkWire({ v1: true }));
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  expect(await screen.findByText("Raw GT protocol")).toBeInTheDocument();
  expect(screen.queryByText("Exact duplicates removed")).not.toBeInTheDocument();
});

test("renders classification metrics as N/A for detection-only evaluations", async () => {
  stubCompletedDetail(completedBenchmarkWire({ detectionOnly: true }));
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  await screen.findByText("Localization");
  expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(2);
});
