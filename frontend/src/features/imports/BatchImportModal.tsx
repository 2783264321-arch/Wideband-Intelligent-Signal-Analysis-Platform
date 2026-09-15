import { Alert, Button, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { importBatchRun } from "../../api/client";
import { toErrorText } from "../../api/errors";
import type { BatchImportSummary } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

interface Props {
  open: boolean;
  onClose: () => void;
}

/**
 * Batch Analysis Package (BAPv1) import modal.
 *
 * Bounded presentation flow over the existing `POST /api/imported-runs/batch`
 * endpoint: choose a ZIP, import it, and display the returned summary + the
 * Recording -> AnalysisRun mapping. It never carries SSH credentials, remote
 * profiles, or server job controls, and it never retries automatically.
 */
export function BatchImportModal({ open, onClose }: Props) {
  const { t } = useLocalization();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<BatchImportSummary | null>(null);

  const canSubmit = !submitting && file !== null && summary === null;

  const submitImport = async () => {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await importBatchRun(file);
      setSummary(result);
    } catch (reason) {
      setError(toErrorText(reason, t("batchImport.failure")));
    } finally {
      setSubmitting(false);
    }
  };

  const resetAndClose = () => {
    setFile(null);
    setError(null);
    setSummary(null);
    onClose();
  };

  return (
    <Modal
      title={t("batchImport.title")}
      open={open}
      onCancel={() => resetAndClose()}
      footer={summary ? [
        <Button key="close" onClick={() => resetAndClose()}>{t("batchImport.close")}</Button>,
      ] : [
        <Button key="cancel" onClick={() => resetAndClose()}>{t("common.cancel")}</Button>,
        <Button key="import" type="primary" loading={submitting} disabled={!canSubmit} onClick={() => void submitImport()}>
          {t("batchImport.submit")}
        </Button>,
      ]}
    >
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
      {summary ? (
        <div data-testid="batch-import-summary">
          <Alert
            type={summary.alreadyImported ? "info" : "success"}
            showIcon
            message={summary.alreadyImported ? t("batchImport.idempotent") : t("batchImport.success")}
            style={{ marginBottom: 16 }}
          />
          <Typography.Title level={5}>{t("batchImport.summaryTitle")}</Typography.Title>
          <Space direction="vertical" size={2}>
            <Typography.Text>{t("batchImport.batchId")}: {summary.batchId}</Typography.Text>
            <Typography.Text>{t("batchImport.dataset")}: {summary.datasetName} / {summary.datasetSplit}</Typography.Text>
            <Typography.Text>{t("batchImport.pipeline")}: {summary.pipelineId} · {summary.pipelineVersion}</Typography.Text>
            <Typography.Text>
              {t("batchImport.results")}: {t("batchImport.itemCount")} {summary.itemCount} · {t("batchImport.detectionCount")} {summary.detectionCount}
            </Typography.Text>
            <Typography.Text>{t("batchImport.createdRuns")} = {summary.createdRuns}</Typography.Text>
            <Typography.Text>{t("batchImport.existingRuns")} = {summary.existingRuns}</Typography.Text>
            <Typography.Text>{t("batchImport.matchedRecordings")} = {summary.matchedRecordings}</Typography.Text>
          </Space>
          <Typography.Title level={5} style={{ marginTop: 12 }}>{t("batchImport.mappingTitle")}</Typography.Title>
          <ul data-testid="batch-import-mapping" style={{ paddingLeft: 20, margin: 0 }}>
            {summary.recordingRunMapping.map((row) => (
              <li key={row.analysisRunId}>
                {t("batchImport.columnRecording")}: {row.recordingName} → {t("batchImport.columnRun")}: {row.analysisRunId}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <label htmlFor="batch-analysis-package-zip">{t("batchImport.zipLabel")}</label>
          <input
            id="batch-analysis-package-zip"
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          {file === null ? (
            <Typography.Text type="secondary">{t("batchImport.chooseFile")}</Typography.Text>
          ) : null}
          <Typography.Text type="secondary">{t("batchImport.zipHint")}</Typography.Text>
        </Space>
      )}
    </Modal>
  );
}
