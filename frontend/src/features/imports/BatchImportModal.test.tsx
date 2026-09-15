import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BatchImportModal } from "./BatchImportModal";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";
import { PlatformApiError, importBatchRun } from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, importBatchRun: vi.fn() };
});

const mockedImportBatchRun = vi.mocked(importBatchRun);

const successSummary = {
  batchId: "id_batch_1",
  importFingerprint: "a".repeat(64),
  archiveSha256: "b".repeat(64),
  datasetName: "SpaceNet",
  datasetSplit: "test",
  pipelineId: "zoomspec_yolo26n_aug_combined_frn_v3",
  pipelineVersion: "1.0.0",
  labelSpace: "spacenet_14",
  itemCount: 4,
  detectionCount: 12,
  alreadyImported: false,
  createdRuns: 2,
  existingRuns: 0,
  createdDetections: 9,
  matchedRecordings: 2,
  missingRecordings: 0,
  ambiguousRecordings: 0,
  fingerprintMismatches: 0,
  recordingRunMapping: [
    { recordingId: "rec_1", recordingName: "0", analysisRunId: "run_1" },
    { recordingId: "rec_2", recordingName: "1", analysisRunId: "run_2" },
  ],
};

beforeEach(() => {
  mockedImportBatchRun.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

function renderModal(onClose: () => void = () => undefined, locale: "zh-CN" | "en-US" = "en-US") {
  return render(
    renderWithLocalization(<BatchImportModal open onClose={onClose} />, { locale }),
  );
}

test("disables submit until a file is chosen", () => {
  renderModal();

  const submit = screen.getByRole("button", { name: "Import Batch" });
  expect(submit).toBeDisabled();

  fireEvent.change(screen.getByLabelText("Batch Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });

  expect(screen.getByRole("button", { name: "Import Batch" })).not.toBeDisabled();
});

test("shows new-import summary fields and mapping on success", async () => {
  mockedImportBatchRun.mockResolvedValue(successSummary);
  renderModal();

  fireEvent.change(screen.getByLabelText("Batch Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import Batch" }));

  const summary = await screen.findByTestId("batch-import-summary");
  expect(summary).toHaveTextContent("id_batch_1");
  expect(summary).toHaveTextContent("SpaceNet / test");
  expect(summary).toHaveTextContent("zoomspec_yolo26n_aug_combined_frn_v3");
  expect(summary).toHaveTextContent("1.0.0");
  expect(summary).toHaveTextContent("12");
  expect(summary).toHaveTextContent("Import Summary");
  // mapping rows
  expect(screen.getByTestId("batch-import-mapping")).toHaveTextContent("run_1");
  expect(screen.getByTestId("batch-import-mapping")).toHaveTextContent("run_2");
  expect(screen.getByTestId("batch-import-mapping")).toHaveTextContent("0");
  expect(screen.getByTestId("batch-import-mapping")).toHaveTextContent("1");
});

test("shows idempotent state on re-import", async () => {
  mockedImportBatchRun.mockResolvedValue({
    ...successSummary,
    alreadyImported: true,
    createdRuns: 0,
    existingRuns: 2,
  });
  renderModal();

  fireEvent.change(screen.getByLabelText("Batch Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import Batch" }));

  const summary = await screen.findByTestId("batch-import-summary");
  expect(summary).toHaveTextContent("Already imported; no new runs were created.");
  expect(summary).toHaveTextContent("Created runs = 0");
});

test("renders a structured validation failure via toErrorText", async () => {
  mockedImportBatchRun.mockRejectedValue(new PlatformApiError({
    status: 409,
    code: "BATCH_IMPORT_STATE_INCONSISTENT",
    message: "Partial or conflicting prior semantic import state exists.",
    details: {},
  }));
  renderModal();

  fireEvent.change(screen.getByLabelText("Batch Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import Batch" }));

  expect(await screen.findByText(/BATCH_IMPORT_STATE_INCONSISTENT/)).toBeInTheDocument();
});

test("resets state when closed and reopened", async () => {
  mockedImportBatchRun.mockResolvedValue(successSummary);
  const onClose = vi.fn();
  const { rerender } = render(renderWithLocalization(<BatchImportModal open onClose={onClose} />));

  fireEvent.change(screen.getByLabelText("Batch Analysis Package ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import Batch" }));
  await screen.findByTestId("batch-import-summary");

  fireEvent.click(screen.getByText("Close"));
  expect(onClose).toHaveBeenCalledTimes(1);

  rerender(renderWithLocalization(<BatchImportModal open onClose={onClose} />));
  expect(screen.queryByTestId("batch-import-summary")).toBeNull();
  expect(screen.getByLabelText("Batch Analysis Package ZIP")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Import Batch" })).toBeDisabled());
});

test("defaults to zh-CN localized labels", async () => {
  mockedImportBatchRun.mockResolvedValue(successSummary);
  renderModal(() => undefined, "zh-CN");

  expect(screen.getByRole("button", { name: "导入批量包" })).toBeInTheDocument();
  expect(screen.getByLabelText("批量分析包 ZIP")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Import Batch" })).toBeNull();

  fireEvent.change(screen.getByLabelText("批量分析包 ZIP"), {
    target: { files: [new File(["zip"], "batch.zip", { type: "application/zip" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "导入批量包" }));

  const summary = await screen.findByTestId("batch-import-summary");
  expect(summary).toHaveTextContent("导入结果");
  expect(summary).toHaveTextContent("批量包 ID");
});
