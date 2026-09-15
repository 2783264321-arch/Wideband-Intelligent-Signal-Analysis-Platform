import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("renders the V1 navigation and defaults to the Recording Library", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );

  expect(screen.getByText("Recordings")).toBeInTheDocument();
  expect(screen.getByText("Experiments")).toBeInTheDocument();
  expect(screen.getByText("Algorithm Lab")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Recording Library" })).toBeInTheDocument();
});
