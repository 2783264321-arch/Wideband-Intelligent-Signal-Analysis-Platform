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

const waveformWire = { time_s: [0, 0.0005, 0.001], i: [1, 0, -1], q: [0, 1, 0] };
const spectrumWire = {
  frequency_hz: [2.4405e9, 2.441e9, 2.4415e9],
  power_db: [-60, -8, -70],
  fft_size: 4096,
  segment_count: 8,
};
const spectrogramWire = {
  representation: "stft",
  image_url: "/media/spectrograms/x.png",
  t_start_s: 0,
  t_end_s: 0.001,
  f_low_hz: 2.4405e9,
  f_high_hz: 2.4415e9,
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
  if (url.includes("/waveform")) return new Response(JSON.stringify(waveformWire), { status: 200 });
  if (url.includes("/spectrum")) return new Response(JSON.stringify(spectrumWire), { status: 200 });
  if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogramWire), { status: 200 });
  if (url.includes("/api/analysis-runs")) return new Response(JSON.stringify([runWire]), { status: 200 });
  if (url.includes("/api/recordings/rec_1")) return new Response(JSON.stringify(currentRecording), { status: 200 });
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

function fetchCalls(): string[] {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((call) => String(call[0]));
}

beforeEach(() => {
  currentRecording = recordingWire();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)));
});
afterEach(() => vi.unstubAllGlobals());

test("renders the three user-facing tabs with the representation sub-tabs", async () => {
  renderPage();
  await screen.findByText("sample-a");
  for (const label of ["Sample Info", "Visualize", "Detections"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
  expect(screen.queryByRole("tab", { name: "Overview" })).toBeNull();

  // Representation sub-tabs live inside the Visualize tab.
  fireEvent.click(screen.getByRole("tab", { name: "Visualize" }));
  await screen.findByRole("tab", { name: "Time Domain" });
  for (const label of ["Time Domain", "Spectrum", "Spectrogram"]) {
    expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
  }
});

test("representations are lazy: nothing fetched until its sub-tab is opened", async () => {
  renderPage();
  await screen.findByText("sample-a");
  expect(fetchCalls().some((url) => url.includes("/waveform"))).toBe(false);
  expect(fetchCalls().some((url) => url.includes("/spectrum"))).toBe(false);
  expect(fetchCalls().some((url) => url.includes("/spectrogram"))).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "Visualize" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Time Domain" }));
  await screen.findByTestId("line-series-I");
  expect(fetchCalls().some((url) => url.includes("/waveform"))).toBe(true);
  expect(fetchCalls().some((url) => url.includes("/spectrum"))).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "Spectrum" }));
  await screen.findByTestId("spectrum-plot");
  expect(fetchCalls().some((url) => url.includes("/spectrum"))).toBe(true);
  expect(fetchCalls().some((url) => url.includes("/spectrogram"))).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: "Spectrogram" }));
  await screen.findByTestId("sample-spectrogram");
  expect(fetchCalls().some((url) => url.includes("/spectrogram"))).toBe(true);
});

test("Time Domain I/Q renders two series; Magnitude and Phase derive client-side", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Visualize" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Time Domain" }));
  expect(await screen.findByTestId("line-series-I")).toBeInTheDocument();
  expect(screen.getByTestId("line-series-Q")).toBeInTheDocument();

  fireEvent.click(screen.getByText("Magnitude"));
  expect(await screen.findByTestId("line-series-Magnitude")).toBeInTheDocument();
  expect(screen.queryByTestId("line-series-Q")).toBeNull();

  fireEvent.click(screen.getByText("Phase"));
  expect(await screen.findByTestId("line-series-Phase")).toBeInTheDocument();
});

test("Spectrum renders frequency/power data with a secondary FFT summary", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Visualize" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Spectrum" }));
  expect(await screen.findByTestId("spectrum-plot")).toBeInTheDocument();
  expect(screen.getByTestId("spectrum-summary")).toHaveTextContent("FFT 4096 · 8 segments");
  expect(screen.getByTestId("line-series-Power (dB)")).toBeInTheDocument();
});

test("pure Spectrogram never shows the overlay legend", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Visualize" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Spectrogram" }));
  await screen.findByTestId("sample-spectrogram");
  expect(screen.queryByTestId("spectrogram-legend")).toBeNull();
  expect(screen.queryByText("Prediction overlay")).toBeNull();
  expect(screen.queryByText("Selected prediction overlay")).toBeNull();
});

test("renders a dataset member with dataset context and no Delete action", async () => {
  currentRecording = recordingWire({ dataset_name: "SpaceNet", dataset_split: "test", dataset_id: "ds_1", sample_key: "a" });
  renderPage();
  expect(await screen.findByText("sample-a")).toBeInTheDocument();
  const context = await screen.findByTestId("sample-context");
  expect(context).toHaveTextContent("Dataset Sample");
  expect(context).toHaveTextContent("SpaceNet");
  expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
});

test("renders a standalone member with Delete and Analyze", async () => {
  renderPage();
  expect(await screen.findByText("sample-a")).toBeInTheDocument();
  expect(await screen.findByTestId("sample-context")).toHaveTextContent("Standalone Sample");
  expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Analyze" })).toBeInTheDocument();
});

test("Detections tab is user-friendly: time, pipeline, source, and spectrum view", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("tab", { name: "Detections" }));
  const item = await screen.findByTestId("run-history-item");
  expect(item).toHaveTextContent("stft_energy_detector");
  expect(item).toHaveTextContent("Local analysis");
  expect(item).toHaveTextContent(/2026|—/);
  // No machine hash exposed to the user.
  expect(item.textContent).not.toContain("run_1");
  fireEvent.click(within(item).getByRole("button", { name: "View Results" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/spectrum/rec_1?run=run_1");
});

test("Analyze opens the real spectrum workspace", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Analyze" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/spectrum/rec_1");
});

test("standalone sample Back returns to the Standalone Samples tab", async () => {
  renderPage();
  await screen.findByText("sample-a");
  fireEvent.click(screen.getByTestId("sample-back"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library?tab=standalone");
});

test("dataset member sample Back returns to the Datasets tab", async () => {
  currentRecording = recordingWire({ dataset_name: "SpaceNet", dataset_split: "test", dataset_id: "ds_1", sample_key: "a" });
  renderPage();
  await screen.findByText("sample-a");
  fireEvent.click(screen.getByTestId("sample-back"));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent("/data-library?tab=datasets");
});
