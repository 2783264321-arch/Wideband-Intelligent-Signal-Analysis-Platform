import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ExperimentDetail } from "../dataset-experiment/ExperimentDetail";
import { ExportAnalysisBundleButton } from "./ExportAnalysisBundleButton";
import { ImportAnalysisBundleModal } from "./ImportAnalysisBundleModal";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import { ANALYSIS_BUNDLE_DATASET_MISMATCH } from "../../api/analysisBundles";

const SUMMARY = {
  schema_version: 1,
  bundle_id: "bundle_abc",
  import_fingerprint: "a".repeat(64),
  archive_sha256: "b".repeat(64),
  dataset_id: "ds_1",
  dataset_name: "SpaceNet",
  dataset_split: "test",
  pipeline_id: "ghost_pipeline",
  pipeline_version: "9.9",
  label_space: "spacenet_14",
  sample_count: 2,
  detection_count: 3,
  already_imported: false,
  created_runs: 2,
  existing_runs: 0,
  created_detections: 3,
  sample_run_mapping: [
    { sample_key: "0000", sample_name: "name_0", recording_id: "rec_1", analysis_run_id: "run_1" },
    { sample_key: "0001", sample_name: "name_1", recording_id: "rec_2", analysis_run_id: "run_2" },
  ],
};

function experimentWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "exp_1",
    name: "Exp A",
    dataset_name: "SpaceNet",
    dataset_split: "test",
    dataset_label_space: "spacenet_14",
    recording_manifest_hash: "a".repeat(64),
    plugin_id: "ghost_pipeline",
    plugin_version: "9.9",
    model_release_id: null,
    asset_manifest_sha256: null,
    parameters_json: {},
    executor: "local_cpu",
    evaluation_protocol: "physical_tf_detection_ap_v2",
    max_concurrency: 1,
    status: "completed",
    dataset_evaluation_id: null,
    error_type: null,
    error_message: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    expected_items: 2,
    queued_items: 0,
    running_items: 0,
    completed_items: 2,
    failed_items: 0,
    attempt_count: 2,
    ...overrides,
  };
}

function installDownloadStubs() {
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => "blob:mock");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
}

function zipResponse(): Response {
  return new Response(new Blob(["zip-bytes"]), {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": 'attachment; filename="results.zip"',
    },
  });
}

beforeEach(() => {
  window.localStorage.clear();
  installDownloadStubs();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderModal(onImported?: () => void) {
  return render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library"]}>
        <Routes>
          <Route
            path="/data-library"
            element={<ImportAnalysisBundleModal open onClose={() => undefined} onImported={onImported} />}
          />
          <Route path="/spectrum/:recordingId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

function chooseFile() {
  fireEvent.change(screen.getByLabelText("Select Analysis Bundle"), {
    target: { files: [new File(["zip"], "bundle.zip", { type: "application/zip" })] },
  });
}

// ---------- export action ----------

test("completed analysis exposes Export Results and downloads the bundle", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    expect(String(url)).toContain("/api/dataset-experiments/exp_1/export");
    return zipResponse();
  });
  vi.stubGlobal("fetch", fetchMock);

  render(
    renderWithLocalization(<ExportAnalysisBundleButton experimentId="exp_1" status="completed" />),
  );

  const button = screen.getByTestId("export-analysis-bundle-button");
  expect(button).toBeInTheDocument();
  expect(screen.queryByTestId("analysis-bundle-partial-note")).toBeNull();

  fireEvent.click(button);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
});

test("incomplete or failed analysis does not expose Export Results", () => {
  const { rerender } = render(
    renderWithLocalization(<ExportAnalysisBundleButton experimentId="exp_1" status="running" />),
  );
  expect(screen.queryByTestId("export-analysis-bundle-button")).toBeNull();

  rerender(
    renderWithLocalization(<ExportAnalysisBundleButton experimentId="exp_1" status="failed" />),
  );
  expect(screen.queryByTestId("export-analysis-bundle-button")).toBeNull();
});

test("completed_with_failures is exportable but flagged as a partial result set", () => {
  render(
    renderWithLocalization(
      <ExportAnalysisBundleButton experimentId="exp_1" status="completed_with_failures" />,
    ),
  );
  expect(screen.getByTestId("export-analysis-bundle-button")).toBeInTheDocument();
  expect(screen.getByTestId("analysis-bundle-partial-note")).toHaveTextContent(
    "This analysis completed with failures",
  );
});

