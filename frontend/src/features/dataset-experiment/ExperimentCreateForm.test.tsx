import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentCreateForm } from "./ExperimentCreateForm";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

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
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentCreateForm />
      </MemoryRouter>,
    ),
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

  await waitFor(() => expect(screen.getByRole("button", { name: "Create Experiment" })).not.toBeDisabled());
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

// ---------------------------------------------------------------------------
// F3 corrective A1 — create form must respect the CURRENT backend selection
// ---------------------------------------------------------------------------

const noRunnableSelection = {
  requested_mode: "auto",
  resolved_executor: null,
  reason_code: "AUTO_NO_RUNNABLE_EXECUTOR",
  reason: "No runnable executor.",
  workload_class: "UNKNOWN",
  candidates: [],
};

const dualAvailableSelection = {
  requested_mode: "auto",
  resolved_executor: "local_cpu",
  reason_code: "AUTO_LOCAL_CPU_PREFERRED",
  reason: "Local CPU preferred.",
  workload_class: "SMALL",
  candidates: [
    { executor: "local_cpu", technical: true, configured: true, certified: true, available: true, reason_code: null, reason_message: null },
    { executor: "local_gpu", technical: true, configured: true, certified: true, available: true, reason_code: null, reason_message: null },
  ],
};

function authoritySetup(options: { selection: unknown; secondSelection?: unknown; deferSecond?: boolean }) {
  const posted: Record<string, unknown>[] = [];
  let resolveSecond: ((value: Response) => void) | undefined;
  const secondDeferred = new Promise<Response>((resolve) => { resolveSecond = resolve; });
  let callCount = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, fetchOptions?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.includes("/api/executor-availability")) throw new Error("dataset scope must not call executor-availability");
    if (url.includes("/api/executor-selection")) {
      callCount += 1;
      if (callCount >= 2) {
        if (options.deferSecond) return secondDeferred;
        if (options.secondSelection !== undefined) return new Response(JSON.stringify(options.secondSelection));
      }
      return new Response(JSON.stringify(options.selection));
    }
    if (url.endsWith("/api/dataset-experiments") && fetchOptions?.method === "POST") {
      posted.push(JSON.parse(String(fetchOptions.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(experimentWire), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentCreateForm />
      </MemoryRouter>,
    ),
  );
  return { posted, resolveSecond: () => resolveSecond };
}

async function fillIdentityAndPipeline() {
  await screen.findByLabelText("Name");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exp" } });
  fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "spacenet" } });
  fireEvent.change(screen.getByLabelText("Dataset split"), { target: { value: "test" } });
  fireEvent.change(screen.getByLabelText("Label space"), { target: { value: "spacenet_14" } });
  fireEvent.mouseDown(screen.getByLabelText("Pipeline"));
  fireEvent.click(await screen.findByTitle(/Dummy Pipeline 1.0/));
}

test("AUTO_NO_RUNNABLE_EXECUTOR disables Create and cannot POST", async () => {
  const { posted } = authoritySetup({ selection: noRunnableSelection });
  await fillIdentityAndPipeline();
  const button = screen.getByRole("button", { name: "Create Experiment" });
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);
  expect(posted).toHaveLength(0);
});

test("a scope change resets manual environment and cannot authorize the new scope", async () => {
  const { posted, resolveSecond } = authoritySetup({
    selection: dualAvailableSelection,
    secondSelection: dualAvailableSelection,
    deferSecond: true,
  });
  await fillIdentityAndPipeline();
  const button = screen.getByRole("button", { name: "Create Experiment" });
  await waitFor(() => expect(button).not.toBeDisabled());

  fireEvent.click(screen.getByText("Local GPU"));
  await waitFor(() => expect(button).not.toBeDisabled());

  // Change dataset identity -> new scope; selection B is deferred.
  fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "other" } });
  await waitFor(() => expect(button).toBeDisabled());
  fireEvent.click(button);
  expect(posted).toHaveLength(0);

  // B resolves valid Auto -> Create becomes enabled again.
  await act(async () => { resolveSecond()?.(new Response(JSON.stringify(dualAvailableSelection))); await Promise.resolve(); });
  await waitFor(() => expect(button).not.toBeDisabled());
});

