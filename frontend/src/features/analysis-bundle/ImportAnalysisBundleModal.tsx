import { Alert, Button, Descriptions, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  importAnalysisBundle,
  isAnalysisBundleDatasetMismatch,
  type AnalysisBundleImportSummary,
} from "../../api/analysisBundles";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

export interface ImportAnalysisBundleModalProps {
  open: boolean;
  onClose: () => void;
  /** Called when the user finishes a successful import (e.g. refresh the library). */
  onImported?: () => void;
}

/**
 * Product entry for importing previously computed WISA analysis results.
 *
 * Results transport only: it uploads an Analysis Bundle and creates ordinary
 * completed AnalysisRuns/Detections on the backend. It never installs or checks
 * pipelines, never selects executors, and never reruns inference.
 */
export function ImportAnalysisBundleModal({
  open,
  onClose,
  onImported,
}: ImportAnalysisBundleModalProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AnalysisBundleImportSummary | null>(null);

  const canSubmit = !submitting && file !== null && summary === null;

  const reset = () => {
    setFile(null);
    setError(null);
    setSummary(null);
    setSubmitting(false);
  };

  const close = () => {
    reset();
    onClose();
  };

  const submitImport = async () => {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      setSummary(await importAnalysisBundle(file));
    } catch (reason) {
      setError(
        isAnalysisBundleDatasetMismatch(reason)
          ? t("analysisBundle.datasetMismatch")
          : toErrorText(reason, t("analysisBundle.failure")),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const done = () => {
    const imported = summary !== null;
    close();
    if (imported) onImported?.();
  };

  const viewFirstImportedResult = () => {
    const first = summary?.sampleRunMapping[0];
    if (!first) return;
    close();
    navigate(
      `/spectrum/${encodeURIComponent(first.recordingId)}?run=${encodeURIComponent(first.analysisRunId)}`,
    );
  };

  return (
    <Modal
      title={t("analysisBundle.modalTitle")}
      open={open}
      onCancel={close}
      footer={
        summary
          ? [
              summary.sampleRunMapping.length > 0 ? (
                <Button
                  key="view"
                  type="primary"
                  data-testid="analysis-bundle-view-results"
                  onClick={viewFirstImportedResult}
                >
                  {t("analysisBundle.viewImportedResults")}
                </Button>
              ) : null,
              <Button key="done" data-testid="analysis-bundle-done" onClick={done}>
                {t("analysisBundle.done")}
              </Button>,
            ]
          : [
              <Button key="cancel" onClick={close}>{t("common.cancel")}</Button>,
              <Button
                key="import"
                type="primary"
                loading={submitting}
                disabled={!canSubmit}
                data-testid="analysis-bundle-import-submit"
                onClick={() => void submitImport()}
              >
                {t("analysisBundle.submit")}
              </Button>,
            ]
      }
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          data-testid="analysis-bundle-error"
          style={{ marginBottom: 16 }}
        />
      ) : null}

      {summary ? (
        <div data-testid="analysis-bundle-summary">
          <Alert
            type={summary.alreadyImported ? "info" : "success"}
            showIcon
            message={
              summary.alreadyImported
                ? t("analysisBundle.alreadyImported")
                : t("analysisBundle.success")
            }
            data-testid={
              summary.alreadyImported
                ? "analysis-bundle-idempotent"
                : "analysis-bundle-success"
            }
            style={{ marginBottom: 16 }}
          />
          <Descriptions title={t("analysisBundle.summaryTitle")} column={1} size="small">
            <Descriptions.Item label={t("analysisBundle.datasetMatched")}>
              {summary.datasetName} / {summary.datasetSplit}
            </Descriptions.Item>
            <Descriptions.Item label={t("analysisBundle.samplesMatched")}>
              <span data-testid="analysis-bundle-sample-count">{summary.sampleCount}</span>
            </Descriptions.Item>
            <Descriptions.Item label={t("analysisBundle.runsImported")}>
              <span data-testid="analysis-bundle-run-count">{summary.createdRuns}</span>
            </Descriptions.Item>
            <Descriptions.Item label={t("analysisBundle.detectionsImported")}>
              <span data-testid="analysis-bundle-detection-count">
                {summary.createdDetections}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label={t("analysisBundle.alreadyImportedLabel")}>
              {summary.alreadyImported ? t("analysisBundle.yes") : t("analysisBundle.no")}
            </Descriptions.Item>
          </Descriptions>
        </div>
      ) : (
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <label htmlFor="analysis-bundle-zip">{t("analysisBundle.selectLabel")}</label>
          <input
            id="analysis-bundle-zip"
            type="file"
            accept=".zip,application/zip"
            data-testid="analysis-bundle-file-input"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <Typography.Text type="secondary">{t("analysisBundle.selectHint")}</Typography.Text>
          <Typography.Text type="secondary">{t("analysisBundle.importHelp")}</Typography.Text>
          <Typography.Text type="secondary">{t("analysisBundle.noRawIq")}</Typography.Text>
        </Space>
      )}
    </Modal>
  );
}
