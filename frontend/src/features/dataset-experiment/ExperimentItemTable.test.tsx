import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ExperimentItemTable } from "./ExperimentItemTable";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

function itemWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "item_1",
    experiment_id: "exp_1",
    manifest_order: 0,
    recording_id: "rec_1",
    recording_name: "rec_1",
    status: "completed",
    last_error_type: null,
    last_error_message: null,
    latest_analysis_run_id: "run_1",
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

afterEach(() => { vi.unstubAllGlobals(); });

test("renders items with status and a link to the analysis run", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([itemWire()]))));
  render(renderWithLocalization(
    <MemoryRouter>
      <ExperimentItemTable experimentId="exp_1" />
    </MemoryRouter>,
    ),
  );
  expect(await screen.findByText("rec_1")).toBeInTheDocument();
  expect(screen.getByText("Completed")).toBeInTheDocument();
  const viewResult = screen.getByRole("link", { name: "View Result" });
  expect(viewResult).toHaveAttribute("href", "/spectrum/rec_1?run=run_1");
  expect(screen.getByRole("link", { name: "Open Sample" })).toHaveAttribute("href", "/samples/rec_1");
});

test("a failed item shows its bounded last error type", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    itemWire({ status: "failed", last_error_type: "INPUT_INCOMPATIBLE", last_error_message: "nope", latest_analysis_run_id: null }),
  ]))));
  render(renderWithLocalization(
    <MemoryRouter>
      <ExperimentItemTable experimentId="exp_1" />
    </MemoryRouter>,
    ),
  );
  expect(await screen.findByText("Failed")).toBeInTheDocument();
  expect(screen.getByText("INPUT_INCOMPATIBLE")).toBeInTheDocument();
});

test("localizes a known item status in zh-CN while preserving raw identity", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    itemWire({ status: "queued", latest_analysis_run_id: "run_42" }),
  ]))));
  render(renderWithLocalization(
    <MemoryRouter>
      <ExperimentItemTable experimentId="exp_1" />
    </MemoryRouter>,
    { locale: "zh-CN" },
  ));
  expect(await screen.findByText("rec_1")).toBeInTheDocument();
  expect(screen.getByText("排队中")).toBeInTheDocument();
  expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  // An unfinished item offers Open Sample but no View Result yet.
  expect(screen.getByRole("link", { name: "打开样本" })).toHaveAttribute("href", "/samples/rec_1");
  expect(screen.queryByRole("link", { name: "查看结果" })).toBeNull();
});

test("shows an empty state when there are no items", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(renderWithLocalization(
    <MemoryRouter>
      <ExperimentItemTable experimentId="exp_1" />
    </MemoryRouter>,
    ),
  );
  expect(await screen.findByText(/No items/i)).toBeInTheDocument();
});
