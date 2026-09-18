import {
  ANALYSIS_BUNDLE_DATASET_MISMATCH,
  exportAnalysisBundle,
  filenameFromContentDisposition,
  importAnalysisBundle,
  isAnalysisBundleDatasetMismatch,
} from "./analysisBundles";
import { PlatformApiError } from "./client";

const summaryWire = {
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
  dataset_analysis_id: "exp_imported_1",
  sample_run_mapping: [
    { sample_key: "0000", sample_name: "name_0", recording_id: "rec_1", analysis_run_id: "run_1" },
  ],
};

beforeEach(() => {
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => "blob:mock");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

function fetchCalls(fetchMock: ReturnType<typeof vi.fn>): unknown[][] {
  return fetchMock.mock.calls;
}

test("export requests the dataset experiment export endpoint and triggers a download", async () => {
  const fetchMock = vi.fn(async () =>
    new Response(new Blob(["zip-bytes"]), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": 'attachment; filename="results.zip"',
      },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  const filename = await exportAnalysisBundle("exp_1");

  expect(String(fetchCalls(fetchMock)[0][0])).toContain("/api/dataset-experiments/exp_1/export");
  expect(filename).toBe("results.zip");
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
});

test("export falls back to a safe filename when Content-Disposition is absent", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new Blob(["x"]), { status: 200 })));

  const filename = await exportAnalysisBundle("exp_42");
  expect(filename).toBe("wisa-analysis-exp_42.zip");
});

test("filenameFromContentDisposition parses quoted, extended and missing headers", () => {
  expect(filenameFromContentDisposition('attachment; filename="results.zip"')).toBe("results.zip");
  expect(filenameFromContentDisposition("attachment; filename*=UTF-8''r%C3%A9sults.zip")).toBe("résults.zip");
  expect(filenameFromContentDisposition(null)).toBeNull();
});

test("export sanitizes a hostile Content-Disposition filename to its basename", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(new Blob(["x"]), {
      status: 200,
      headers: { "Content-Disposition": 'attachment; filename="../../evil.zip"' },
    }),
  ));

  expect(await exportAnalysisBundle("exp_9")).toBe("evil.zip");
});

test("import posts the file as multipart and maps the summary to camelCase", async () => {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(summaryWire), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);

  const file = new File(["zip"], "bundle.zip", { type: "application/zip" });
  const summary = await importAnalysisBundle(file);

  const [url, init] = fetchCalls(fetchMock)[0];
  expect(String(url)).toContain("/api/analysis-bundles/import");
  expect((init as RequestInit).method).toBe("POST");
  expect((init as RequestInit).body).toBeInstanceOf(FormData);
  expect(((init as RequestInit).body as FormData).get("file")).toBe(file);

  expect(summary).toMatchObject({
    schemaVersion: 1,
    bundleId: "bundle_abc",
    datasetName: "SpaceNet",
    datasetSplit: "test",
    pipelineId: "ghost_pipeline",
    sampleCount: 2,
    detectionCount: 3,
    createdRuns: 2,
    createdDetections: 3,
    alreadyImported: false,
    datasetAnalysisId: "exp_imported_1",
  });
  expect(summary.sampleRunMapping[0]).toEqual({
    sampleKey: "0000",
    sampleName: "name_0",
    recordingId: "rec_1",
    analysisRunId: "run_1",
  });
});

test("structured backend errors are preserved on import", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
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
  ));

  let thrown: unknown;
  try {
    await importAnalysisBundle(new File(["zip"], "bundle.zip"));
  } catch (reason) {
    thrown = reason;
  }
  expect(thrown).toBeInstanceOf(PlatformApiError);
  const error = thrown as PlatformApiError;
  expect(error.status).toBe(409);
  expect(error.code).toBe(ANALYSIS_BUNDLE_DATASET_MISMATCH);
  expect(error.display).toContain(ANALYSIS_BUNDLE_DATASET_MISMATCH);
  expect(isAnalysisBundleDatasetMismatch(error)).toBe(true);
});
