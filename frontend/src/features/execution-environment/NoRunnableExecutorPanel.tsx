import { Alert } from "antd";
import type { ExecutorSelection } from "../../api/types";
import type { MessageKey } from "../../localization/types";
import { useLocalization } from "../../localization/useLocalization";
import { optionsFromSelection } from "./executionEnvironment";
import type { ExecutorOptionStateKind } from "./executionEnvironment";

const STATE_LABEL_KEYS: Record<ExecutorOptionStateKind, MessageKey> = {
  available: "executionEnv.available",
  not_configured: "executionEnv.notConfigured",
  not_certified: "executionEnv.notCertified",
  unsupported: "executionEnv.unsupported",
  temporarily_unavailable: "executionEnv.temporarilyUnavailable",
  unresolved: "executionEnv.unresolved",
};

export interface NoRunnableExecutorPanelProps {
  selection: ExecutorSelection | null;
}

/**
 * Candidate-level explanation for a selection with no runnable executor.
 * Renders only when the selection exists and no option is enabled. Never
 * enables execution; it only explains the backend-reported candidate states.
 */
export function NoRunnableExecutorPanel({ selection }: NoRunnableExecutorPanelProps) {
  const { t } = useLocalization();
  if (selection === null) return null;
  const options = optionsFromSelection(selection);
  if (options.some((option) => option.enabled)) return null;

  return (
    <div data-testid="no-runnable-executor-panel">
      <Alert
        type="warning"
        showIcon
        message={t("executionEnv.noRunnableTitle")}
        description={t("executionEnv.noRunnableHint")}
      />
      <ul style={{ marginTop: 8 }}>
        {options
          .filter((option) => option.key !== "auto")
          .map((option) => (
            <li key={option.key} data-testid={`no-runnable-option-${option.key}`}>
              {option.label}: {t(STATE_LABEL_KEYS[option.state])}
              {option.reasonMessage !== null ? ` — ${option.reasonMessage}` : ""}
            </li>
          ))}
      </ul>
    </div>
  );
}
