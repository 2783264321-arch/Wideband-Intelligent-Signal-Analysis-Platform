import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("primary navigation is exactly Recordings | Experiments | Algorithm Lab", () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
  const items = screen.getAllByRole("menuitem");
  expect(items).toHaveLength(3);
  expect(screen.getByRole("menuitem", { name: /Recordings/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Experiments/ })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: /Algorithm Lab/ })).toBeInTheDocument();
  expect(screen.queryByText("Settings")).toBeNull();
  expect(screen.queryByText("Spectrum Analysis")).toBeNull();
});

test("the Experiments route renders the Experiments tab shell", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("tab", { name: "Experiments" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Compare" })).toBeInTheDocument();
});

test("?tab=compare shows the Compare tab", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments?tab=compare"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("experiment-compare-page")).toBeInTheDocument();
});

test("the experiment detail route renders", async () => {
  render(
    <MemoryRouter initialEntries={["/experiments/exp_1"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByTestId("experiment-detail-page")).toBeInTheDocument();
});
