import { ConfigProvider } from "antd";
import enUSAntd from "antd/locale/en_US";
import zhCNAntd from "antd/locale/zh_CN";
import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { enUS } from "./messages.en-US";
import { zhCN } from "./messages.zh-CN";
import type { Locale, MessageKey, MessageVars } from "./types";

export const LOCALE_STORAGE_KEY = "wisa.locale";

const RESOURCES: Record<Locale, Record<MessageKey, string>> = {
  "zh-CN": zhCN,
  "en-US": enUS as unknown as Record<MessageKey, string>,
};

export interface LocalizationContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, vars?: MessageVars) => string;
}

export const LocalizationContext = createContext<LocalizationContextValue | null>(null);

function readStoredLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return stored === "en-US" || stored === "zh-CN" ? stored : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

function interpolate(template: string, vars?: MessageVars): string {
  if (vars === undefined) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

export function LocalizationProvider({
  children,
  initialLocale,
}: {
  children: ReactNode;
  initialLocale?: Locale;
}) {
  const [locale, setLocaleState] = useState<Locale>(() => initialLocale ?? readStoredLocale());

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    } catch {
      // localStorage unavailable: keep the in-memory selection.
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback(
    (key: MessageKey, vars?: MessageVars) => interpolate(RESOURCES[locale][key] ?? key, vars),
    [locale],
  );

  const value = useMemo<LocalizationContextValue>(
    () => ({ locale, setLocale, t }),
    [locale, setLocale, t],
  );

  return (
    <LocalizationContext.Provider value={value}>
      <ConfigProvider locale={locale === "zh-CN" ? zhCNAntd : enUSAntd}>{children}</ConfigProvider>
    </LocalizationContext.Provider>
  );
}
