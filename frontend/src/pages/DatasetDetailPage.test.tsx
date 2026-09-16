import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

function route(url: string): Response {
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

function renderPage() {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library/datasets/dsproj_1"]}>
        <Routes>
          <Route path="/data-library/datasets/:datasetProjectionId" element={<DatasetDetailPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => route(String(url))),
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
