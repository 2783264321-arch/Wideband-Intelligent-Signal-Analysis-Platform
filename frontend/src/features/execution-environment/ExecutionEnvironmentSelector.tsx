import { Button, Radio, Space, Typography } from "antd";
import { useMemo, useState } from "react";
import { optionsFromSelection, type ExecutorOptionKey, type ExecutorOptionStateKind } from "./executionEnvironment";
import { useLocalization } from "../../localization/useLocalization";
import type { MessageKey } from "../../localization/types";
import type { ExecutionEnvironmentSelectorProps } from "./types";

const optionLabelKey: Record<ExecutorOptionKey, MessageKey> = {
  auto: "executionEnv.auto",
  local_cpu: "executionEnv.localCpu",
  local_gpu: "executionEnv.localGpu",
  remote_gpu: "executionEnv.remoteGpu",
};

const stateLabelKey: Record<ExecutorOptionStateKind, MessageKey> = {
  available: "executionEnv.available",
  not_configured: "executionEnv.notConfigured",
  not_certified: "executionEnv.notCertified",
  unsupported: "executionEnv.unsupported",
  temporarily_unavailable: "executionEnv.temporarilyUnavailable",
  unresolved: "executionEnv.unresolved",
};

/**
 * Reusable execution environment selector.
 *
 * The four options are always enumerated; usability and reasons come exclusively
 * from backend facts (ExecutorSelection). The component never decides capability,
 * never falls back between executors, and never derives an executor from a
 * pipeline id. Auto stays Auto across the request boundary.
 */
export function ExecutionEnvironmentSelector({
  selection,
  loading,
  error,
  value,
  onChange,
  disabled,
}: ExecutionEnvironmentSelectorProps) {
  const { t } = useLocalization();
  const options = useMemo(() => optionsFromSelection(selection).map((option) => ({
    ...option,
    label: t(optionLabelKey[option.key]),
  })), [selection, t]);
  const [showDetails, setShowDetails] = useState(false);

  const selectedKey: ExecutorOptionKey = value.mode === "auto" ? "auto" : (value.executor as ExecutorOptionKey);
  const selected = options.find((option) => option.key === selectedKey) ?? null;
  const locked = disabled === true || loading || error !== null;

  const summary = (() => {
    if (error !== null) return error;
    if (loading) return t("executionEnv.checking");
    if (selectedKey === "auto") {
      if (selection !== null && selection.resolvedExecutor !== null) {
        const resolvedKey = (selection.resolvedExecutor as ExecutorOptionKey) in optionLabelKey
          ? optionLabelKey[selection.resolvedExecutor as ExecutorOptionKey]
          : null;
        return t("executionEnv.recommended", {
          executor: resolvedKey !== null ? t(resolvedKey) : (selection.resolvedExecutor as string),
        });
      }
      return selected?.reasonMessage ?? null;
    }
    if (selected !== null && !selected.enabled) {
      return selected.reasonMessage ?? null;
    }
    return null;
  })();

  return (
    <div data-testid="execution-environment-selector">
      <Radio.Group
        value={selectedKey}
        disabled={locked}
        onChange={(event) => {
          const key = event.target.value as ExecutorOptionKey;
          onChange(key === "auto" ? { mode: "auto", executor: null } : { mode: "manual", executor: key });
        }}
      >
        <Space wrap>
          {options.map((option) => (
            <Radio.Button key={option.key} value={option.key} disabled={!option.enabled || locked}>
              {option.label}
            </Radio.Button>
          ))}
        </Space>
      </Radio.Group>
      {summary !== null ? (
        <Typography.Text type="secondary" data-testid="execution-environment-summary">
          {summary}
        </Typography.Text>
      ) : null}
      <div>
        <Button type="link" size="small" onClick={() => setShowDetails((current) => !current)}>
          {t("executionEnv.details")}
        </Button>
      </div>
      {showDetails ? (
        <div data-testid="execution-environment-details">
          <Typography.Text type="secondary" data-testid="execution-environment-reason-code">
            {selection?.reasonCode ?? "\u2014"}
          </Typography.Text>
          <ul>
            {options.map((option) => (
              <li key={option.key} data-testid={`execution-option-${option.key}`}>
                {option.label}: {t(stateLabelKey[option.state])}
                {option.reasonMessage !== null ? ` \u2014 ${option.reasonMessage}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
