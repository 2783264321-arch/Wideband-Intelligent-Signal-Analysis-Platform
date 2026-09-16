import { ConfigProvider, theme as antdTheme } from "antd";
import { createContext, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  THEME_STORAGE_KEY,
  readStoredThemePreference,
  resolveTheme,
  subscribeSystemTheme,
} from "./themePreference";
import type { ResolvedTheme, ThemePreference } from "./types";

export interface ThemeContextValue {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readStoredThemePreference());
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark());
  const resolved = resolveTheme(preference, systemDark);

  useEffect(() => subscribeSystemTheme(setSystemDark), []);

  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Ignore storage failures (private mode / unavailable storage).
    }
  }, []);

  const value = useMemo(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );

  return (
    <ThemeContext.Provider value={value}>
      <ConfigProvider
        theme={{
          algorithm: resolved === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        }}
      >
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}
