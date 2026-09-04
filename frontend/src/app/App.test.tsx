import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

test("renders the V1 navigation and defaults to Recordings", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );

  expect(screen.getByText("Recordings")).toBeInTheDocument();
  expect(screen.getByText("Spectrum Analysis")).toBeInTheDocument();
  expect(screen.getByText("Signals")).toBeInTheDocument();
  expect(screen.getByText("Algorithm Lab")).toBeInTheDocument();
  expect(screen.getByText("Settings")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Recording Library" })).toBeInTheDocument();
});

test("returns contextual sidebar destinations to Recordings", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );

  await screen.findByRole("heading", { name: "Recording Library" });
  fireEvent.click(screen.getByRole("menuitem", { name: /Spectrum Analysis/ }));

  expect(await screen.findByRole("heading", { name: "Recording Library" })).toBeInTheDocument();
});
