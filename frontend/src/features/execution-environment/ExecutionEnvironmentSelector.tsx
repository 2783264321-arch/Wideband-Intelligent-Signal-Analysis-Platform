import { Alert, Button, Select, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { getDiscoveredLocalRuntimes, type DiscoveredLocalRuntime } from "../../api/client";
import { optionsFromSelection, type ExecutorOptionKey, type ExecutorOptionState, type ExecutorOptionStateKind } from "./executionEnvironment";
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
  unresolved: "executionEnv.notAvailable",
};

/**
 * Compact execution environment selector.
 *
 * The happy path is a single Select: Auto resolves to a concrete executor and is
 * rendered as "Auto · Local CPU"; only Auto plus runnable manual executors are
 * offered. Unavailable executors never appear as primary choices and are only
 * disclosed behind the secondary "Environment details" action.
 *
 * Usability and reasons come exclusively from backend facts (ExecutorSelection).
 * The component never decides capability, never falls back between executors, and
 * never derives an executor from a pipeline id. Auto stays Auto across the request
 * boundary.
 */
export function ExecutionEnvironmentSelector({
  selection,
  loading,
  error,
  value,
  onChange,
  disabled,
  compact = false,
  showSelector = true,
}: ExecutionEnvironmentSelectorProps) {
  const { t } = useLocalization();
  const [showDetails, setShowDetails] = useState(false);

  const options = useMemo(() => optionsFromSelection(selection), [selection]);
  const locked = disabled === true || loading || error !== null;
  const [runtimes, setRuntimes] = useState<DiscoveredLocalRuntime[]>([]);

  // Discovered runtimes load only when details are opened (lazy diagnostic).
  useEffect(() => {
    if (!showDetails || runtimes.length > 0) return;
    let active = true;
    getDiscoveredLocalRuntimes()
      .then((items) => {
        if (items.length > 0) setRuntimes(items);
      })
      .catch(() => {
        /* diagnostic only: absence is fine */
      });
    return () => {
      active = false;
    };
  }, [showDetails, runtimes.length]);

  const labelFor = (option: ExecutorOptionState): string => t(optionLabelKey[option.key]);

  const resolvedExecutor = selection !== null ? selection.resolvedExecutor : null;
  const resolvedLabel = resolvedExecutor === null
    ? null
    : (resolvedExecutor as ExecutorOptionKey) in optionLabelKey
      ? t(optionLabelKey[resolvedExecutor as ExecutorOptionKey])
      : resolvedExecutor;

  const selectOptions = useMemo(() => {
    const auto = options.find((option) => option.key === "auto");
    const autoLabel = resolvedLabel !== null
      ? t("executionEnv.autoResolved", { executor: resolvedLabel })
      : t("executionEnv.auto");
    return [
      { value: "auto", label: autoLabel, disabled: auto === undefined || !auto.enabled },
      ...options
        .filter((option) => option.key !== "auto" && option.enabled)
        .map((option) => ({ value: option.key as string, label: labelFor(option), disabled: false })),
    ];
  }, [options, resolvedLabel, t]);

  const selectedKey = value.mode === "auto" ? "auto" : value.executor;
  const noRunnable = !loading && error === null && selection !== null && !options.some((option) => option.enabled);

  return (
    <div data-testid="execution-environment-selector">
      {showSelector && !compact ? (
        <Typography.Text type="secondary" style={{ display: "block", fontSize: 12 }}>
          {t("executionEnv.fieldLabel")}
        </Typography.Text>
      ) : null}
      {showSelector ? (
        <Select
          data-testid="execution-environment-select"
          aria-label={t("executionEnv.title")}
          style={{ minWidth: compact ? 170 : 220 }}
          value={loading ? undefined : selectedKey}
          placeholder={loading ? t("executionEnv.checking") : undefined}
          loading={loading}
          disabled={locked}
          onChange={(next: string) => {
            onChange(next === "auto" ? { mode: "auto", executor: null } : { mode: "manual", executor: next });
          }}
          options={selectOptions}
        />
      ) : null}
      {error !== null ? (
        <Typography.Text type="danger" style={{ display: "block" }} data-testid="execution-environment-error">
          {error}
        </Typography.Text>
      ) : null}
      {noRunnable && !compact ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8, maxWidth: 360 }}
          message={t("executionEnv.noRunnableTitle")}
          description={t("executionEnv.noRunnableHint")}
          data-testid="execution-environment-no-runnable"
        />
      ) : null}
      <div>
        <Button type="link" size="small" style={{ paddingLeft: 0 }} onClick={() => setShowDetails((current) => !current)}>
          {noRunnable ? t("executionEnv.whyNotRunnable") : t("executionEnv.details")}
        </Button>
      </div>
      {showDetails ? (
        <div data-testid="execution-environment-details" style={{ maxWidth: 340 }}>
          {selection !== null && selection.reasonCode !== null ? (
            <Typography.Text type="secondary" data-testid="execution-environment-reason-code">
              {selection.reasonCode}
            </Typography.Text>
          ) : null}
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {options.map((option) => (
              <li key={option.key} data-testid={`execution-option-${option.key}`}>
                {labelFor(option)}: {t(stateLabelKey[option.state])}
                {option.reasonMessage !== null ? ` — ${option.reasonMessage}` : ""}
              </li>
            ))}
          </ul>
          {runtimes.length > 0 ? (
            <>
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
                {t("executionEnv.discoveredTitle")}
              </Typography.Text>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {runtimes.map((runtime) => (
                  <li key={runtime.pythonPath} data-testid="execution-runtime-discovered">
                    {runtime.version !== null ? `Python ${runtime.version}` : "Python"}
                    {": "}
                    {[runtime.torch ? "torch" : null, runtime.ultralytics ? "ultralytics" : null]
                      .filter(Boolean)
                      .join(" + ") || t("executionEnv.discoveredNoMl")}
                    {runtime.isConfiguredLocalCpu ? ` (${t("executionEnv.discoveredWired")})` : ""}
                  </li>
                ))}
              </ul>
              <Typography.Text type="secondary" style={{ display: "block", marginTop: 6 }}>
                {t("executionEnv.discoveredHint")}
              </Typography.Text>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
