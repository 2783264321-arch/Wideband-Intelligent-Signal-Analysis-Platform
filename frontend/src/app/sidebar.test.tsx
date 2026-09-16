import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function renderApp() {
  return render(
    <LocalizationProvider initialLocale="en-US">
      <ThemeProvider>
        <MemoryRouter initialEntries={["/recordings"]}>
          <App />
        </MemoryRouter>
      </ThemeProvider>
    </LocalizationProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })));
});
afterEach(() => { vi.unstubAllGlobals(); });

test("sidebar collapse persists across remount", () => {
  const first = renderApp();
  expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
  fireEvent.click(screen.getByTestId("sidebar-toggle"));
  expect(window.localStorage.getItem("wisa.sidebarCollapsed")).toBe("true");
  first.unmount();
  renderApp();
  expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");
});