test("ExperimentDetail places Export Results on a completed Dataset Analysis", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const target = String(url);
      if (target.includes("/items") || target.includes("/attempts")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (target.includes("/export")) return zipResponse();
      return new Response(JSON.stringify(experimentWire()), { status: 200 });
    }),
  );

  render(
    renderWithLocalization(
      <MemoryRouter>
        <ExperimentDetail experimentId="exp_1" />
      </MemoryRouter>,
    ),
  );

  expect(await screen.findByTestId("export-analysis-bundle-button")).toBeInTheDocument();
});

// ---------- import modal ----------

test("import modal accepts a bundle file and disables submit until chosen", () => {
  vi.stubGlobal("fetch", vi.fn());
  renderModal();

  const submit = screen.getByRole("button", { name: "Import" });
  expect(submit).toBeDisabled();
  expect(
    screen.getByText("Analysis bundles contain computed results, not raw IQ data."),
  ).toBeInTheDocument();

  chooseFile();
  expect(screen.getByRole("button", { name: "Import" })).not.toBeDisabled();
});

test("successful import renders a useful summary", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 201 })));
  renderModal();

  chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));

  const summary = await screen.findByTestId("analysis-bundle-summary");
  expect(summary).toHaveTextContent("SpaceNet / test");
  expect(screen.getByTestId("analysis-bundle-sample-count")).toHaveTextContent("2");
  expect(screen.getByTestId("analysis-bundle-run-count")).toHaveTextContent("2");
  expect(screen.getByTestId("analysis-bundle-detection-count")).toHaveTextContent("3");
  expect(screen.getByTestId("analysis-bundle-success")).toBeInTheDocument();
});

test("already-imported bundles render success/info styling, not an error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ ...SUMMARY, already_imported: true, created_runs: 0, existing_runs: 2 }),
        { status: 201 },
      ),
    ),
  );
  renderModal();

  chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));

  const idempotent = await screen.findByTestId("analysis-bundle-idempotent");
  expect(idempotent).toHaveTextContent(
    "These analysis results were already imported. No duplicate results were created.",
  );
  expect(screen.queryByTestId("analysis-bundle-error")).toBeNull();
  expect(screen.getByTestId("analysis-bundle-run-count")).toHaveTextContent("0");
});

test("dataset mismatch renders a useful, non-technical message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({
          error: {
            code: ANALYSIS_BUNDLE_DATASET_MISMATCH,
            message: "No local dataset matches this bundle's portable dataset identity.",
            details: { dataset_name: "SpaceNet" },
          },
        }),
        { status: 409 },
      ),
    ),
  );
  renderModal();

  chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));

  expect(
    await screen.findByText("No matching local Dataset was found for this analysis bundle."),
  ).toBeInTheDocument();
});

test("View Imported Results navigates to the spectrum view with recording and run ids", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 201 })));
  renderModal();

  chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  fireEvent.click(await screen.findByTestId("analysis-bundle-view-results"));

  expect(await screen.findByTestId("location-probe")).toHaveTextContent(
    "/spectrum/rec_1?run=run_1",
  );
});

test("import never queries pipelines or executor selection", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(SUMMARY), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);
  renderModal();

  chooseFile();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await screen.findByTestId("analysis-bundle-summary");

  const calls = (fetchMock.mock.calls as unknown[][]).map((call) => String(call[0]));
  expect(calls.some((url) => url.includes("/api/pipelines"))).toBe(false);
  expect(calls.some((url) => url.includes("executor-selection"))).toBe(false);
  expect(calls.every((url) => url.includes("/api/analysis-bundles/import"))).toBe(true);
});

test("import modal is localized in zh-CN", () => {
  vi.stubGlobal("fetch", vi.fn());
  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/data-library"]}>
        <Routes>
          <Route
            path="/data-library"
            element={<ImportAnalysisBundleModal open onClose={() => undefined} />}
          />
        </Routes>
      </MemoryRouter>,
      { locale: "zh-CN" },
    ),
  );

  expect(screen.getByText("导入分析结果")).toBeInTheDocument();
  expect(screen.getByText("分析包仅包含计算结果，不包含原始 IQ 数据。")).toBeInTheDocument();
});
