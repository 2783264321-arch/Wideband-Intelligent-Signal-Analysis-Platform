import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DatasetDetailPage } from "./DatasetDetailPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const datasetWire = {
  id: "ds_1",
  name: "SpaceNet",
  split: "test",
  adapter_id: "spacenet",
  label_space: "spacenet_14",
  local_root: "D:\\SpaceNet",
  portable_fingerprint: "a".repeat(64),
  sample_count: 2500,
  ground_truth_sample_count: 2500,
  created_at: "2026-09-17T00:00:00",
};

const sampleWire = {
  id: "rec_1",
  name: "a1",
  sample_key: "a1",
  data_format: "float16_interleaved_le",
  sample_rate_hz: 1e6,
  center_frequency_hz: 0,
  frequency_low_hz: -5e5,
  frequency_high_hz: 5e5,
  num_samples: 1000,
  duration_s: 0.001,
  has_ground_truth: true,
  analysis_count: 0,
};

const historyWire = {
  dataset_id: "ds_1",
  total: 1,
  items: [
    {
      kind: "imported_batch",
      resource_id: "fp1",
      name: "zoomspec 1.0",
      pipeline_id: "zoomspec",
      pipeline_version: "1.0",
      status: "completed",
      executor: "imported",
      expected_items: 2500,
      completed_items: 2500,
      failed_items: 0,
      coverage: 1.0,
      created_at: "2026-01-01T00:00:00Z",
      dataset_evaluation_id: null,
      batch_id: "b1",
      archive_sha256: "a".repeat(64),
    },
  ],
};

let deleteMode: "ok" | "blocked" = "ok";

function route(url: string, init?: RequestInit): Response {
  const method = init?.method ?? "GET";
  if (method === "DELETE" && url.includes("/api/datasets/ds_1")) {
    if (deleteMode === "blocked") {
      return new Response(
        JSON.stringify({
          error: {
            code: "DATASET_REMOVE_BLOCKED",
            message: "blocked",
            details: {
              blockers: [{ kind: "active_analysis_run", resource_id: "run_1", reference: "analysis_run" }],
            },
          },
        }),
        { status: 409 },
      );
    }
    return new Response(null, { status: 204 });
  }
  if (url.includes("/analysis-history")) {
    return new Response(JSON.stringify(historyWire), { status: 200 });
  }
  if (url.includes("/samples")) {
    return new Response(JSON.stringify({ dataset_id: "ds_1", items: [sampleWire], total: 1 }), { status: 200 });
  }
  if (url.includes("/api/datasets/ds_1")) {
    return new Response(JSON.stringify(datasetWire), { status: 200 });
  }
  return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderPage() {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library/datasets/ds_1"]}>
        <Routes>
          <Route path="/data-library/datasets/:datasetId" element={<DatasetDetailPage />} />
          <Route path="/samples/:recordingId" element={<LocationProbe />} />
          <Route path="/experiments" element={<LocationProbe />} />
          <Route path="/data-library" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  deleteMode = "ok";
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

function fetchCalls(): unknown[][] {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
}

test("overview shows first-class dataset metadata", async () => {
  renderPage();
  const overview = await screen.findByTestId("dataset-overview");
  expect(overview).toHaveTextContent("SpaceNet");
  expect(overview).toHaveTextContent("test");
  expect(overview).toHaveTextContent("2500");
  expect(overview).toHaveTextContent("spacenet_14");
  expect(overview).toHaveTextContent("spacenet");
  expect(overview).toHaveTextContent("D:\\SpaceNet");
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/datasets/ds_1"))).toBe(true);
});

test("samples tab uses the first-class samples endpoint and opens the Sample page", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Samples" }));
  expect(await screen.findByText("a1")).toBeInTheDocument();
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/datasets/ds_1/samples"))).toBe(true);
  const open = await screen.findByRole("button", { name: "Open Sample" });
  fireEvent.click(open);
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/samples/rec_1");
});

test("analysis history uses the first-class dataset history endpoint", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  const item = await screen.findByTestId("analysis-history-item");
  expect(item).toHaveTextContent("Imported batch");
  expect(item).toHaveTextContent("2500 / 2500");
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/datasets/ds_1/analysis-history"))).toBe(true);
});

test("Import Batch Analysis Results opens the batch import modal", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  fireEvent.click(await screen.findByRole("button", { name: "Import Batch Analysis Results" }));
  expect(await screen.findByText("Import Batch Analysis Package")).toBeInTheDocument();
});

test("Remove Dataset confirms external-file preservation and navigates back on success", async () => {
  renderPage();
  await screen.findByTestId("dataset-overview");
  fireEvent.click(screen.getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByText(/NOT deleted/)).toBeInTheDocument();
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library");
  expect(
    fetchCalls().some(
      (call) => String(call[0]).includes("/api/datasets/ds_1") && (call[1] as RequestInit | undefined)?.method === "DELETE",
    ),
  ).toBe(true);
});

test("blocked removal stays on the page and renders the blocker", async () => {
  deleteMode = "blocked";
  renderPage();
  await screen.findByTestId("dataset-overview");
  fireEvent.click(screen.getByRole("button", { name: "Remove Dataset" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("delete-conflict-alert")).toHaveTextContent("run_1");
});

test("dataset history compare entry selects only completed evaluations", async () => {
  const richHistory = {
    dataset_id: "ds_1",
    total: 3,
    items: [
      { kind: "evaluation", resource_id: "eval_a", name: "Eval A", pipeline_id: "p", pipeline_version: "1.0", status: "completed", executor: null, expected_items: 1, completed_items: 1, failed_items: 0, coverage: 1.0, created_at: null, dataset_evaluation_id: "eval_a", batch_id: null, archive_sha256: null },
      { kind: "evaluation", resource_id: "eval_b", name: "Eval B", pipeline_id: "p", pipeline_version: "1.0", status: "completed", executor: null, expected_items: 1, completed_items: 1, failed_items: 0, coverage: 1.0, created_at: null, dataset_evaluation_id: "eval_b", batch_id: null, archive_sha256: null },
      { kind: "evaluation", resource_id: "eval_c", name: "Eval C", pipeline_id: "p", pipeline_version: "1.0", status: "pending", executor: null, expected_items: 1, completed_items: 0, failed_items: 0, coverage: 0.0, created_at: null, dataset_evaluation_id: "eval_c", batch_id: null, archive_sha256: null },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/analysis-history")) return new Response(JSON.stringify(richHistory), { status: 200 });
    if (String(url).includes("/samples")) return new Response(JSON.stringify({ dataset_id: "ds_1", items: [], total: 0 }), { status: 200 });
    if (String(url).includes("/api/datasets/ds_1")) return new Response(JSON.stringify(datasetWire), { status: 200 });
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  }));
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  const entry = await screen.findByTestId("dataset-analysis-compare-entry");
  const checkboxes = within(entry).getAllByRole("checkbox");
  expect(checkboxes).toHaveLength(2);
  expect(within(entry).queryByRole("checkbox", { name: "eval_c" })).toBeNull();
  fireEvent.click(within(entry).getByRole("checkbox", { name: "eval_a" }));
  fireEvent.click(within(entry).getByRole("checkbox", { name: "eval_b" }));
  fireEvent.click(within(entry).getByRole("button", { name: "Compare" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/experiments?tab=compare&a=eval_a&b=eval_b",
  );
});
