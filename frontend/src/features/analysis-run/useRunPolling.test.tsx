import { act, render } from "@testing-library/react";
import type { AnalysisRun, DetectionResult } from "../../api/types";
import { useRunPolling } from "./useRunPolling";

function Harness({
  runId,
  onRun,
  onDetections,
  onError = () => {},
}: {
  runId: string | undefined;
  onRun: (run: AnalysisRun) => void;
  onDetections: (detections: DetectionResult[]) => void;
  onError?: (reason: unknown) => void;
}) {
  useRunPolling({ runId, onRun, onDetections, onError });
  return null;
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

function runWire(status: string, id: string): Record<string, unknown> {
  return {
    id,
    recording_id: "rec_1",
    pipeline_id: "dummy",
    pipeline_version: "1.0",
    executor: "local_cpu",
    status,
    parameters_json: {},
    hardware_info_json: null,
    execution_metadata_json: null,
    started_at: null,
    finished_at: null,
    error_type: null,
    error_message: null,
    worker_pid: null,
    created_at: null,
  };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

async function tick(ms = 1000) {
  await act(async () => { vi.advanceTimersByTime(ms); });
}

test("pending keeps polling", async () => {
  const runCalls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runCalls.push(url);
    return jsonResponse(runWire("pending", "run_1"));
  }));
  const onRun = vi.fn();
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={onRun} onDetections={onDetections} />);

  await tick();
  await tick();
  expect(runCalls.length).toBeGreaterThanOrEqual(2);
  expect(onRun).toHaveBeenCalled();
  expect(onDetections).not.toHaveBeenCalled();
});

test("running keeps polling", async () => {
  const runCalls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runCalls.push(url);
    return jsonResponse(runWire("running", "run_1"));
  }));
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={vi.fn()} />);

  await tick();
  await tick();
  expect(runCalls.length).toBeGreaterThanOrEqual(2);
});

test("completed stops polling and fetches detections", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([{ id: "det_1" }]);
    runFetches += 1;
    return jsonResponse(runWire("completed", "run_1"));
  }));
  const onRun = vi.fn();
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={onRun} onDetections={onDetections} />);

  await tick();
  expect(onRun).toHaveBeenCalledTimes(1);
  expect(onDetections).toHaveBeenCalledTimes(1);
  const settled = runFetches;
  await tick(5000);
  expect(runFetches).toBe(settled);
});

test("failed stops polling without fetching detections", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runFetches += 1;
    return jsonResponse(runWire("failed", "run_1"));
  }));
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={onDetections} />);

  await tick();
  expect(onDetections).not.toHaveBeenCalled();
  const settled = runFetches;
  await tick(5000);
  expect(runFetches).toBe(settled);
});

test("interrupted stops polling without fetching detections", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runFetches += 1;
    return jsonResponse(runWire("interrupted", "run_1"));
  }));
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={onDetections} />);

  await tick();
  expect(onDetections).not.toHaveBeenCalled();
  const settled = runFetches;
  await tick(5000);
  expect(runFetches).toBe(settled);
});

test("changing runId cancels the old lifecycle and late results do not land", async () => {
  const seenRunIds: string[] = [];
  let resolveA: ((value: Response) => void) | undefined;
  const deferredA = new Promise<Response>((resolve) => { resolveA = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    if (url.endsWith("/api/analysis-runs/run_a")) return deferredA;
    if (url.endsWith("/api/analysis-runs/run_b")) return jsonResponse(runWire("pending", "run_b"));
    throw new Error(`Unexpected request: ${url}`);
  }));
  const onRun = vi.fn((run: AnalysisRun) => { seenRunIds.push(run.id); });

  const { rerender } = render(<Harness runId="run_a" onRun={onRun} onDetections={vi.fn()} />);
  await tick(); // run_a tick in flight (deferred)

  rerender(<Harness runId="run_b" onRun={onRun} onDetections={vi.fn()} />);
  await tick(); // run_b tick resolves

  await act(async () => {
    resolveA?.(runJson("run_a"));
    await Promise.resolve();
  });

  expect(seenRunIds).toContain("run_b");
  expect(seenRunIds).not.toContain("run_a");
});

test("unmount cancels polling", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runFetches += 1;
    return jsonResponse(runWire("pending", "run_1"));
  }));
  const { unmount } = render(<Harness runId="run_1" onRun={vi.fn()} onDetections={vi.fn()} />);
  await tick();
  const before = runFetches;
  unmount();
  await tick(5000);
  expect(runFetches).toBe(before);
});

function runJson(id: string): Response {
  return jsonResponse(runWire("pending", id));
}

// ---------------------------------------------------------------------------
// F2 polling corrective (Phase A)
// ---------------------------------------------------------------------------

test("serializes polling: at most one request in flight for a runId", async () => {
  let runFetches = 0;
  let resolveFirst: ((value: Response) => void) | undefined;
  const firstDeferred = new Promise<Response>((resolve) => { resolveFirst = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runFetches += 1;
    if (runFetches === 1) return firstDeferred;
    return jsonResponse(runWire("pending", "run_1"));
  }));
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={vi.fn()} />);

  await tick(); // first poll starts and is deferred
  expect(runFetches).toBe(1);

  // Advancing well beyond multiple intervals must NOT start overlapping polls.
  await tick(5000);
  expect(runFetches).toBe(1);

  // Resolve first poll; only then may the next poll be scheduled.
  await act(async () => {
    resolveFirst?.(jsonResponse(runWire("pending", "run_1")));
    await Promise.resolve();
  });
  await tick();
  expect(runFetches).toBe(2);
});

test("completed fetches detections exactly once and never re-polls", async () => {
  let runFetches = 0;
  let detectionFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) { detectionFetches += 1; return jsonResponse([{ id: "det_1" }]); }
    runFetches += 1;
    return jsonResponse(runWire("completed", "run_1"));
  }));
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={onDetections} />);

  await tick();
  expect(runFetches).toBe(1);
  expect(detectionFetches).toBe(1);
  expect(onDetections).toHaveBeenCalledTimes(1);

  await tick(6000);
  expect(runFetches).toBe(1);
  expect(detectionFetches).toBe(1);
});

test("a transient poll failure is surfaced and polling continues", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return jsonResponse([]);
    runFetches += 1;
    if (runFetches === 1) return new Response(JSON.stringify({ error: { code: "BOOM", message: "transient" } }), { status: 503 });
    return jsonResponse(runWire("pending", "run_1"));
  }));
  const onError = vi.fn();
  const onRun = vi.fn();
  render(<Harness runId="run_1" onRun={onRun} onDetections={vi.fn()} onError={onError} />);

  await tick();
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onRun).not.toHaveBeenCalled();

  await tick();
  expect(runFetches).toBe(2);
  expect(onRun).toHaveBeenCalledTimes(1);
});

test("a detections failure after completion is surfaced and does not restart polling", async () => {
  let runFetches = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/detections")) return new Response("nope", { status: 500 });
    runFetches += 1;
    return jsonResponse(runWire("completed", "run_1"));
  }));
  const onError = vi.fn();
  const onDetections = vi.fn();
  render(<Harness runId="run_1" onRun={vi.fn()} onDetections={onDetections} onError={onError} />);

  await tick();
  expect(onError).toHaveBeenCalledTimes(1);
  expect(onDetections).not.toHaveBeenCalled();

  await tick(6000);
  expect(runFetches).toBe(1);
});
