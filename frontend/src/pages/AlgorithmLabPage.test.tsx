import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

const completedRuns = [
  {
    id: "run_a",
    recording_id: "rec1",
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
  },
  {
    id: "run_b",
    recording_id: "rec1",
    pipeline_id: "zoomspec",
    pipeline_version: "1.0",
    executor: "imported",
    status: "completed",
    parameters_json: {},
    hardware_info_json: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    worker_pid: null,
    created_at: "2026-09-05T00:00:01",
  },
];

const compareResponse = {
  recording_id: "rec1",
  iou_threshold: 0.5,
  run_a: {
    run_id: "run_a",
    pipeline_id: "stft_energy_detector",
    pipeline_name: "STFT Energy Detector",
    metrics: { tp: 6, fp: 166, fn: 0, precision: 0.0349, recall: 1.0, f1: 0.0674, mean_matched_iou: 0.62 },
    classification_applicable: false,
    classification_reason: "detection_only_pipeline",
    classification: null,
    class_aware: null,
  },
  run_b: {
    run_id: "run_b",
    pipeline_id: "zoomspec",
    pipeline_name: "ZoomSpec",
    metrics: { tp: 5, fp: 2, fn: 1, precision: 0.7143, recall: 0.8333, f1: 0.7692, mean_matched_iou: null },
    classification_applicable: true,
    classification_reason: null,
    classification: {
      matched_count: 5,
      class_correct: 5,
      class_wrong: 0,
      matched_accuracy: 1.0,
      confusions: [],
    },
    class_aware: { tp: 5, fp: 2, fn: 1, precision: 0.7143, recall: 0.8333, f1: 0.7692 },
  },
  cases: [
    {
      ground_truth_id: "gt0",
      class_id: 9,
      class_name: "LoRa 250kHz",
      bbox: { t_start_s: 0.032, t_end_s: 0.08, f_low_hz: 2417973850, f_high_hz: 2418026150 },
      comparison: "both_detected",
      run_a: { matched: true, detection_id: "det1", iou: 0.68, class_id: 0, class_name: "Signal", confidence: 0.91, class_correct: null, bbox: { t_start_s: 0.031, t_end_s: 0.081, f_low_hz: 2417960000, f_high_hz: 2418040000 } },
      run_b: { matched: true, detection_id: "det2", iou: 0.71, class_id: 9, class_name: "LoRa 250kHz", confidence: 0.8, class_correct: true, bbox: { t_start_s: 0.032, t_end_s: 0.08, f_low_hz: 2417973850, f_high_hz: 2418026150 } },
    },
    {
      ground_truth_id: "gt1",
      class_id: 2,
      class_name: "WiFi 20MHz 16QAM",
      bbox: { t_start_s: 0.08, t_end_s: 0.1, f_low_hz: 2437000000, f_high_hz: 2477000000 },
      comparison: "a_only",
      run_a: { matched: true, detection_id: "det3", iou: 0.55, class_id: 0, class_name: "Signal", confidence: 0.7, class_correct: null, bbox: { t_start_s: 0.081, t_end_s: 0.1, f_low_hz: 2437100000, f_high_hz: 2476900000 } },
      run_b: { matched: false, detection_id: null, iou: null, class_id: null, class_name: null, confidence: null, class_correct: null, bbox: null },
    },
    {
      ground_truth_id: "gt2",
      class_id: 6,
      class_name: "BLE LE1M",
      bbox: { t_start_s: 0.1, t_end_s: 0.12, f_low_hz: 2446000000, f_high_hz: 2448000000 },
      comparison: "b_only",
      run_a: { matched: false, detection_id: null, iou: null, class_id: null, class_name: null, confidence: null, class_correct: null, bbox: null },
      run_b: { matched: true, detection_id: "det4", iou: 0.6, class_id: 6, class_name: "BLE LE1M", confidence: 0.85, class_correct: true, bbox: { t_start_s: 0.1, t_end_s: 0.12, f_low_hz: 2446100000, f_high_hz: 2447900000 } },
    },
    {
      ground_truth_id: "gt3",
      class_id: 13,
      class_name: "FM",
      bbox: { t_start_s: 0.12, t_end_s: 0.14, f_low_hz: 2456000000, f_high_hz: 2459000000 },
      comparison: "both_missed",
      run_a: { matched: false, detection_id: null, iou: null, class_id: null, class_name: null, confidence: null, class_correct: null, bbox: null },
      run_b: { matched: false, detection_id: null, iou: null, class_id: null, class_name: null, confidence: null, class_correct: null, bbox: null },
    },
  ],
};

const spectrogram = {
  representation: "stft",
  image_url: "/media/spectrograms/key.png",
  t_start_s: 0.0,
  t_end_s: 0.15,
  f_low_hz: 2430000000,
  f_high_hz: 2480000000,
};

