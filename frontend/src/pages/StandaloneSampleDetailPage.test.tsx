import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { StandaloneSampleDetailPage } from "./StandaloneSampleDetailPage";
import { renderWithLocalization } from "../test-utils/renderWithLocalization";

const recordingWire = {
  id: "rec_1",
  name: "sample-a",
  data_format: "complex64_le",
  source: "custom",
  external_path: null,
  sample_rate_hz: 1e6,
  center_frequency_hz: 0,
  frequency_low_hz: -5e5,
  frequency_high_hz: 5e5,
  num_samples: 1000,
  duration_s: 0.001,
  dataset_name: null,
  dataset_split: null,
  label_space: null,
  has_ground_truth: false,
};

function runWire(id: string, status: string) {
  return {
    id,
    recording_id: "rec_1",
    pipeline_id: "p",
    pipeline_version: "1.0",
    executor: "local_cpu",
    status,
    parameters_json: {},
    created_at: "2026-01-01T00:00:00Z",
  };
}

const runs = [runWire("run_p", "pending"), runWire("run_r", "running"), runWire("run_c", "completed")];

function route(url: string, init?: RequestInit): Response {
  if ((init?.method ?? "GET") === "DELETE") {
    return new Response(JSON.stringify({ error: { code: "DELETE", message: "x", details: {} } }), {
      status: 409,
    });
  }
  if (url.includes("/api/analysis-runs")) {
    return new Response(JSON.stringify(runs), { status: 200 });
  }
  if (url.includes("/api/recordings/rec_1")) {
    return new Response(JSON.stringify(recordingWire), { status: 200 });
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
      <MemoryRouter initialEntries={["/data-library/samples/rec_1"]}>
        <Routes>
          <Route path="/data-library/samples/:recordingId" element={<StandaloneSampleDetailPage />} />
          <Route path="/algorithm-lab" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => route(String(url), init)),
  );
});
afterEach(() => vi.unstubAllGlobals());

test("analysis history shows all run statuses, not completed-only", async () => {
  renderPage();
  const items = await screen.findAllByTestId("run-history-item");
  expect(items).toHaveLength(3);
  expect(screen.getByText("pending")).toBeInTheDocument();
  expect(screen.getByText("running")).toBeInTheDocument();
  expect(screen.getByText("completed")).toBeInTheDocument();
});

test("the history request omits the status filter", async () => {
  renderPage();
  await screen.findAllByTestId("run-history-item");
  const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
  const historyCall = calls.find((call) => String(call[0]).includes("/api/analysis-runs?"));
  expect(String(historyCall?.[0])).toContain("recording_id=rec_1");
  expect(String(historyCall?.[0])).not.toContain("status=");
});

test("delete confirmation is explicit", async () => {
  renderPage();
  await screen.findAllByTestId("run-history-item");
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/permanently deletes/)).toBeInTheDocument();
});

test("frequency range is labelled distinctly from source", async () => {
  renderPage();
  await screen.findAllByTestId("run-history-item");
  expect(screen.getByText("Frequency Range")).toBeInTheDocument();
  expect(screen.getAllByText("Source")).toHaveLength(1);
});

import userEvent from "@testing-library/user-event";

test("compare shortcut navigates with recording/runA/runB business state", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if ((init?.method ?? "GET") === "DELETE") return new Response(null, { status: 204 });
    if (url.includes("/api/analysis-runs")) {
      return new Response(
        JSON.stringify([runWire("run_a", "completed"), runWire("run_b", "completed")]),
        { status: 200 },
      );
    }
    if (url.includes("/api/recordings/rec_1")) {
      return new Response(JSON.stringify({ ...recordingWire, has_ground_truth: true }), {
        status: 200,
      });
    }
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  }));

  renderPage();
  await screen.findAllByTestId("run-history-item");
  const user = userEvent.setup();
  await user.click(screen.getByRole("checkbox", { name: "run_a" }));
  await user.click(screen.getByRole("checkbox", { name: "run_b" }));
  await user.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/algorithm-lab?recording=rec_1&runA=run_a&runB=run_b",
  );
});
