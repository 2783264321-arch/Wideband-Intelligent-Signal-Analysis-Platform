import { Alert, Button, Card, Descriptions, Space, Spin, Table, Typography } from "antd";
import { useEffect, useState } from "react";
import { compareDatasetBenchmarks, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkAggregateMetrics, DatasetBenchmarkCompareResult } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { SampleDifferencesTable } from "./SampleDifferencesTable";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export interface DatasetEvaluationComparePanelProps {
  evaluationAId: string;
  evaluationBId: string;
  evaluationAName?: string;
  evaluationBName?: string;
  onBack: () => void;
}

interface MetricRow {
  key: string;
  metric: string;
  a: number | null | undefined;
  b: number | null | undefined;
  delta: number | null;
}

/**
 * Compares two completed Dataset Evaluations via the backend compare endpoint.
 *
 * Pipeline A/B values are read from the backend response (`aggregate_a/b`);
 * only the factual B−A difference is derived for display. No ranking or winner
 * is produced on the frontend. A non-comparable response renders an
 * informational alert and never a locally computed comparison.
 */
export function DatasetEvaluationComparePanel({
  evaluationAId,
  evaluationBId,
  evaluationAName,
  evaluationBName,
  onBack,
}: DatasetEvaluationComparePanelProps) {
  const { t } = useLocalization();
  const [result, setResult] = useState<DatasetBenchmarkCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setResult(null);
    setError(null);
    compareDatasetBenchmarks(evaluationAId, evaluationBId)
      .then((value) => { if (active) setResult(value); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, [evaluationAId, evaluationBId]);

  const back = <Button onClick={onBack}>{t("datasetEvaluation.backToAnalyses")}</Button>;

  if (error !== null) {
    return <Alert type="error" showIcon message={error} />;
  }
  if (result === null) {
    return (
      <Space direction="vertical">
        {back}
        <Spin tip={t("pipelineCompare.comparing")} />
      </Space>
    );
  }

  if (!result.comparable) {
    return (
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {back}
        <Alert
          type="info"
          showIcon
          data-testid="pipeline-compare-not-comparable"
          message={t("pipelineCompare.notComparable")}
          description={
            result.reasons.length > 0 ? (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{t("pipelineCompare.reasons")}</Typography.Text>
                <ul style={{ margin: 0 }}>
                  {result.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </Space>
            ) : undefined
          }
        />
      </Space>
    );
  }

  const fmt = (value: number | null | undefined): string =>
    typeof value === "number" ? value.toFixed(4) : t("metrics.notAvailable");
  const deriveDelta = (a: number | null | undefined, b: number | null | undefined): number | null =>
    typeof a === "number" && typeof b === "number" ? b - a : null;
  const pick = (
    a: DatasetBenchmarkAggregateMetrics | null,
    b: DatasetBenchmarkAggregateMetrics | null,
    select: (aggregate: DatasetBenchmarkAggregateMetrics) => number | null | undefined,
    backendDelta: number | null | undefined,
  ) => {
    const va = a ? select(a) : null;
    const vb = b ? select(b) : null;
    return { a: va, b: vb, delta: backendDelta ?? deriveDelta(va, vb) };
  };

  const rows: MetricRow[] = [
    {
      key: "map50",
      metric: t("datasetEvaluation.map50"),
      ...pick(result.aggregateA, result.aggregateB, (x) => x.localization.ap50, result.deltas.localization_ap50),
    },
    {
      key: "map50_95",
      metric: t("datasetEvaluation.map50_95"),
      ...pick(result.aggregateA, result.aggregateB, (x) => x.localization.ap50_95, result.deltas.localization_ap50_95),
    },
    {
      key: "precision",
      metric: t("metrics.precision"),
      ...pick(result.aggregateA, result.aggregateB, (x) => x.localization.operating.precision, undefined),
    },
    {
      key: "recall",
      metric: t("metrics.recall"),
      ...pick(result.aggregateA, result.aggregateB, (x) => x.localization.operating.recall, undefined),
    },
    {
      key: "f1",
      metric: t("metrics.f1"),
      ...pick(result.aggregateA, result.aggregateB, (x) => x.localization.operating.f1, undefined),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }} data-testid="pipeline-compare">
      {back}
      <Card title={t("pipelineCompare.title")}>
        <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
          <Descriptions.Item label={t("pipelineCompare.pipelineA")}>{evaluationAName ?? result.evaluationAId}</Descriptions.Item>
          <Descriptions.Item label={t("pipelineCompare.pipelineB")}>{evaluationBName ?? result.evaluationBId}</Descriptions.Item>
        </Descriptions>
        <Table
          rowKey="key"
          pagination={false}
          size="small"
          dataSource={rows}
          columns={[
            { title: t("compare.columnMetric"), dataIndex: "metric" },
            { title: t("pipelineCompare.pipelineA"), render: (_: unknown, row) => fmt(row.a) },
            { title: t("pipelineCompare.pipelineB"), render: (_: unknown, row) => fmt(row.b) },
            { title: t("pipelineCompare.columnDelta"), render: (_: unknown, row) => fmt(row.delta) },
          ]}
        />
      </Card>
      <SampleDifferencesTable result={result} />
    </Space>
  );
}
