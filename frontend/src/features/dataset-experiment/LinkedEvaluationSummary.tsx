import { Alert, Button, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { getDatasetBenchmark, PlatformApiError, retryDatasetExperimentEvaluation } from "../../api/client";
import type { DatasetEvaluation, DatasetExperiment } from "../../api/types";
import { EvaluationMetricsView } from "../evaluation/EvaluationMetricsView";
import { useLocalization } from "../../localization/useLocalization";
import { evaluationStatusKey } from "../analysis-run/statusModel";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/**
 * Minimal linked-evaluation summary for a DatasetExperiment.
 *
 * Retry Evaluation is previewed only when the backend-visible preconditions hold:
 * experiment failed + linked evaluation failed/interrupted + all inference items
 * completed. The backend remains the final authority.
 */
export function LinkedEvaluationSummary({
  experiment,
  onRetryAccepted,
}: {
  experiment: DatasetExperiment;
  onRetryAccepted?: (experiment: DatasetExperiment) => void;
}) {
  const { t } = useLocalization();
  const [evaluation, setEvaluation] = useState<DatasetEvaluation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (experiment.datasetEvaluationId === null) return undefined;
    let active = true;
    // A lifecycle change (status or evaluation id) invalidates the previously
    // displayed evaluation; do not show a stale failed evaluation for a new lifecycle.
    setEvaluation(null);
    setError(null);
    getDatasetBenchmark(experiment.datasetEvaluationId)
      .then((next) => {
        if (!active) return;
        setError(null);
        setEvaluation(next);
      })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, [experiment.datasetEvaluationId, experiment.status]);

  if (experiment.datasetEvaluationId === null) {
    return null;
  }
  if (evaluation === null) {
    return error !== null
      ? <Alert type="error" showIcon message={t("evaluation.loadError")} description={error} />
      : null;
  }

  const statusKey = evaluationStatusKey(evaluation.status);
  const statusLabel = statusKey !== null ? t(statusKey) : evaluation.status;
  const allItemsCompleted =
    experiment.queuedItems === 0 &&
    experiment.runningItems === 0 &&
    experiment.failedItems === 0 &&
    experiment.completedItems === experiment.expectedItems;
  const eligible =
    experiment.status === "failed" &&
    allItemsCompleted &&
    (evaluation.status === "failed" || evaluation.status === "interrupted");

  const retry = async () => {
    setError(null);
    try {
      const updated = await retryDatasetExperimentEvaluation(experiment.id);
      // Backend-returned experiment is authoritative; let the parent restart from it.
      if (onRetryAccepted) onRetryAccepted(updated);
    } catch (reason) {
      setError(toErrorText(reason));
    }
  };

  return (
    <Space direction="vertical" size={4} data-testid="linked-evaluation-summary">
      <Space size={8} wrap>
        <Typography.Text>{t("evaluation.summaryPrefix")} {evaluation.id}</Typography.Text>
        <Tag>{statusLabel}</Tag>
        <Typography.Text>{evaluation.evaluatedRecordings} / {evaluation.expectedRecordings}</Typography.Text>
        <Typography.Text type="secondary">{t("evaluation.missing")} {evaluation.missingRecordings}</Typography.Text>
        <Typography.Text type="secondary">{t("evaluation.coverage")} {evaluation.coverage}</Typography.Text>
      </Space>
      {evaluation.errorType !== null ? <Typography.Text code>{evaluation.errorType}</Typography.Text> : null}
      {evaluation.errorMessage !== null ? <Typography.Text type="danger">{evaluation.errorMessage}</Typography.Text> : null}
      {eligible ? <Button onClick={() => void retry()}>{t("common.retryEvaluation")}</Button> : null}
      {error !== null ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <EvaluationMetricsView evaluation={evaluation} />
    </Space>
  );
}
