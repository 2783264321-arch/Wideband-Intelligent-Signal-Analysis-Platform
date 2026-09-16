import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SettingsPage } from "./SettingsPage";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

function renderSettings() {
  return render(
    <LocalizationProvider initialLocale="en-US">
      <ThemeProvider>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </ThemeProvider>
    </LocalizationProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

test("theme preference persists", () => {
  renderSettings();
  fireEvent.click(screen.getByText("Dark"));
  expect(window.localStorage.getItem("wisa.theme")).toBe("dark");
});

test("sidebar collapsed preference persists", () => {
  renderSettings();
  fireEvent.click(screen.getByTestId("settings-sidebar"));
  expect(window.localStorage.getItem("wisa.sidebarCollapsed")).toBe("true");
});

test("guide link is present", () => {
  renderSettings();
  expect(screen.getByTestId("settings-open-guide")).toHaveAttribute("href", "/guide");
});
