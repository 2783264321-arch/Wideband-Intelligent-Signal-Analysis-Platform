import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function AppWithLocale({ locale = "en-US" }: { locale?: "zh-CN" | "en-US" }) {
  return (
    <LocalizationProvider initialLocale={locale}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </LocalizationProvider>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("renders the task-oriented navigation and defaults to the Data Library", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AppWithLocale />
    </MemoryRouter>,
  );

  expect(screen.getByRole("menuitem", { name: /Data Library/ })).toBeInTheDocument();
  expect(screen.getByText("Analysis Overview")).toBeInTheDocument();
  expect(screen.getByText("Algorithm Lab")).toBeInTheDocument();
  expect(screen.getByText("User Guide")).toBeInTheDocument();
  expect(screen.getByText("Settings")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Data Library" })).toBeInTheDocument();
});
