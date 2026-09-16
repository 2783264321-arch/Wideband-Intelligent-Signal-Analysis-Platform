import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { ExperimentComparePanel } from "./ExperimentComparePanel";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

function evaluationWire(id: string, name: string, status = "completed") {
  return { id, name, status };
}

function compareResult(comparable: boolean, reason: string, a = "eval_a", b = "eval_b") {
  return {
    comparable,
    reasons: comparable ? [] : [reason],
    evaluation_a_id: a,
    evaluation_b_id: b,
    aggregate_a: null,
    aggregate_b: null,
    deltas: comparable ? { localization_ap50: 0.1 } : {},
  };
}

async function choose(label: string, name: string) {
  fireEvent.mouseDown(screen.getByLabelText(label));
  const options = await screen.findAllByTitle(name);
  fireEvent.click(options[options.length - 1]);
}

function NavButton({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>{label}</button>;
}

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

test("manual comparison of two completed evaluations renders not-comparable reasons", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([evaluationWire("eval_a", "Eval A"), evaluationWire("eval_b", "Eval B")]));
    }
    if (url.includes("/api/dataset-benchmarks/compare")) {
      return new Response(JSON.stringify(compareResult(false, "label_space_mismatch")));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(renderWithLocalization(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>));

  await screen.findByLabelText("Experiment A");
  await choose("Experiment A", "Eval A");
  await choose("Experiment B", "Eval B");
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByText("label_space_mismatch")).toBeInTheDocument();
});

test("changing a selected evaluation clears the previous result", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([
        evaluationWire("eval_a", "Eval A"), evaluationWire("eval_b", "Eval B"), evaluationWire("eval_c", "Eval C"),
      ]));
    }
    if (url.includes("/api/dataset-benchmarks/compare")) {
      return new Response(JSON.stringify(compareResult(true, "")));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(renderWithLocalization(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>));
  await screen.findByLabelText("Experiment A");
  await choose("Experiment A", "Eval A");
  await choose("Experiment B", "Eval B");
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  expect(await screen.findByTestId("compare-delta-table")).toBeInTheDocument();

  await choose("Experiment A", "Eval C");
  expect(screen.queryByTestId("compare-delta-table")).toBeNull();
});

test("shows an explicit empty state when no comparison is possible", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/dataset-benchmarks")) return new Response(JSON.stringify([]));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(renderWithLocalization(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>));
  expect(await screen.findByText(/No completed experiments with linked evaluations to compare/i)).toBeInTheDocument();
});

test("a stale compare success cannot render against a changed identity", async () => {
  let resolveR1: ((value: Response) => void) | undefined;
  const r1 = new Promise<Response>((resolve) => { resolveR1 = resolve; });
  let compareCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([
        evaluationWire("eval_a1", "Eval A1"), evaluationWire("eval_a2", "Eval A2"), evaluationWire("eval_b1", "Eval B1"),
      ]));
    }
    if (url.includes("/api/dataset-benchmarks/compare")) {
      compareCalls += 1;
      if (compareCalls === 1) return r1;
      return new Response(JSON.stringify(compareResult(false, "R2_ONLY")));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(renderWithLocalization(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>));
  await screen.findByLabelText("Experiment A");
  await choose("Experiment A", "Eval A1");
  await choose("Experiment B", "Eval B1");
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));

  await choose("Experiment A", "Eval A2");
  await act(async () => { resolveR1?.(new Response(JSON.stringify(compareResult(false, "STALE_A1B1")))); await Promise.resolve(); });
  expect(screen.queryByText("STALE_A1B1")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  await waitFor(() => expect(screen.getByText("R2_ONLY")).toBeInTheDocument());
});

