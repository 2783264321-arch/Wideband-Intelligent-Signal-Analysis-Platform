import { apiGet, apiPostJson, PlatformApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());

test("apiGet preserves the backend error code, message and details", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({
      error: {
        code: "INVALID_BENCHMARK_TRANSITION",
        message: "Only pending evaluations can be started.",
        details: { stage: "running" },
      },
    }),
    { status: 409 },
  )));

  let thrown: unknown;
  try {
    await apiGet("/api/dataset-benchmarks/abc");
  } catch (e) {
    thrown = e;
  }
  expect(thrown).toBeInstanceOf(PlatformApiError);
  const err = thrown as PlatformApiError;
  expect(err.status).toBe(409);
  expect(err.code).toBe("INVALID_BENCHMARK_TRANSITION");
  expect(err.message).toBe("Only pending evaluations can be started.");
  expect(err.details).toEqual({ stage: "running" });
  expect(err.display).toBe("INVALID_BENCHMARK_TRANSITION: Only pending evaluations can be started.");
});

test("apiPostJson preserves an incomplete-batch backend error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({
      error: {
        code: "IMPORTED_BATCH_DATASET_INCOMPLETE",
        message: "Imported batch does not cover the current frozen Recording manifest exactly.",
        details: {},
      },
    }),
    { status: 422 },
  )));

  let thrown: unknown;
  try {
    await apiPostJson("/api/dataset-benchmarks/resolve-imported-batch", { import_fingerprint: "a".repeat(64) });
  } catch (e) {
    thrown = e;
  }
  const err = thrown as PlatformApiError;
  expect(err.status).toBe(422);
  expect(err.code).toBe("IMPORTED_BATCH_DATASET_INCOMPLETE");
  expect(err.message).toBe("Imported batch does not cover the current frozen Recording manifest exactly.");
});

test("non-JSON error body falls back to a generic HTTP error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("internal error", { status: 500 })));
  let thrown: unknown;
  try {
    await apiGet("/api/dataset-benchmarks");
  } catch (e) {
    thrown = e;
  }
  const err = thrown as PlatformApiError;
  expect(err).toBeInstanceOf(PlatformApiError);
  expect(err.status).toBe(500);
  expect(err.code).toBe("HTTP_500");
  expect(err.message).toBe("API request failed: 500");
});