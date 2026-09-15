import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentCreateForm } from "./ExperimentCreateForm";

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

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
];

const selectionWire = {
  requested_mode: "auto",
  resolved_executor: "local_cpu",
  reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR",
  reason: "Only local_cpu is runnable.",
  workload_class: "SMALL",
  candidates: [
    { executor: "local_cpu", technical: true, configured: true, certified: true, available: true, reason_code: null, reason_message: null },
  ],
};

const experimentWire = {
  id: "exp_1",
  name: "Exp",
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
  status: "pending",
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
  queued_items: 3,
  running_items: 0,
  completed_items: 0,
  failed_items: 0,
  attempt_count: 0,
};

function setup() {
  const urls: string[] = [];
  let posted: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(url);
    if (url.includes("/api/executor-availability")) {
      throw new Error("dataset scope must not call executor-availability");
    }
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionWire));
    if (url.endsWith("/api/dataset-experiments") && options?.method === "POST") {
      posted = JSON.parse(String(options.body)) as Record<string, unknown>;
      return new Response(JSON.stringify(experimentWire), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter>
      <ExperimentCreateForm />
    </MemoryRouter>,
  );
  return { urls, posted: () => posted };
}

test("the pipeline choice comes from /api/pipelines and supplies id + version", async () => {
  const { posted } = setup();
  await screen.findByLabelText("Name");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exp" } });
  fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "spacenet" } });
  fireEvent.change(screen.getByLabelText("Dataset split"), { target: { value: "test" } });
  fireEvent.change(screen.getByLabelText("Label space"), { target: { value: "spacenet_14" } });

  fireEvent.mouseDown(screen.getByLabelText("Pipeline"));
  fireEvent.click(await screen.findByTitle(/Dummy Pipeline 1.0/));

  fireEvent.click(screen.getByRole("button", { name: "Create Experiment" }));

  await waitFor(() => expect(posted()).not.toBeNull());
  expect(posted()).toMatchObject({ plugin_id: "dummy", plugin_version: "1.0", execution_mode: "auto" });
  expect(posted()).not.toHaveProperty("evaluation_protocol");
  expect(posted()).not.toHaveProperty("recording_manifest_hash");
});

test("dataset-scoped executor selection uses the dataset triple and never executor-availability", async () => {
  const { urls } = setup();
  await screen.findByLabelText("Name");
  fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "spacenet" } });
  fireEvent.change(screen.getByLabelText("Dataset split"), { target: { value: "test" } });
  fireEvent.change(screen.getByLabelText("Label space"), { target: { value: "spacenet_14" } });
  fireEvent.mouseDown(screen.getByLabelText("Pipeline"));
  fireEvent.click(await screen.findByTitle(/Dummy Pipeline 1.0/));

  await waitFor(() => expect(urls.some((u) => u.includes("/api/executor-selection"))).toBe(true));
  const selectionUrl = urls.find((u) => u.includes("/api/executor-selection")) ?? "";
  expect(selectionUrl).toContain("dataset_name=spacenet");
  expect(selectionUrl).toContain("dataset_split=test");
  expect(selectionUrl).toContain("dataset_label_space=spacenet_14");
  expect(urls.some((u) => u.includes("/api/executor-availability"))).toBe(false);
});
