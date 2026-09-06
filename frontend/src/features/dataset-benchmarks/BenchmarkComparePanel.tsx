import { Alert, Button, Card, Select, Space, Spin, Table } from "antd";
import { useEffect, useState } from "react";
import { compareDatasetBenchmarks, listDatasetBenchmarkItems } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetEvaluationItem } from "../../api/types";

export interface BenchmarkComparePanelProps {
  evaluationAId: string;
  evaluationBId: string;
  onOpenCase: (recordingId: string, runAId: string, runBId: string) => void;
}

export function BenchmarkComparePanel({ evaluationAId, evaluationBId, onOpenCase }: BenchmarkComparePanelProps) {
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
    void load().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => { cancelled = true; };
  }, [evaluationAId, evaluationBId]);

  if (error) return <Alert type="error" message={error} />;
  if (!result) return <Spin tip="Comparing benchmarks..." />;
  if (!result.comparable) {
    return <Alert type="warning" showIcon message="Not comparable" description={result.reasons.join(", ")} />;
  }

  const a = result.aggregateA;
  const b = result.aggregateB;
  if (!a || !b) return <Alert type="error" message="Comparable benchmark pair is missing aggregate metrics." />;
  const fmt = (value: number | null | undefined) => value == null ? "N/A" : value.toFixed(4);
  const derivedDelta = (x: number | null | undefined, y: number | null | undefined) =>
    x == null || y == null ? null : y - x;
  const metricRows = [
    { key: "map5095", metric: "Class-aware mAP50:95", a: a.classAware?.map50_95, b: b.classAware?.map50_95, delta: result.deltas.class_aware_map50_95 },
    { key: "map50", metric: "Class-aware mAP50", a: a.classAware?.map50, b: b.classAware?.map50, delta: result.deltas.class_aware_map50 },
    { key: "loc", metric: "Localization AP50:95", a: a.localization.ap50_95, b: b.localization.ap50_95, delta: result.deltas.localization_ap50_95 },
    { key: "matched", metric: "Matched Accuracy", a: a.classificationOnMatched?.matchedAccuracy, b: b.classificationOnMatched?.matchedAccuracy, delta: result.deltas.matched_accuracy },
    { key: "f1", metric: "Class-aware F1", a: a.classAware?.operating.f1, b: b.classAware?.operating.f1, delta: derivedDelta(a.classAware?.operating.f1, b.classAware?.operating.f1) },
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
    <Card title="Benchmark Comparison">
      <Table
        rowKey="key"
        pagination={false}
        dataSource={metricRows}
        columns={[
          { title: "Metric", dataIndex: "metric" },
          { title: "A", render: (_, row) => fmt(row.a) },
          { title: "B", render: (_, row) => fmt(row.b) },
          { title: "Δ (B-A)", render: (_, row) => fmt(row.delta) },
        ]}
      />
      <Space>
        <Select aria-label="Compare Recording" placeholder="Select a Recording" value={recordingId} onChange={setRecordingId} options={options} style={{ width: 260 }} />
        <Button
          disabled={!selected}
          onClick={() => selected && onOpenCase(selected.value, selected.runAId, selected.runBId)}
        >
          Open Case Comparison
        </Button>
      </Space>
    </Card>
  );
}