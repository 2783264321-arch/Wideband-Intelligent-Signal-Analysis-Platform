import { Alert, Button, Card, Select, Space, Spin, Table } from "antd";
import { useEffect, useState } from "react";
import { compareDatasetBenchmarks, listDatasetBenchmarkItems, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetEvaluationItem } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

function toErrorText(error: unknown): string {
  if (error instanceof PlatformApiError) return error.display;
  if (error instanceof Error) return error.message;
  return String(error);
}

export interface BenchmarkComparePanelProps {
  evaluationAId: string;
  evaluationBId: string;
  onOpenCase: (recordingId: string, runAId: string, runBId: string) => void;
}

export function BenchmarkComparePanel({ evaluationAId, evaluationBId, onOpenCase }: BenchmarkComparePanelProps) {
  const { t } = useLocalization();
  const [result, setResult] = useState<DatasetBenchmarkCompareResult>();
  const [itemsA, setItemsA] = useState<DatasetEvaluationItem[]>([]);
  const [itemsB, setItemsB] = useState<DatasetEvaluationItem[]>([]);
  const [recordingId, setRecordingId] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const compared = await compareDatasetBenchmarks(evaluationAId, evaluationBId);
      if (cancelled) return;
      setResult(compared);
      setRecordingId(undefined);
      if (!compared.comparable) { setItemsA([]); setItemsB([]); return; }
      const [a, b] = await Promise.all([
        listDatasetBenchmarkItems(evaluationAId),
        listDatasetBenchmarkItems(evaluationBId),
      ]);
      if (!cancelled) { setItemsA(a); setItemsB(b); }
    };
    void load().catch((e: unknown) => setError(toErrorText(e)));
    return () => { cancelled = true; };
  }, [evaluationAId, evaluationBId]);

  if (error) return <Alert type="error" message={error} />;
  if (!result) return <Spin tip={t("benchmarks.comparing")} />;
  if (!result.comparable) {
    return <Alert type="warning" showIcon message={t("compare.notComparable")} description={result.reasons.join(", ")} />;
  }

  const a = result.aggregateA;
  const b = result.aggregateB;
  if (!a || !b) return <Alert type="error" message={t("benchmarks.pairMissingAggregate")} />;
  const fmt = (value: number | null | undefined) => value == null ? t("metrics.notAvailable") : value.toFixed(4);
  const derivedDelta = (x: number | null | undefined, y: number | null | undefined) =>
    x == null || y == null ? null : y - x;
  const metricRows = [
    { key: "map5095", metric: t("metrics.classAwareMap50_95"), a: a.classAware?.map50_95, b: b.classAware?.map50_95, delta: result.deltas.class_aware_map50_95 },
    { key: "map50", metric: t("metrics.classAwareMap50"), a: a.classAware?.map50, b: b.classAware?.map50, delta: result.deltas.class_aware_map50 },
    { key: "loc", metric: t("compare.metricLocalizationAp50_95"), a: a.localization.ap50_95, b: b.localization.ap50_95, delta: result.deltas.localization_ap50_95 },
    { key: "matched", metric: t("metrics.matchedAccuracy"), a: a.classificationOnMatched?.matchedAccuracy, b: b.classificationOnMatched?.matchedAccuracy, delta: result.deltas.matched_accuracy },
    { key: "f1", metric: t("runMetrics.classAwareF1"), a: a.classAware?.operating.f1, b: b.classAware?.operating.f1, delta: derivedDelta(a.classAware?.operating.f1, b.classAware?.operating.f1) },
  ];

  const byB = new Map(itemsB.map((item) => [item.recordingId, item]));
  const options = itemsA.flatMap((left) => {
    const right = byB.get(left.recordingId);
    return left.analysisRunId && right?.analysisRunId
      ? [{ value: left.recordingId, label: left.recordingName, runAId: left.analysisRunId, runBId: right.analysisRunId }]
      : [];
  });
  const selected = options.find((option) => option.value === recordingId);

  return (
    <Card title={t("benchmarks.comparisonTitle")}>
      <Table
        rowKey="key"
        pagination={false}
        dataSource={metricRows}
        columns={[
          { title: t("compare.columnMetric"), dataIndex: "metric" },
          { title: "A", render: (_, row) => fmt(row.a) },
          { title: "B", render: (_, row) => fmt(row.b) },
          { title: "Δ (B-A)", render: (_, row) => fmt(row.delta) },
        ]}
      />
      <Space>
        <Select aria-label={t("benchmarks.compareRecording")} placeholder={t("import.selectRecording")} value={recordingId} onChange={setRecordingId} options={options} style={{ width: 260 }} />
        <Button
          disabled={!selected}
          onClick={() => selected && onOpenCase(selected.value, selected.runAId, selected.runBId)}
        >
          {t("benchmarks.openCaseComparison")}
        </Button>
      </Space>
    </Card>
  );
}