import { useContext } from "react";
import { LocalizationContext, type LocalizationContextValue } from "./LocalizationProvider";

export function useLocalization(): LocalizationContextValue {
  const context = useContext(LocalizationContext);
  if (context === null) {
    throw new Error("useLocalization must be used within a LocalizationProvider.");
  }
  return context;
}
