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
  expect(screen.getByText("completed")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /run_1/ });
  expect(link).toHaveAttribute("href", "/signals/run_1");
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
  expect(await screen.findByText("failed")).toBeInTheDocument();
  expect(screen.getByText("INPUT_INCOMPATIBLE")).toBeInTheDocument();
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
