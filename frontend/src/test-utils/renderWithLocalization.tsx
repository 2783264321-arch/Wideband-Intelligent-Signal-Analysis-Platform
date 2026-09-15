import type { ReactNode } from "react";
import { LocalizationProvider } from "../localization/LocalizationProvider";

export interface RenderWithLocalizationOptions {
  /** Business-behavior tests default to en-US; localization acceptance tests explicitly pass "zh-CN". */
  locale?: "zh-CN" | "en-US";
}

/** Wrap arbitrary UI in a LocalizationProvider for localized components. */
export function renderWithLocalization(children: ReactNode, options: RenderWithLocalizationOptions = {}) {
  const locale = options.locale ?? "en-US";
  return <LocalizationProvider initialLocale={locale}>{children}</LocalizationProvider>;
}
