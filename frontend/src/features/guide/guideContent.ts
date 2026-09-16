import zh from "./user-guide.zh-CN.md?raw";
import en from "./user-guide.en-US.md?raw";
import type { Locale } from "../../localization/types";

export function guideMarkdownFor(locale: Locale): string {
  return locale === "en-US" ? en : zh;
}