test("a stale compare error cannot render against a changed identity", async () => {
  let resolveR1: ((value: Response) => void) | undefined;
  const r1 = new Promise<Response>((resolve) => { resolveR1 = resolve; });
  let compareCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([
        evaluationWire("eval_a1", "Eval A1"), evaluationWire("eval_a2", "Eval A2"), evaluationWire("eval_b1", "Eval B1"),
      ]));
    }
    if (url.includes("/api/dataset-benchmarks/compare")) {
      compareCalls += 1;
      if (compareCalls === 1) return r1;
      return new Response(JSON.stringify(compareResult(true, "")));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(renderWithLocalization(<MemoryRouter><ExperimentComparePanel /></MemoryRouter>));
  await screen.findByLabelText("Experiment A");
  await choose("Experiment A", "Eval A1");
  await choose("Experiment B", "Eval B1");
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));

  await choose("Experiment A", "Eval A2");
  await act(async () => {
    resolveR1?.(new Response(JSON.stringify({ error: { code: "STALE_ERR", message: "old pair failed", details: {} } }), { status: 409 }));
    await Promise.resolve();
  });
  expect(screen.queryByText(/STALE_ERR/)).toBeNull();
});

test("unlinked imported evaluations compare independently of DatasetExperiment", async () => {
  const compareBodies: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/dataset-benchmarks/compare")) {
      compareBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return new Response(JSON.stringify(compareResult(true, "", "eval_import_a", "eval_import_b")));
    }
    if (u.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([
        evaluationWire("eval_import_a", "Imported A"), evaluationWire("eval_import_b", "Imported B"),
      ]));
    }
    // The compare path must not consult DatasetExperiment at all.
    if (u.includes("/api/dataset-experiments")) throw new Error("compare must not load experiments");
    throw new Error(`Unexpected request: ${u}`);
  }));

  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/experiments?tab=compare&a=eval_import_a&b=eval_import_b"]}>
        <ExperimentComparePanel />
      </MemoryRouter>,
    ),
  );
  expect(await screen.findByTestId("compare-delta-table")).toBeInTheDocument();
  expect(compareBodies).toHaveLength(1);
  expect(compareBodies[0]).toMatchObject({
    evaluation_a_id: "eval_import_a",
    evaluation_b_id: "eval_import_b",
  });
});

test("URL pair change rehydrates once and does not duplicate requests", async () => {
  const compareBodies: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/dataset-benchmarks/compare")) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      compareBodies.push(body);
      return new Response(JSON.stringify(compareResult(true, "", String(body.evaluation_a_id), String(body.evaluation_b_id))));
    }
    if (u.endsWith("/api/dataset-benchmarks")) {
      return new Response(JSON.stringify([
        evaluationWire("eval_a", "Eval A"), evaluationWire("eval_b", "Eval B"),
        evaluationWire("eval_c", "Eval C"), evaluationWire("eval_d", "Eval D"),
      ]));
    }
    throw new Error(`Unexpected request: ${u}`);
  }));

  render(
    renderWithLocalization(
      <MemoryRouter initialEntries={["/experiments?tab=compare&a=eval_a&b=eval_b"]}>
        <ExperimentComparePanel />
        <NavButton to="/experiments?tab=compare&a=eval_c&b=eval_d" label="go-cd" />
      </MemoryRouter>,
    ),
  );
  expect(await screen.findByTestId("compare-delta-table")).toBeInTheDocument();
  await waitFor(() => expect(compareBodies).toHaveLength(1));
  expect(compareBodies[0]).toMatchObject({ evaluation_a_id: "eval_a", evaluation_b_id: "eval_b" });

  fireEvent.click(screen.getByRole("button", { name: "go-cd" }));
  await waitFor(() => expect(compareBodies).toHaveLength(2));
  expect(compareBodies[1]).toMatchObject({ evaluation_a_id: "eval_c", evaluation_b_id: "eval_d" });

  // Re-navigating to the same pair must not duplicate the request.
  fireEvent.click(screen.getByRole("button", { name: "go-cd" }));
  await act(async () => { await Promise.resolve(); });
  expect(compareBodies).toHaveLength(2);
});
