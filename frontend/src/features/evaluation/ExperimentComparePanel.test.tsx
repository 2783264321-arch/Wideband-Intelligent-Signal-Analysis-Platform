import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentComparePanel } from "./ExperimentComparePanel";

function experimentWire(id: string, name: string, evaluationId: string | null) {
  return {
    id,
    name,
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
    status: "completed",
    dataset_evaluation_id: evaluationId,
    error_type: null,
    error_message: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    requested_execution_mode: "auto",
    auto_reason_code: null,
    auto_reason: null,
    workload_class: null,
    expected_items: 3,
    queued_items: 0,
    running_items: 0,
    completed_items: 3,
    failed_items: 0,
    attempt_count: 3,
  };
}

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

test("compares the linked evaluations and renders all reasons when not comparable", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    urls.push(`${options?.method ?? "GET"} ${url}`);
    if (url.endsWith("/api/dataset-experiments")) {
      return new Response(JSON.stringify([experimentWire("exp_a", "Exp A", "eval_a"), experimentWire("exp_b", "Exp B", "eval_b")]));
    }
    if (url.includes("/api/dataset-benchmarks/compare")) {
      return new Response(JSON.stringify({
        comparable: false,
        reasons: ["label_space_mismatch", "evaluation_protocol_mismatch"],
        evaluation_a_id: "eval_a",
        evaluation_b_id: "eval_b",
        aggregate_a: null,
        aggregate_b: null,
        deltas: {},
      }));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));

  render(
    <MemoryRouter>
      <ExperimentComparePanel />
    </MemoryRouter>,
  );

  await screen.findByLabelText("Experiment A");
  fireEvent.mouseDown(screen.getByLabelText("Experiment A"));
  const aOptions = await screen.findAllByTitle("Exp A");
  fireEvent.click(aOptions[aOptions.length - 1]);
  fireEvent.mouseDown(screen.getByLabelText("Experiment B"));
  const bOptions = await screen.findAllByTitle("Exp B");
  fireEvent.click(bOptions[bOptions.length - 1]);

  fireEvent.click(screen.getByRole("button", { name: "Compare" }));

  await waitFor(() => expect(screen.getByText("label_space_mismatch")).toBeInTheDocument());
  expect(screen.getByText("evaluation_protocol_mismatch")).toBeInTheDocument();
  expect(urls.some((u) => u.includes("/api/dataset-benchmarks/compare"))).toBe(true);
});

// ---------------------------------------------------------------------------
// Review corrective A3 — bind comparison result to the A/B identity
// ---------------------------------------------------------------------------

function threeExperiments() {
  return [
    experimentWire("exp_a", "Exp A", "eval_a"),
    experimentWire("exp_b", "Exp B", "eval_b"),
    experimentWire("exp_c", "Exp C", "eval_c"),
  ];
}

test("changing a selected experiment clears the previous comparison result", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/api/dataset-experiments")) return new Response(JSON.stringify(threeExperiments()));
    if (url.includes("/api/dataset-benchmarks/compare")) {
      return new Response(JSON.stringify({
        comparable: true, reasons: [], evaluation_a_id: "eval_a", evaluation_b_id: "eval_b",
        aggregate_a: null, aggregate_b: null, deltas: { localization_ap50: 0.07 },
      }));
    }
    if (url.includes("/items")) return new Response(JSON.stringify([]));
    throw new Error(`Unexpected request: ${url}`);
  }));

  render(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>);
  await screen.findByLabelText("Experiment A");
  fireEvent.mouseDown(screen.getByLabelText("Experiment A"));
  const aOpts = await screen.findAllByTitle("Exp A");
  fireEvent.click(aOpts[aOpts.length - 1]);
  fireEvent.mouseDown(screen.getByLabelText("Experiment B"));
  const bOpts = await screen.findAllByTitle("Exp B");
  fireEvent.click(bOpts[bOpts.length - 1]);
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByTestId("compare-delta-table")).toBeInTheDocument();

  // Change A -> the previous A/B result must disappear immediately.
  fireEvent.mouseDown(screen.getByLabelText("Experiment A"));
  const cOpts = await screen.findAllByTitle("Exp C");
  fireEvent.click(cOpts[cOpts.length - 1]);
  expect(screen.queryByTestId("compare-delta-table")).toBeNull();
});
