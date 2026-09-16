import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DatasetDetailPage } from "./DatasetDetailPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const datasetWire = {
  dataset_projection_id: "dsproj_1",
  source: "spacenet",
  dataset_name: "SpaceNet",
  dataset_split: "test",
  label_space: "spacenet_14",
  sample_count: 2500,
  ground_truth_sample_count: 2500,
  external: true,
  source_location: "D:\\SpaceNet\\test",
};

const sampleWire = {
  id: "rec_1",
  name: "a1",
  sample_rate_hz: 1e6,
  center_frequency_hz: 0,
  frequency_low_hz: -5e5,
  frequency_high_hz: 5e5,
  duration_s: 0.001,
  has_ground_truth: true,
  analysis_count: 0,
  sample_rate_derived: true,
  center_frequency_derived: true,
};

const historyWire = {
  dataset_projection_id: "dsproj_1",
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
  if (method === "DELETE" && url.includes("/api/data-library/datasets/dsproj_1")) {
    if (deleteMode === "blocked") {
      return new Response(
        JSON.stringify({
          error: {
            code: "DATASET_REMOVE_BLOCKED",
            message: "blocked",
            details: {
              blockers: [
                { kind: "active_analysis_run", resource_id: "run_1", reference: "analysis_run" },
              ],
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
    return new Response(
      JSON.stringify({ dataset_projection_id: "dsproj_1", items: [sampleWire], total: 1 }),
      { status: 200 },
    );
  }
  if (url.includes("/api/data-library/datasets/dsproj_1")) {
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
      <MemoryRouter initialEntries={["/data-library/datasets/dsproj_1"]}>
        <Routes>
          <Route path="/data-library/datasets/:datasetProjectionId" element={<DatasetDetailPage />} />
          <Route path="/experiments" element={<LocationProbe />} />
          <Route path="/data-library" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  deleteMode = "ok";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)),
  );
});
afterEach(() => vi.unstubAllGlobals());

test("overview shows the aggregate projection metadata", async () => {
  renderPage();
  expect(await screen.findByTestId("dataset-overview")).toHaveTextContent("SpaceNet");
  expect(screen.getByTestId("dataset-overview")).toHaveTextContent("2500");
});

test("samples table shows the platform-derived marker and searches server-side", async () => {
  const { container } = renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Samples" }));
  expect(await screen.findByText(/Derived/)).toBeInTheDocument();
  const searchInput = container.querySelector("input") as HTMLInputElement;
  fireEvent.change(searchInput, { target: { value: "a1" } });
  fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });
  const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
  expect(calls.some((call) => String(call[0]).includes("search=a1"))).toBe(true);
});

test("analysis history renders an imported batch before any evaluation", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  const item = await screen.findByTestId("analysis-history-item");
  expect(item).toHaveTextContent("Imported batch");
  expect(item).toHaveTextContent("2500 / 2500");
});

test("Create Experiment navigates with the exact datasetProjectionId", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  fireEvent.click(await screen.findByRole("button", { name: "Create Dataset Experiment" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/experiments?datasetProjectionId=dsproj_1",
  );
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

test("dataset history compare entry selects only evaluations and navigates with a/b", async () => {
  const richHistory = {
    dataset_projection_id: "dsproj_1",
    total: 3,
    items: [
      { kind: "evaluation", resource_id: "eval_a", name: "Eval A", pipeline_id: "p", pipeline_version: "1.0", status: "completed", executor: null, expected_items: 1, completed_items: 1, failed_items: 0, coverage: 1.0, created_at: null, dataset_evaluation_id: "eval_a", batch_id: null, archive_sha256: null },
      { kind: "evaluation", resource_id: "eval_b", name: "Eval B", pipeline_id: "p", pipeline_version: "1.0", status: "completed", executor: null, expected_items: 1, completed_items: 1, failed_items: 0, coverage: 1.0, created_at: null, dataset_evaluation_id: "eval_b", batch_id: null, archive_sha256: null },
      { kind: "evaluation", resource_id: "eval_c", name: "Eval C", pipeline_id: "p", pipeline_version: "1.0", status: "pending", executor: null, expected_items: 1, completed_items: 0, failed_items: 0, coverage: 0.0, created_at: null, dataset_evaluation_id: "eval_c", batch_id: null, archive_sha256: null },
      { kind: "evaluation", resource_id: "eval_d", name: "Eval D", pipeline_id: "p", pipeline_version: "1.0", status: "failed", executor: null, expected_items: 1, completed_items: 0, failed_items: 1, coverage: 0.0, created_at: null, dataset_evaluation_id: "eval_d", batch_id: null, archive_sha256: null },
      { kind: "imported_batch", resource_id: "fp1", name: "zoom 1.0", pipeline_id: "zoom", pipeline_version: "1.0", status: "completed", executor: "imported", expected_items: 1, completed_items: 1, failed_items: 0, coverage: 1.0, created_at: null, dataset_evaluation_id: null, batch_id: "b", archive_sha256: null },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url).includes("/analysis-history")) return new Response(JSON.stringify(richHistory), { status: 200 });
    if (String(url).includes("/samples")) return new Response(JSON.stringify({ dataset_projection_id: "dsproj_1", items: [], total: 0 }), { status: 200 });
    if (String(url).includes("/api/data-library/datasets/dsproj_1")) return new Response(JSON.stringify(datasetWire), { status: 200 });
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  }));
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Analysis History" }));
  const entry = await screen.findByTestId("dataset-analysis-compare-entry");
  const checkboxes = within(entry).getAllByRole("checkbox");
  expect(checkboxes).toHaveLength(2);
  expect(within(entry).queryByRole("checkbox", { name: "eval_c" })).toBeNull();
  expect(within(entry).queryByRole("checkbox", { name: "eval_d" })).toBeNull();
  expect(within(entry).queryByRole("checkbox", { name: "fp1" })).toBeNull();
  fireEvent.click(within(entry).getByRole("checkbox", { name: "eval_a" }));
  fireEvent.click(within(entry).getByRole("checkbox", { name: "eval_b" }));
  fireEvent.click(within(entry).getByRole("button", { name: "Compare" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/experiments?tab=compare&a=eval_a&b=eval_b",
  );
});
