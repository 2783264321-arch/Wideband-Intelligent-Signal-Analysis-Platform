import { Space, Tag, Typography } from "antd";
import { describeReasonCode, describeRunStatus } from "./statusModel";

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
  interrupted: "warning",
};

export interface RunStatusBadgeProps {
  status: string;
  errorType?: string | null;
  errorMessage?: string | null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Renders an AnalysisRun status label plus any bounded terminal error identity.
 *
 * Any terminal run carrying an `errorType` keeps that bounded code visible; in
 * particular `interrupted` + `ANALYSIS_LAUNCH_AMBIGUOUS` renders as "Interrupted"
 * (never "Failed") with the raw code and message preserved.
 */
export function RunStatusBadge({ status, errorType, errorMessage }: RunStatusBadgeProps) {
  const label = describeRunStatus(status);
  const code = nonEmptyString(errorType);
  const message = nonEmptyString(errorMessage);

  return (
    <Space size={6} data-testid="run-status-badge">
      <Tag color={STATUS_COLORS[status] ?? "default"}>{label}</Tag>
      {code !== null ? (
        <Typography.Text code data-testid="run-error-code">{code}</Typography.Text>
      ) : null}
      {code !== null && describeReasonCode(code) !== code ? (
        <Typography.Text type="secondary">{describeReasonCode(code)}</Typography.Text>
      ) : null}
      {message !== null ? <Typography.Text type="danger">{message}</Typography.Text> : null}
    </Space>
  );
}