const groundTruth = [{ id: "gt0", recording_id: "rec1", t_start_s: 0.032, t_end_s: 0.08, f_low_hz: 2417973850, f_high_hz: 2418026150, class_id: 9, class_name: "LoRa 250kHz" }];
const detectionsA = [{ id: "det1", run_id: "run_a", recording_id: "rec1", t_start_s: 0.031, t_end_s: 0.081, f_low_hz: 2417960000, f_high_hz: 2418040000, class_id: 0, class_name: "Signal", confidence: 0.91, scores_json: null }];
const detectionsB = [{ id: "det2", run_id: "run_b", recording_id: "rec1", t_start_s: 0.032, t_end_s: 0.08, f_low_hz: 2417973850, f_high_hz: 2418026150, class_id: 9, class_name: "LoRa 250kHz", confidence: 0.8, scores_json: null }];

function setup(overrides: { runs?: unknown[]; compareError?: boolean } = {}) {
  const runs = overrides.runs ?? completedRuns;
  const requests: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    const urlStr = String(url);
    requests.push({ url: urlStr, body: options?.body as string | undefined });
    if (urlStr.includes("/api/recordings?limit=")) {
      return new Response(JSON.stringify({ items: [recording], total: 1 }));
    }
    if (urlStr.includes("/api/analysis-runs?recording_id=")) {
      return new Response(JSON.stringify(runs));
    }
    if (urlStr.endsWith("/api/algorithm-lab/compare") && options?.method === "POST") {
      if (overrides.compareError) {
        return new Response(JSON.stringify({ error: { code: "INVALID_COMPARISON", message: "Run must be completed.", details: {} } }), { status: 422 });
      }
      return new Response(JSON.stringify(compareResponse), { status: 200 });
    }
    if (urlStr.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (urlStr.endsWith("/ground-truth")) return new Response(JSON.stringify(groundTruth));
    if (urlStr.endsWith("/analysis-runs/run_a/detections")) return new Response(JSON.stringify(detectionsA));
    if (urlStr.endsWith("/analysis-runs/run_b/detections")) return new Response(JSON.stringify(detectionsB));
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
  render(
    <MemoryRouter>
      <AlgorithmLabPage />
    </MemoryRouter>,
  );
  return requests;
}

async function chooseRecording() {
  fireEvent.mouseDown(await screen.findByLabelText("Recording"));
  fireEvent.click(await screen.findByTitle("Sample 0"));
}

async function chooseRun(label: string, value: string) {
  fireEvent.mouseDown(screen.getByLabelText(label));
  const matches = await screen.findAllByTitle(value);
  fireEvent.click(matches[matches.length - 1]);
}

test("selects a recording, exposes only completed runs, and compares", async () => {
  const requests = setup();
  await chooseRecording();

  await chooseRun("Run A", "stft_energy_detector · run_a");
  await chooseRun("Run B", "zoomspec · run_b");

  const compareButton = screen.getByRole("button", { name: "Compare" });
  await waitFor(() => expect(compareButton).toBeEnabled());
  fireEvent.click(compareButton);

  expect(await screen.findByText("both_detected")).toBeInTheDocument();
  expect(screen.getAllByText(/STFT Energy Detector/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/ZoomSpec/).length).toBeGreaterThan(0);
  // Localization
  expect(screen.getAllByText("Precision").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Recall").length).toBeGreaterThan(0);
  expect(screen.getAllByText("F1").length).toBeGreaterThan(0);
  // Classification / detection-only N/A (Run A)
  expect(screen.getAllByText(/Not applicable/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/detection_only_pipeline/).length).toBeGreaterThan(0);
  // Classification-on-matched (Run B)
  expect(screen.getByText("Matched Accuracy")).toBeInTheDocument();
  // End-to-End (Run B)
  expect(screen.getByText("Class-aware F1")).toBeInTheDocument();
  expect(screen.getByText("a_only")).toBeInTheDocument();
  expect(screen.getByText("b_only")).toBeInTheDocument();
  expect(screen.getByText("both_missed")).toBeInTheDocument();

  const compareCall = requests.find((item) => item.url.endsWith("/api/algorithm-lab/compare"));
  expect(JSON.parse(compareCall!.body!)).toEqual({
    recording_id: "rec1",
    run_a_id: "run_a",
    run_b_id: "run_b",
    iou_threshold: 0.5,
  });
});

test("shows empty state when the recording has fewer than two completed runs", async () => {
  setup({ runs: [completedRuns[0]] });
  await chooseRecording();
  expect(await screen.findByText(/fewer than two completed/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
});

test("renders a non-destructive error when compare fails", async () => {
  setup({ compareError: true });
  await chooseRecording();
  await chooseRun("Run A", "stft_energy_detector · run_a");
  await chooseRun("Run B", "zoomspec · run_b");
  const compareButton = screen.getByRole("button", { name: "Compare" });
  await waitFor(() => expect(compareButton).toBeEnabled());
  fireEvent.click(compareButton);
  expect(await screen.findByText("Run must be completed.")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: /Compare/ })).toBeEnabled());
  expect(screen.getByLabelText("Recording")).toBeInTheDocument();
});