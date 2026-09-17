import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ImportStandaloneIqModal } from "./ImportStandaloneIqModal";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

const createdRecording = {
  id: "rec_new",
  name: "capture_001",
  data_format: "float16_interleaved_le",
  source: "custom",
  external_path: "D:\\signals\\capture_001.iq",
  sample_rate_hz: 2e6,
  center_frequency_hz: 2.4e9,
  frequency_low_hz: 2.399e9,
  frequency_high_hz: 2.401e9,
  num_samples: 100,
  duration_s: 0.00005,
  dataset_name: null,
  dataset_split: null,
  label_space: null,
  has_ground_truth: false,
  dataset_id: null,
  sample_key: null,
};

let posted: Record<string, unknown> | null = null;

beforeEach(() => {
  posted = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).endsWith("/api/recordings/register-path") && init?.method === "POST") {
      posted = JSON.parse(String(init.body)) as Record<string, unknown>;
      return new Response(JSON.stringify(createdRecording), { status: 201 });
    }
    return new Response(JSON.stringify([]), { status: 200 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

test("register local path preserves the entered fields in the request", async () => {
  const onImported = vi.fn();
  render(
    renderWithLocalization(
      <ImportStandaloneIqModal open onClose={() => {}} onImported={onImported} />,
    ),
  );

  fireEvent.click(screen.getByRole("tab", { name: "Register Local Path" }));
  fireEvent.change(screen.getByLabelText("Local path"), {
    target: { value: "D:\\signals\\capture_001.iq" },
  });
  fireEvent.change(screen.getByLabelText("Recording name"), { target: { value: "capture_001" } });
  fireEvent.change(screen.getByLabelText("Sample rate (Hz)"), { target: { value: "2000000" } });
  fireEvent.change(screen.getByLabelText("Center frequency (Hz)"), {
    target: { value: "2400000000" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Register" }));

  await waitFor(() => expect(posted).not.toBeNull());
  expect(posted).toMatchObject({
    path: "D:\\signals\\capture_001.iq",
    name: "capture_001",
    data_format: "complex64_le",
    sample_rate_hz: 2000000,
    center_frequency_hz: 2400000000,
  });
  await waitFor(() => expect(onImported).toHaveBeenCalledWith("rec_new"));
});