test("a manual current-scope available executor posts the exact executor", async () => {
  const { posted } = authoritySetup({ selection: dualAvailableSelection });
  await fillIdentityAndPipeline();
  await waitFor(() => expect(screen.getByRole("button", { name: "Create Experiment" })).not.toBeDisabled());
  fireEvent.click(screen.getByText("Local GPU"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Create Experiment" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Create Experiment" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ executor: "local_gpu", execution_mode: "manual" });
});

test("localizes the create-form shell and submit control in zh-CN without changing the API payload", async () => {
  let posted: Record<string, unknown> | null = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.includes("/api/executor-selection")) return new Response(JSON.stringify(selectionWire));
    if (url.endsWith("/api/dataset-experiments") && options?.method === "POST") {
      posted = JSON.parse(String(options.body)) as Record<string, unknown>;
      return new Response(JSON.stringify(experimentWire), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentCreateForm />
      </MemoryRouter>,
      { locale: "zh-CN" },
    ),
  );
  await screen.findByLabelText("名称");
  expect(screen.queryByRole("button", { name: "Create Experiment" })).toBeNull();
  expect(screen.getByLabelText("算法流水线")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Exp" } });
  fireEvent.change(screen.getByLabelText("数据集名称"), { target: { value: "spacenet" } });
  fireEvent.change(screen.getByLabelText("数据集划分"), { target: { value: "test" } });
  fireEvent.change(screen.getByLabelText("标签空间"), { target: { value: "spacenet_14" } });
  fireEvent.mouseDown(screen.getByLabelText("算法流水线"));
  fireEvent.click(await screen.findByTitle(/Dummy Pipeline 1.0/));

  const createButton = screen.getByRole("button", { name: "创建数据集实验" });
  await waitFor(() => expect(createButton).not.toBeDisabled());
  fireEvent.click(createButton);

  await waitFor(() => expect(posted).not.toBeNull());
  expect(posted as unknown as Record<string, unknown>).toMatchObject({
    plugin_id: "dummy",
    plugin_version: "1.0",
    execution_mode: "auto",
  });
});

import { Route, Routes } from "react-router-dom";

test("projection prefill is read-only, uses projection scope, and sends datasetProjectionId", async () => {
  const calls: { url: string; body?: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/pipelines")) {
      return new Response(JSON.stringify([{
        id: "p", name: "P", version: "1.0", label_space: "spacenet_14",
        recommended_device: "cpu", cpu_supported: true, stages: [], inspectable_stages: [],
        task_capability: "detection", executors_supported: ["local_cpu"], recommended_executor: "local_cpu",
        technical_execution_capabilities: [{ executor: "local_cpu", deviceType: "cpu", precision: "float32" }],
      }]), { status: 200 });
    }
    if (u.includes("/api/executor-selection")) {
      calls.push({ url: u });
      return new Response(JSON.stringify({
        requested_mode: "auto", resolved_executor: "local_cpu",
        reason_code: "AUTO_ONLY_RUNNABLE_EXECUTOR", reason: "ok", workload_class: "SMALL",
        candidates: [{ executor: "local_cpu", technical: true, configured: true, certified: true, available: true, reason_code: null, reason_message: null }],
      }), { status: 200 });
    }
    if (u.includes("/api/dataset-experiments")) {
      calls.push({ url: u, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify({ id: "exp_1" }), { status: 201 });
    }
    return new Response("{}", { status: 200 });
  }));

  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={
            <ExperimentCreateForm initialDataset={{
              datasetProjectionId: "dsproj_A", datasetName: "SpaceNet",
              datasetSplit: "test", datasetLabelSpace: "spacenet_14",
            }} />
          } />
          <Route path="/experiments/:id" element={<div />} />
        </Routes>
      </MemoryRouter>,
    ),
  );

  expect(await screen.findByDisplayValue("SpaceNet")).toHaveAttribute("readonly");
  expect(screen.getByDisplayValue("test")).toHaveAttribute("readonly");
  expect(screen.getByDisplayValue("spacenet_14")).toHaveAttribute("readonly");

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exp" } });
  fireEvent.mouseDown(screen.getByLabelText("Pipeline"));
  fireEvent.click(await screen.findByTitle(/P 1.0/));

  await waitFor(() => {
    expect(calls.some((call) => call.url.includes("dataset_projection_id=dsproj_A"))).toBe(true);
  });

  fireEvent.click(screen.getByRole("button", { name: "Create Experiment" }));
  await waitFor(() => {
    expect(calls.some((call) => (call.body as { dataset_projection_id?: string } | undefined)?.dataset_projection_id === "dsproj_A")).toBe(true);
  });
});
