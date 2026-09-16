import type { ResolvedTheme, ThemePreference } from "./types";

export const THEME_STORAGE_KEY = "wisa.theme";

export function readStoredThemePreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
  } catch {
    return "system";
  }
}

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === "system") return systemPrefersDark ? "dark" : "light";
  return preference;
}

export function subscribeSystemTheme(listener: (prefersDark: boolean) => void): () => void {
  let media: MediaQueryList;
  try {
    media = window.matchMedia("(prefers-color-scheme: dark)");
  } catch {
    return () => {};
  }
  const handler = (event: MediaQueryListEvent) => listener(event.matches);
  media.addEventListener("change", handler);
  return () => media.removeEventListener("change", handler);
}
