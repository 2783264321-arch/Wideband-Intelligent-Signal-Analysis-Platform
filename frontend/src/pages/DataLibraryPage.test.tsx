import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DataLibraryPage } from "./DataLibraryPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const dataset = {
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
  if (method === "DELETE" && url.includes("/api/datasets/ds_1")) {
    return new Response(
      JSON.stringify({
        error: {
          code: "DATASET_REMOVE_BLOCKED",
          message: "blocked",
          details: {
            blockers: [{ kind: "dataset_evaluation", resource_id: "eval_1", reference: "recording" }],
          },
        },
      }),
      { status: 409 },
    );
  }
  if (url.includes("/api/data-library/standalone-samples")) {
    return new Response(JSON.stringify({ items: [standalone], total: 1 }), { status: 200 });
  }
  if (url.includes("/api/datasets")) {
    return new Response(JSON.stringify({ items: [dataset], total: 1 }), { status: 200 });
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
      <MemoryRouter initialEntries={["/data-library"]}>
        <Routes>
          <Route path="/data-library" element={<DataLibraryPage />} />
          <Route path="/samples/:recordingId" element={<LocationProbe />} />
          <Route path="/data-library/datasets/:datasetId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

function fetchCalls(): unknown[][] {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
}

test("dataset cards come from the first-class dataset API", async () => {
  renderPage();
  const cards = await screen.findAllByTestId("dataset-card");
  expect(cards).toHaveLength(1);
  expect(screen.getByText("SpaceNet · test")).toBeInTheDocument();
  expect(screen.getByText(/Samples 2500/)).toBeInTheDocument();
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/datasets?limit=50&offset=0"))).toBe(true);
  expect(fetchCalls().some((call) => String(call[0]).includes("/api/data-library/datasets"))).toBe(false);
});

test("standalone tab requests server-side pagination", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(screen.getByRole("tab", { name: "Standalone Samples" }));
  expect(await screen.findByTestId("standalone-card")).toBeInTheDocument();
  expect(fetchCalls().some((call) => String(call[0]).includes("standalone-samples?limit=20&offset=0"))).toBe(true);
});

test("dataset removal confirmation states external files are not deleted", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(within(screen.getAllByTestId("dataset-card")[0]).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByText(/NOT deleted/)).toBeInTheDocument();
});

test("blocked removal uses the datasetId endpoint and renders the structured blocker", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(within(screen.getAllByTestId("dataset-card")[0]).getByRole("button", { name: "Remove Dataset" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove Dataset" }));
  expect(await screen.findByTestId("delete-conflict-alert")).toHaveTextContent("eval_1");
  const deleteCall = fetchCalls().find(
    (call) => String(call[0]).includes("/api/datasets/ds_1") && (call[1] as RequestInit | undefined)?.method === "DELETE",
  );
  expect(deleteCall).toBeDefined();
});

test("dataset Browse Samples navigates with datasetId", async () => {
  renderPage();
  const card = await screen.findByTestId("dataset-card");
  fireEvent.click(within(card).getByRole("button", { name: "Browse Samples" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library/datasets/ds_1");
});

test("standalone sample opens the unified Sample page", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.click(screen.getByRole("tab", { name: "Standalone Samples" }));
  const card = await screen.findByTestId("standalone-card");
  fireEvent.click(within(card).getByRole("button", { name: "Open Sample" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/samples/rec_s1");
});

test("Add Standalone IQ switches between Upload File and Register Local Path", async () => {
  renderPage();
  await screen.findAllByTestId("dataset-card");
  fireEvent.mouseEnter(screen.getByTestId("add-data-button"));
  fireEvent.click(await screen.findByText("Add Standalone IQ"));
  expect(await screen.findByRole("tab", { name: "Upload File" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Register Local Path" }));
  expect(await screen.findByLabelText("Local path")).toBeInTheDocument();
  expect(screen.getByText("Data format")).toBeInTheDocument();
});
