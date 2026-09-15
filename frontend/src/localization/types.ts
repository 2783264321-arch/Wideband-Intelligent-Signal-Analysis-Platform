import type { enUS } from "./messages.en-US";

export type Locale = "zh-CN" | "en-US";

/** Every key is derived from the English resource; Chinese must cover all of them. */
export type MessageKey = keyof typeof enUS;

export type MessageVars = Record<string, string | number | null>;
