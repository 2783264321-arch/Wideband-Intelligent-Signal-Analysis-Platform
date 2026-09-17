import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SampleDetailPage } from "./SampleDetailPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const runWire = {
  id: "run_1",
  recording_id: "rec_1",
  pipeline_id: "stft_energy_detector",
  pipeline_version: "1.0",
  executor: "local_cpu",
  status: "completed",
  parameters_json: {},
  created_at: "2026-01-01T00:00:00Z",
};

function recordingWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "rec_1",
    name: "sample-a",
    data_format: "complex64_le",
    source: "custom",
    external_path: "D:\\signals\\a.iq",
    sample_rate_hz: 1e6,
    center_frequency_hz: 2.441e9,
    frequency_low_hz: 2.4405e9,
    frequency_high_hz: 2.4415e9,
    num_samples: 1000,
    duration_s: 0.001,
    dataset_name: null,
    dataset_split: null,
    label_space: null,
    has_ground_truth: false,
    dataset_id: null,
    sample_key: null,
    ...overrides,
  };
}

function route(url: string, init?: RequestInit): Response {
  const method = init?.method ?? "GET";
  if (method === "DELETE" && url.includes("/api/recordings/rec_1")) {
    return new Response(null, { status: 204 });
  }
  if (url.includes("/api/analysis-runs")) {
    return new Response(JSON.stringify([runWire]), { status: 200 });
  }
  if (url.includes("/api/recordings/rec_1")) {
    return new Response(JSON.stringify(currentRecording), { status: 200 });
  }
  return new Response(JSON.stringify([]), { status: 200 });
}

let currentRecording = recordingWire();

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderPage() {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/samples/rec_1"]}>
        <Routes>
          <Route path="/samples/:recordingId" element={<SampleDetailPage />} />
          <Route path="/spectrum/:recordingId" element={<LocationProbe />} />
          <Route path="/data-library/datasets/:datasetId" element={<LocationProbe />} />
          <Route path="/data-library" element={<LocationProbe />} />
          <Route path="/signals/:runId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  currentRecording = recordingWire();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

test("renders a dataset member with dataset context and no Delete action", async () => {
  currentRecording = recordingWire({
    dataset_name: "SpaceNet",
    dataset_split: "test",
    dataset_id: "ds_1",
    sample_key: "a",
  });
  renderPage();
  expect(await screen.findByText("sample-a")).toBeInTheDocument();
  const context = await screen.findByTestId("sample-context");
  expect(context).toHaveTextContent("Dataset Sample");
  expect(context).toHaveTextContent("SpaceNet");
  // Analyze is always available; Delete is not offered for dataset members.
  expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
});

test("dataset member links back to the Dataset page", async () => {
  currentRecording = recordingWire({ dataset_name: "SpaceNet", dataset_split: "test", dataset_id: "ds_1" });
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Open Dataset" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library/datasets/ds_1");
});

test("renders a standalone member with Delete and Analyze", async () => {
  renderPage();
  expect(await screen.findByText("sample-a")).toBeInTheDocument();
  const context = await screen.findByTestId("sample-context");
  expect(context).toHaveTextContent("Standalone Sample");
  expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
});

test("both kinds show analysis history with View Results", async () => {
  renderPage();
  const item = await screen.findByTestId("run-history-item");
  expect(item).toHaveTextContent("stft_energy_detector");
  fireEvent.click(within(item).getByRole("button", { name: "View Results" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/signals/run_1");
});

test("Analyze opens the real spectrum workspace for both kinds", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Analyze" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/spectrum/rec_1");
});
