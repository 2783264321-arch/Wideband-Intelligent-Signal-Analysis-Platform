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

test("brand region stays mounted with a stable 64px height in both states", () => {
  renderApp();
  const expandedBrand = screen.getByTestId("sidebar-brand");
  expect(expandedBrand).toBeInTheDocument();
  expect(expandedBrand.style.height).toBe("64px");
  expect(expandedBrand.style.overflow).toBe("hidden");
  expect(expandedBrand.style.flexShrink).toBe("0");

  fireEvent.click(screen.getByTestId("sidebar-toggle"));
  expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");

  // Same contract after collapse: the brand region is not removed and keeps its height.
  const collapsedBrand = screen.getByTestId("sidebar-brand");
  expect(collapsedBrand).toBeInTheDocument();
  expect(collapsedBrand.style.height).toBe("64px");
  expect(collapsedBrand.style.overflow).toBe("hidden");
  expect(collapsedBrand.style.flexShrink).toBe("0");
});

test("brand title stays in the DOM when collapsed and is only visually hidden", () => {
  renderApp();
  const expandedTitle = screen.getByTestId("sidebar-brand-title");
  expect(expandedTitle).toHaveTextContent("Wideband Signal Lab");
  expect(expandedTitle.style.opacity).toBe("1");

  fireEvent.click(screen.getByTestId("sidebar-toggle"));

  const collapsedTitle = screen.getByTestId("sidebar-brand-title");
  expect(collapsedTitle).toHaveTextContent("Wideband Signal Lab");
  expect(collapsedTitle.style.opacity).toBe("0");
  expect(collapsedTitle.style.maxWidth).toBe("0px");

  // The compact brand mark survives collapse too.
  expect(screen.getByTestId("sidebar-brand-icon")).toBeInTheDocument();
});

test("navigation items remain present across collapse", () => {
  renderApp();
  const labels = ["Data Library", "Dataset Experiments", "Algorithm Lab", "User Guide", "Settings"];
  for (const label of labels) expect(screen.getByRole("menuitem", { name: new RegExp(label) })).toBeInTheDocument();

  fireEvent.click(screen.getByTestId("sidebar-toggle"));

  for (const label of labels) expect(screen.getByRole("menuitem", { name: new RegExp(label) })).toBeInTheDocument();
  expect(screen.getByTestId("sidebar-brand")).toBeInTheDocument();
});
