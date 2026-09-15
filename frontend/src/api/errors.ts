import { PlatformApiError } from "./client";

/**
 * Readable, bounded error text. Preserves the structured backend identity
 * (`code: message`) for PlatformApiError instead of collapsing to a generic
 * message.
 */
export function toErrorText(reason: unknown, fallback: string): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return fallback;
}
