import { Space, Tag, Typography } from "antd";
import type { AnalysisRun } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

const SHORT_SHA_LENGTH = 8;

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function shortSha(value: unknown): string | null {
  const text = nonEmptyString(value);
  return text !== null ? text.slice(0, SHORT_SHA_LENGTH) : null;
}

/**
 * User-visible execution provenance for an AnalysisRun.
 *
 * Renders ONLY backend-projected allowlisted fields (concrete executor, optional
 * requested execution mode / auto reason / workload class, and optional
 * remote/device provenance). It never infers a ModelRelease, never fabricates a
 * requested mode, and never renders arbitrary private execution metadata.
 *
 * NOTE: `AnalysisRunRead` does not currently project a resolved model release, so
 * none is shown (see the approved Frontend V1 plan).
 */
export function RunProvenanceCard({ run }: { run: AnalysisRun }) {
  const { t } = useLocalization();
  const metadata = run.executionMetadata ?? {};
  const hardware = run.hardwareInfo ?? {};

  const requestedMode = nonEmptyString(metadata.requested_execution_mode);
  const autoReasonCode = nonEmptyString(metadata.auto_reason_code);
  const autoReason = nonEmptyString(metadata.auto_reason);
  const workloadClass = nonEmptyString(metadata.workload_class);
  const remoteProfile = nonEmptyString(metadata.remote_profile);
  const runtimeCommit = shortSha(metadata.required_remote_runtime_commit);
  const payloadSha = shortSha(metadata.payload_sha256);
  const remoteStartedAt = nonEmptyString(metadata.remote_started_at);
  const remoteFinishedAt = nonEmptyString(metadata.remote_finished_at);
  const deviceName = nonEmptyString(hardware.device_name);
  const deviceType = nonEmptyString(hardware.device_type);

  return (
    <Space direction="vertical" size={4} data-testid="run-provenance-card">
      <Tag color="geekblue">{t("provenance.executor")}: {run.executor}</Tag>
      {requestedMode !== null ? (
        <Typography.Text type="secondary">{t("provenance.mode")}: {requestedMode}</Typography.Text>
      ) : null}
      {autoReasonCode !== null ? (
        <Typography.Text type="secondary">{t("provenance.autoReason")}: {autoReasonCode}</Typography.Text>
      ) : null}
      {autoReason !== null ? (
        <Typography.Text type="secondary">{autoReason}</Typography.Text>
      ) : null}
      {workloadClass !== null ? (
        <Typography.Text type="secondary">{t("provenance.workload")}: {workloadClass}</Typography.Text>
      ) : null}
      {remoteProfile !== null ? (
        <Typography.Text type="secondary">{t("provenance.profile")}: {remoteProfile}</Typography.Text>
      ) : null}
      {deviceName !== null || deviceType !== null ? (
        <Typography.Text type="secondary">
          {t("provenance.device")}: {deviceName ?? deviceType}{deviceName !== null && deviceType !== null ? ` (${deviceType})` : ""}
        </Typography.Text>
      ) : null}
      {runtimeCommit !== null ? (
        <Typography.Text type="secondary">{t("provenance.runtimeCommit")}: {runtimeCommit}</Typography.Text>
      ) : null}
      {payloadSha !== null ? (
        <Typography.Text type="secondary">{t("provenance.payloadSha")}: {payloadSha}</Typography.Text>
      ) : null}
      {remoteStartedAt !== null || remoteFinishedAt !== null ? (
        <Typography.Text type="secondary">
          {t("provenance.remote")}: {remoteStartedAt ?? "—"} → {remoteFinishedAt ?? "—"}
        </Typography.Text>
      ) : null}
    </Space>
  );
}
