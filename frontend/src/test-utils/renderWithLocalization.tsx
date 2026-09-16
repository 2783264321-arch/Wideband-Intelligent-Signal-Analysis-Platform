import type { ReactNode } from "react";
import { LocalizationProvider } from "../localization/LocalizationProvider";
import { ThemeProvider } from "../theme/ThemeProvider";

export interface RenderWithLocalizationOptions {
  /** Business-behavior tests default to en-US; localization acceptance tests explicitly pass "zh-CN". */
  locale?: "zh-CN" | "en-US";
}

/** Wrap arbitrary UI in the shell providers (locale + theme) for localized components. */
export function renderWithLocalization(children: ReactNode, options: RenderWithLocalizationOptions = {}) {
  const locale = options.locale ?? "en-US";
  return (
    <LocalizationProvider initialLocale={locale}>
      <ThemeProvider>{children}</ThemeProvider>
    </LocalizationProvider>
  );
}
