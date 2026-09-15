import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AttemptTimeline } from "./AttemptTimeline";
import { renderWithLocalization } from "../../test-utils/renderWithLocalization";

afterEach(() => { vi.unstubAllGlobals(); });

test("renders attempts with number, linked run and launch-requested time", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
    {
      id: "att_1",
      experiment_item_id: "item_1",
      attempt_number: 1,
      analysis_run_id: "run_1",
      launch_requested_at: "2026-09-15T00:00:00+00:00",
      created_at: null,
    },
    {
      id: "att_2",
      experiment_item_id: "item_1",
      attempt_number: 2,
      analysis_run_id: "run_2",
      launch_requested_at: null,
      created_at: null,
    },
  ]))));

  render(
    renderWithLocalization(
      <MemoryRouter>
        <AttemptTimeline experimentId="exp_1" itemId="item_1" />
      </MemoryRouter>,
    ),
  );

  expect(await screen.findByText("run_1")).toBeInTheDocument();
  expect(screen.getByText("run_2")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "run_1" })).toHaveAttribute("href", "/signals/run_1");
  expect(screen.getByText("2026-09-15T00:00:00+00:00")).toBeInTheDocument();
});

test("shows an empty state when there are no attempts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]))));
  render(
    renderWithLocalization(
      <MemoryRouter>
        <AttemptTimeline experimentId="exp_1" itemId="item_1" />
      </MemoryRouter>,
    ),
  );
  expect(await screen.findByText(/No attempts/i)).toBeInTheDocument();
});
