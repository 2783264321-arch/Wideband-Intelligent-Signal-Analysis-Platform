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