import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { AlgorithmLabPage } from "./AlgorithmLabPage";

afterEach(() => vi.unstubAllGlobals());

const recording = {
  id: "rec1",
  name: "Sample 0",
  data_format: "float16_interleaved_le",
  source: "spacenet",
  external_path: "D:\\SpaceNet\\test\\0.bin",
  sample_rate_hz: 50000000,
  center_frequency_hz: 2455000000,
  frequency_low_hz: 2430000000,
  frequency_high_hz: 2480000000,
  num_samples: 7500000,
  duration_s: 0.15,
  dataset_name: "SpaceNet",
  dataset_split: "test",
  label_space: "spacenet_14",
  has_ground_truth: true,
};

const run2500 = {
  id: "run_2500",
  recording_id: "rec2500",
  pipeline_id: "stft_energy_detector",
  pipeline_version: "1.0",
  executor: "local_cpu",
  status: "completed",
  parameters_json: {},
  hardware_info_json: null,
  started_at: null,
  finished_at: null,
  error_type: null,
  error_message: null,
  worker_pid: null,
  created_at: "2026-09-05T00:00:00",
};

function stubFetch(options: { benchmarkList?: unknown[] } = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const urlStr = String(url);
    if (urlStr.includes("/api/recordings?limit=")) {
      return new Response(JSON.stringify({ items: [recording], total: 1 }));
    }
    const directMatch = urlStr.match(/\/api\/recordings\/([^/?]+)$/);
    if (directMatch) {
      const item = directMatch[1] === "rec2500" ? { ...recording, id: "rec2500", name: "Sample 2499" } : undefined;
      if (!item) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(item));
    }
    if (urlStr.includes("/api/analysis-runs?recording_id=")) {
      const id = new URL(urlStr).searchParams.get("recording_id") ?? "";
      return new Response(JSON.stringify(id === "rec2500" ? [run2500] : []));
    }
    if (urlStr.endsWith("/api/dataset-benchmarks") && !urlStr.includes("imported")) {
      return new Response(JSON.stringify(options.benchmarkList ?? []));
    }
    if (urlStr.includes("/spectrogram")) return new Response(JSON.stringify({ representation: "stft", image_url: "/x.png", t_start_s: 0, t_end_s: 1, f_low_hz: 1, f_high_hz: 2 }));
    if (urlStr.endsWith("/ground-truth")) return new Response("[]");
    if (urlStr.endsWith("/analysis-runs/run_2500/detections")) return new Response("[]");
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function renderPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/algorithm-lab" element={<AlgorithmLabPage />} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  );
}

test("defaults to the Case Analysis tab", async () => {
  stubFetch();
  renderPage("/algorithm-lab");
  expect(await screen.findByLabelText("Recording")).toBeInTheDocument();
  expect(screen.getByText("Case Analysis")).toBeInTheDocument();
  expect(screen.getByText("Dataset Benchmarks")).toBeInTheDocument();
});

test("renders the Dataset Benchmarks tab from the query parameter", async () => {
  stubFetch();
  renderPage("/algorithm-lab?tab=benchmarks");
  expect(await screen.findByRole("button", { name: "New Benchmark" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Recording")).not.toBeInTheDocument();
});

test("hydrates a case query recording outside the first 500 list through the page", async () => {
  stubFetch();
  renderPage("/algorithm-lab?tab=case&recording=rec2500&runA=run_2500");
  expect(await screen.findByText("Sample 2499")).toBeInTheDocument();
  expect(await screen.findByText(/Select Run B to compare/)).toBeInTheDocument();
});

test("switching tabs keeps unrelated query state", async () => {
  stubFetch();
  renderPage("/algorithm-lab?tab=case&recording=rec1");
  fireEvent.click(screen.getByText("Dataset Benchmarks"));
  expect(await screen.findByRole("button", { name: "New Benchmark" })).toBeInTheDocument();
  expect(screen.getByTestId("location").textContent).toContain("tab=benchmarks");
  expect(screen.getByTestId("location").textContent).toContain("recording=rec1");
});