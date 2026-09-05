import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SpectrumAnalysisPage } from "./SpectrumAnalysisPage";

const recording = {
  id: "rec_1",
  name: "Burst Demo",
  data_format: "complex64_le",
  sample_rate_hz: 1000000,
  center_frequency_hz: 2441000000,
  frequency_low_hz: 2440500000,
  frequency_high_hz: 2441500000,
  num_samples: 200000,
  duration_s: 0.2,
  dataset_name: null,
  dataset_split: null,
  label_space: "spacenet_14",
  has_ground_truth: false,
};

const spectrogram = {
  representation: "stft",
  image_url: "/media/spectrograms/key.png",
  t_start_s: 0.0,
  t_end_s: 0.2,
  f_low_hz: 2440500000,
  f_high_hz: 2441500000,
};

const pipelines = [
  {
    id: "dummy",
    name: "Dummy Pipeline",
    version: "1.0",
    label_space: "spacenet_14",
    recommended_device: "CPU",
    cpu_supported: true,
    stages: [],
    inspectable_stages: [],
    task_capability: "classification",
  },
  {
    id: "stft_energy_detector",
    name: "STFT Energy Detector",
    version: "1.0",
    label_space: "signal_presence_v1",
    recommended_device: "CPU",
    cpu_supported: true,
    stages: [],
    inspectable_stages: [],
    task_capability: "detection_localization",
  },
];

function setup(postedPipelineIds: string[]) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/api/pipelines")) return new Response(JSON.stringify(pipelines));
    if (url.endsWith("/api/recordings/rec_1")) return new Response(JSON.stringify(recording));
    if (url.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (url.endsWith("/api/analysis-runs") && options?.method === "POST") {
      const body = JSON.parse(String(options.body)) as { pipeline_id: string };
      postedPipelineIds.push(body.pipeline_id);
      return new Response(JSON.stringify({
        id: "run_1",
        recording_id: "rec_1",
        pipeline_id: body.pipeline_id,
        pipeline_version: "1.0",
        executor: "local_cpu",
        status: "running",
        parameters_json: {},
        hardware_info_json: null,
        started_at: null,
        finished_at: null,
        error_type: null,
        error_message: null,
        worker_pid: 1,
        created_at: "2026-09-05T00:00:00",
      }), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={["/spectrum/rec_1"]}>
      <Routes>
        <Route path="/spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("exposes STFT Energy Detector with detection-only copy and submits its id", async () => {
  const postedPipelineIds: string[] = [];
  setup(postedPipelineIds);

  await screen.findByText("Burst Demo");
  expect(screen.getByText("Dummy Pipeline · CPU")).toBeInTheDocument();

  fireEvent.mouseDown(screen.getByText("Dummy Pipeline · CPU"));
  const detectorOption = await screen.findByTitle("STFT Energy Detector · CPU · Detection & localization only");
  fireEvent.click(detectorOption);

  await waitFor(() => expect(screen.getAllByText("STFT Energy Detector · CPU · Detection & localization only").length).toBeGreaterThan(0));

  fireEvent.click(screen.getByRole("button", { name: "Run Analysis" }));
  await waitFor(() => expect(postedPipelineIds).toContain("stft_energy_detector"));
});