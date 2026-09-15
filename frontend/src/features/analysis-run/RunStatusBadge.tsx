import { Space, Tag, Typography } from "antd";
import { reasonKey, runStatusKey } from "./statusModel";
import { useLocalization } from "../../localization/useLocalization";

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
 * Renders a localized AnalysisRun status label plus any bounded terminal error
 * identity: localized explanation (when the code is known) + raw code unchanged
 * + raw backend message as technical detail.
 *
 * In particular `interrupted` + `ANALYSIS_LAUNCH_AMBIGUOUS` renders as
 * Interrupted / 已中断 (never Failed) with the raw code and message preserved.
 * Unknown statuses/codes fall back to the raw backend identity verbatim.
 */
export function RunStatusBadge({ status, errorType, errorMessage }: RunStatusBadgeProps) {
  const { t } = useLocalization();
  const statusKey = runStatusKey(status);
  const label = statusKey !== null ? t(statusKey) : status;
  const code = nonEmptyString(errorType);
  const message = nonEmptyString(errorMessage);
  const reasonMsgKey = reasonKey(code);

  return (
    <Space size={6} data-testid="run-status-badge">
      <Tag color={STATUS_COLORS[status] ?? "default"}>{label}</Tag>
      {code !== null ? (
        <Typography.Text code data-testid="run-error-code">{code}</Typography.Text>
      ) : null}
      {reasonMsgKey !== null ? <Typography.Text type="secondary">{t(reasonMsgKey)}</Typography.Text> : null}
      {message !== null ? (
        reasonMsgKey !== null ? (
          <Typography.Text type="secondary" data-testid="run-error-technical-details">
            {t("common.technicalDetails")}: {message}
          </Typography.Text>
        ) : (
          <Typography.Text type="danger">{message}</Typography.Text>
        )
      ) : null}
    </Space>
  );
}
