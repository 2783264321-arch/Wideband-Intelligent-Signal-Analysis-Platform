import { useCallback, useState } from "react";

export const SIDEBAR_STORAGE_KEY = "wisa.sidebarCollapsed";

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export interface SidebarCollapsedState {
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
  toggle: () => void;
}

export function useSidebarCollapsed(): SidebarCollapsedState {
  const [collapsed, setCollapsedState] = useState<boolean>(() => readStoredCollapsed());

  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedState(value);
    try {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, value ? "true" : "false");
    } catch {
      // Ignore storage failures.
    }
  }, []);

  const toggle = useCallback(() => {
    setCollapsedState((current) => {
      const next = !current;
      try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "true" : "false");
      } catch {
        // Ignore storage failures.
      }
      return next;
    });
  }, []);

  return { collapsed, setCollapsed, toggle };
}
