import { Space, Tag, Typography } from "antd";
import type { DatasetExperiment } from "../../api/types";
import { describeExperimentStatus, describeReasonCode } from "../analysis-run/statusModel";

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
  const requestedMode = experiment.requestedExecutionMode;
  const autoReasonCode = nonEmptyString(experiment.autoReasonCode);
  const autoReason = nonEmptyString(experiment.autoReason);
  const errorType = nonEmptyString(experiment.errorType);
  const errorMessage = nonEmptyString(experiment.errorMessage);

  return (
    <Space direction="vertical" size={4} data-testid="experiment-progress-header">
      <Space size={8} wrap>
        <Tag color={STATUS_COLORS[experiment.status] ?? "default"}>
          {describeExperimentStatus(experiment.status)}
        </Tag>
        <Typography.Text>Executor: {experiment.executor}</Typography.Text>
        {requestedMode !== null ? <Typography.Text type="secondary">Mode: {requestedMode}</Typography.Text> : null}
        <Typography.Text>
          {experiment.completedItems} / {experiment.expectedItems} items
        </Typography.Text>
      </Space>
      <Space size={12} wrap>
        <Typography.Text type="secondary">queued {experiment.queuedItems}</Typography.Text>
        <Typography.Text type="secondary">running {experiment.runningItems}</Typography.Text>
        <Typography.Text type="secondary">completed {experiment.completedItems}</Typography.Text>
        <Typography.Text type="secondary">failed {experiment.failedItems}</Typography.Text>
        <Typography.Text type="secondary">attempts {experiment.attemptCount}</Typography.Text>
      </Space>
      {autoReasonCode !== null ? (
        <Typography.Text type="secondary">Auto reason: {autoReasonCode}</Typography.Text>
      ) : null}
      {autoReason !== null ? <Typography.Text type="secondary">{autoReason}</Typography.Text> : null}
      {errorType !== null ? (
        <Typography.Text code data-testid="experiment-error-code">{errorType}</Typography.Text>
      ) : null}
      {errorType !== null && describeReasonCode(errorType) !== errorType ? (
        <Typography.Text type="secondary">{describeReasonCode(errorType)}</Typography.Text>
      ) : null}
      {errorMessage !== null ? <Typography.Text type="danger">{errorMessage}</Typography.Text> : null}
    </Space>
  );
}
