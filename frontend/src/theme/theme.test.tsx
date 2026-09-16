import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { ThemeProvider } from "./ThemeProvider";
import { useTheme } from "./useTheme";
import { THEME_STORAGE_KEY } from "./themePreference";

function Probe() {
  const { preference, resolved, setPreference } = useTheme();
  return (
    <div>
      <span data-testid="probe">{`${preference}:${resolved}`}</span>
      <button onClick={() => setPreference("dark")}>set-dark</button>
    </div>
  );
}

function setSystemPrefersDark(prefersDark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("dark") ? prefersDark : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

test("defaults to system and resolves light when the OS prefers light", () => {
  setSystemPrefersDark(false);
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByTestId("probe")).toHaveTextContent("system:light");
});

test("system preference resolves dark when the OS prefers dark", () => {
  setSystemPrefersDark(true);
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  expect(screen.getByTestId("probe")).toHaveTextContent("system:dark");
});

test("setPreference persists and sets documentElement data-theme", () => {
  setSystemPrefersDark(false);
  render(
    <ThemeProvider>
      <Probe />
    </ThemeProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "set-dark" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("dark:dark");
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
});
