import { Alert, Button, Card, Col, Descriptions, Row, Space, Spin, Statistic, Typography } from "antd";
import { useEffect, useState } from "react";
import { getDatasetBenchmark, PlatformApiError } from "../../api/client";
import type { DatasetEvaluation } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { EvaluationMetricsView } from "./EvaluationMetricsView";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export interface DatasetEvaluationViewProps {
  evaluationId: string;
  onBack: () => void;
}

/**
 * User-facing Dataset Evaluation view.
 *
 * Summary metrics come from the backend aggregate (`localization`), and the
 * detailed localization / classification / per-class / confusion sections are
 * rendered by the shared `EvaluationMetricsView` (never duplicated here).
 */
export function DatasetEvaluationView({ evaluationId, onBack }: DatasetEvaluationViewProps) {
  const { t } = useLocalization();
  const [evaluation, setEvaluation] = useState<DatasetEvaluation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setEvaluation(null);
    setError(null);
    getDatasetBenchmark(evaluationId)
      .then((value) => { if (active) setEvaluation(value); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, [evaluationId]);

  if (error !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("datasetEvaluation.loadError")}
        description={error}
      />
    );
  }
  if (evaluation === null) return <Spin tip={t("datasetEvaluation.loading")} />;

  const localization = evaluation.aggregateMetrics?.localization ?? null;
  const fmt = (value: number | null | undefined): string =>
    typeof value === "number" ? value.toFixed(4) : t("metrics.notAvailable");
  const coverage =
    typeof evaluation.coverage === "number"
      ? `${(evaluation.coverage * 100).toFixed(1)}%`
      : t("metrics.notAvailable");

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }} data-testid="dataset-evaluation-view">
      <Space wrap align="center">
        <Button onClick={onBack}>{t("datasetEvaluation.backToAnalyses")}</Button>
        <Typography.Title level={3} style={{ margin: 0 }}>{evaluation.name}</Typography.Title>
        <Typography.Text type="secondary">{evaluation.pipelineId} · {evaluation.pipelineVersion}</Typography.Text>
      </Space>

      <Card title={t("datasetEvaluation.summary")} data-testid="dataset-evaluation-summary">
        <Row gutter={16}>
          <Col span={4}><Statistic title={t("datasetEvaluation.map50")} value={fmt(localization?.ap50)} /></Col>
          <Col span={4}><Statistic title={t("datasetEvaluation.map50_95")} value={fmt(localization?.ap50_95)} /></Col>
          <Col span={4}><Statistic title={t("metrics.precision")} value={fmt(localization?.operating.precision)} /></Col>
          <Col span={4}><Statistic title={t("metrics.recall")} value={fmt(localization?.operating.recall)} /></Col>
          <Col span={4}><Statistic title={t("metrics.f1")} value={fmt(localization?.operating.f1)} /></Col>
        </Row>
        <Descriptions column={2} size="small" style={{ marginTop: 16 }} data-testid="dataset-evaluation-coverage">
          <Descriptions.Item label={t("datasetEvaluation.sampleCoverage")}>
            {evaluation.evaluatedRecordings} / {evaluation.expectedRecordings}
          </Descriptions.Item>
          <Descriptions.Item label={t("datasetEvaluation.coverage")}>{coverage}</Descriptions.Item>
        </Descriptions>
      </Card>

      <EvaluationMetricsView evaluation={evaluation} />
    </Space>
  );
}
