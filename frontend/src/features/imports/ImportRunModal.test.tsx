import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { RecordingsPage } from "../../pages/RecordingsPage";

const recording = {
  id: "rec_local",
  name: "Local IQ",
  data_format: "complex64_le",
  sample_rate_hz: 1000000,
  center_frequency_hz: 2441000000,
  frequency_low_hz: 2440500000,
  frequency_high_hz: 2441500000,
  num_samples: 4096,
  duration_s: 0.004096,
  dataset_name: null,
  dataset_split: null,
  label_space: "spacenet_14",
  has_ground_truth: false,
};

function Destination() {
  const location = useLocation();
  return <div>Results destination: {location.pathname}{location.search}</div>;
}

function setup(reject = false) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/api/recordings")) return new Response(JSON.stringify([recording]));
    if (url.endsWith("/api/imported-runs") && options?.method === "POST") {
      const form = options.body as FormData;
      if (form.get("recording_id") !== "rec_local" || !(form.get("file") instanceof File)) {
        return new Response("{}", { status: 400 });
      }
      if (reject) {
        return new Response(JSON.stringify({ error: {
          code: "INVALID_IMPORT_PACKAGE",
          message: "Package label_space does not match the selected Recording.",
          details: {},
        } }), { status: 400 });
      }
      return new Response(JSON.stringify({
        id: "run_imported",
        recording_id: "rec_local",
        pipeline_id: "zoomspec",
        pipeline_version: "1.0",
        executor: "imported",
        status: "completed",
        parameters_json: {},
        hardware_info_json: { executor: "remote_gpu", device: "RTX 4090", environment: "AutoDL" },
        started_at: null,
        finished_at: null,
        error_type: null,
        error_message: null,
        worker_pid: null,
        created_at: "2026-09-05T00:00:00",
      }), { status: 201 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<RecordingsPage />} />
        <Route path="/spectrum/:recordingId" element={<Destination />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openModal() {
  await screen.findByText("Local IQ");
  fireEvent.click(screen.getByRole("button", { name: "Import Existing Run" }));
  await screen.findByLabelText("Analysis Package ZIP");
}

async function chooseZipAndRecording() {
  fireEvent.change(screen.getByLabelText("Analysis Package ZIP"), {
    target: { files: [new File(["zip fixture"], "analysis.zip", { type: "application/zip" })] },
  });
  fireEvent.mouseDown(screen.getByLabelText("Local Recording"));
  fireEvent.click(await screen.findByTitle("Local IQ"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import" })).toBeEnabled());
}

test("imports a ZIP for the selected Recording and opens the shared Spectrum run route", async () => {
  setup();
  await openModal();
  await chooseZipAndRecording();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open Results" }));
  expect(await screen.findByText("Results destination: /spectrum/rec_local?run=run_imported")).toBeInTheDocument();
});

test("keeps validation errors in the import dialog and allows retry", async () => {
  setup(true);
  await openModal();
  await chooseZipAndRecording();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  expect(await screen.findByText("Package label_space does not match the selected Recording.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Open Results" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Import" })).toBeEnabled());
});

test("requires a ZIP and a Recording before enabling Import", async () => {
  setup();
  await openModal();
  expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "analysis.zip")] },
  });
  expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  fireEvent.mouseDown(screen.getByLabelText("Local Recording"));
  fireEvent.click(await screen.findByTitle("Local IQ"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import" })).toBeEnabled());
});