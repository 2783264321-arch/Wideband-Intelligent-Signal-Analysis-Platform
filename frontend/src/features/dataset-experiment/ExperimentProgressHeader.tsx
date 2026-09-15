import { Space, Tag, Typography } from "antd";
import type { DatasetExperiment } from "../../api/types";
import { experimentStatusKey, reasonKey } from "../analysis-run/statusModel";
import { useLocalization } from "../../localization/useLocalization";

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "processing",
  evaluating: "processing",
  completed: "success",
  completed_with_failures: "warning",
  failed: "error",
};

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function ExperimentProgressHeader({ experiment }: { experiment: DatasetExperiment }) {
  const { t } = useLocalization();
  const requestedMode = experiment.requestedExecutionMode;
  const autoReasonCode = nonEmptyString(experiment.autoReasonCode);
  const autoReason = nonEmptyString(experiment.autoReason);
  const errorType = nonEmptyString(experiment.errorType);
  const errorMessage = nonEmptyString(experiment.errorMessage);
  const statusKey = experimentStatusKey(experiment.status);
  const reasonMsgKey = reasonKey(errorType);

  return (
    <Space direction="vertical" size={4} data-testid="experiment-progress-header">
      <Space size={8} wrap>
        <Tag color={STATUS_COLORS[experiment.status] ?? "default"}>
          {statusKey !== null ? t(statusKey) : experiment.status}
        </Tag>
        <Typography.Text>{t("provenance.executor")}: {experiment.executor}</Typography.Text>
        {requestedMode !== null ? (
          <Typography.Text type="secondary">{t("provenance.mode")}: {requestedMode}</Typography.Text>
        ) : null}
        <Typography.Text>
          {experiment.completedItems} / {experiment.expectedItems} {t("experiment.columnItems")}
        </Typography.Text>
      </Space>
      <Space size={12} wrap>
        <Typography.Text type="secondary">{t("status.queued")} {experiment.queuedItems}</Typography.Text>
        <Typography.Text type="secondary">{t("status.running")} {experiment.runningItems}</Typography.Text>
        <Typography.Text type="secondary">{t("status.completed")} {experiment.completedItems}</Typography.Text>
        <Typography.Text type="secondary">{t("status.failed")} {experiment.failedItems}</Typography.Text>
        <Typography.Text type="secondary">{t("experiment.attemptsTab")} {experiment.attemptCount}</Typography.Text>
      </Space>
      {autoReasonCode !== null ? (
        <Typography.Text type="secondary" data-testid="experiment-auto-reason">
          {t("provenance.autoReason")}: {autoReasonCode}
        </Typography.Text>
      ) : null}
      {reasonKey(autoReasonCode) !== null && autoReason !== null ? (
        <Typography.Text type="secondary" data-testid="experiment-auto-reason-technical">
          {t("common.technicalDetails")}: {autoReason}
        </Typography.Text>
      ) : null}
      {errorType !== null ? (
        <Typography.Text code data-testid="experiment-error-code">{errorType}</Typography.Text>
      ) : null}
      {reasonMsgKey !== null ? <Typography.Text type="secondary">{t(reasonMsgKey)}</Typography.Text> : null}
      {errorMessage !== null ? (
        reasonMsgKey !== null ? (
          <Typography.Text type="secondary" data-testid="experiment-error-technical-details">
            {t("common.technicalDetails")}: {errorMessage}
          </Typography.Text>
        ) : (
          <Typography.Text type="danger">{errorMessage}</Typography.Text>
        )
      ) : null}
    </Space>
  );
}
