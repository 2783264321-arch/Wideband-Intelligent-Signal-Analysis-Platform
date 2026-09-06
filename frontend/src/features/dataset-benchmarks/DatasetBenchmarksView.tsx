import { Button, Space, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { listDatasetBenchmarks, retryDatasetBenchmark, runDatasetBenchmark } from "../../api/client";
import type { DatasetEvaluation } from "../../api/types";
import { BenchmarkCreatePanel } from "./BenchmarkCreatePanel";
import { BenchmarkDetailView } from "./BenchmarkDetailView";
import { BenchmarkListTable } from "./BenchmarkListTable";

export interface DatasetBenchmarksViewProps {
  selectedBenchmarkId?: string;
  onBenchmarkOpen: (evaluationId?: string) => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}

export function DatasetBenchmarksView({ selectedBenchmarkId, onBenchmarkOpen, onOpenCase }: DatasetBenchmarksViewProps) {
  const [items, setItems] = useState<DatasetEvaluation[]>([]);
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const refresh = useCallback(async () => setItems(await listDatasetBenchmarks()), []);
  useEffect(() => { void refresh(); }, [refresh]);

  const start = async (id: string) => { await runDatasetBenchmark(id); await refresh(); onBenchmarkOpen(id); };
  const retry = async (id: string) => { await retryDatasetBenchmark(id); await runDatasetBenchmark(id); await refresh(); onBenchmarkOpen(id); };

  if (selectedBenchmarkId) {
    return (
      <BenchmarkDetailView
        evaluationId={selectedBenchmarkId}
        onBack={() => onBenchmarkOpen(undefined)}
        onOpenCase={onOpenCase}
      />
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Typography.Title level={3} style={{ margin: 0 }}>Dataset Benchmarks</Typography.Title>
        <Button onClick={() => setCreating(true)}>New Benchmark</Button>
      </Space>
      {creating ? <BenchmarkCreatePanel onCreated={(id) => { setCreating(false); void refresh(); onBenchmarkOpen(id); }} /> : null}
      <BenchmarkListTable
        items={items}
        selectedIds={selectedIds}
        onSelectedIdsChange={setSelectedIds}
        onOpen={onBenchmarkOpen}
        onRun={(id) => void start(id)}
        onRetry={(id) => void retry(id)}
      />
    </Space>
  );
}