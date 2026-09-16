import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { DataLibraryPage } from "./DataLibraryPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const dataset = {
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

const standalone = {
  id: "rec_s1",
  name: "standalone-a",
  source: "custom",
  sample_rate_hz: 1e6,
  center_frequency_hz: 0,
  frequency_low_hz: -5e5,
  frequency_high_hz: 5e5,
  duration_s: 0.001,
  data_format: "complex64_le",
  has_ground_truth: false,
  analysis_count: 0,
};

function route(url: string, init?: RequestInit): Response {
  const method = init?.method ?? "GET";
  if (method === "DELETE" && url.includes("/api/data-library/datasets/dsproj_1")) {
    return new Response(
      JSON.stringify({
        error: {
          code: "DATASET_REMOVE_BLOCKED",
          message: "blocked",
          details: {
            blockers: [
              { kind: "dataset_evaluation", resource_id: "eval_1", reference: "recording" },
            ],
          },
        },
      }),
      { status: 409 },
    );
  }
  if (url.includes("/api/data-library/datasets")) {
    return new Response(JSON.stringify({ items: [dataset], total: 1 }), { status: 200 });
  }
  if (url.includes("/api/data-library/standalone-samples")) {
    return new Response(JSON.stringify({ items: [standalone], total: 1 }), { status: 200 });
  }
  return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
}

function renderPage() {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library"]}>
        <Routes>
          <Route path="/data-library" element={<DataLibraryPage />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)),
  );
});
afterEach(() => vi.unstubAllGlobals());

test("renders one dataset projection card, not member recordings", async () => {
  renderPage();
  const cards = await screen.findAllByTestId("dataset-card");
  expect(cards).toHaveLength(1);
  expect(screen.getByText("SpaceNet · test")).toBeInTheDocument();
  expect(screen.getByText(/Samples 2500/)).toBeInTheDocument();
});

test("standalone tab requests server-side pagination", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(screen.getByRole("tab", { name: "Standalone Samples" }));
  expect(await screen.findByTestId("standalone-card")).toBeInTheDocument();
  const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
  expect(
    calls.some((call) => String(call[0]).includes("standalone-samples?limit=20&offset=0")),
  ).toBe(true);
});

test("dataset removal confirmation states external files are not deleted", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(screen.getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByText(/NOT deleted/)).toBeInTheDocument();
});

test("blocked removal renders the structured blocker", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(within(screen.getAllByTestId("dataset-card")[0]).getByRole("button", { name: "Remove Dataset" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("delete-conflict-alert")).toHaveTextContent("eval_1");
});

test("card import results opens the batch import modal", async () => {
  renderPage();
  const card = await screen.findByTestId("dataset-card");
  fireEvent.click(within(card).getByRole("button", { name: "Import Analysis Results" }));
  expect(await screen.findByText("Import Batch Analysis Package")).toBeInTheDocument();
});
