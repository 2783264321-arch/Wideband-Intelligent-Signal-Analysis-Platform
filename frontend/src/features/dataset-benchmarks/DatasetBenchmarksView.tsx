import { Alert, Button, Space, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { listDatasetBenchmarks, PlatformApiError, retryDatasetBenchmark, runDatasetBenchmark } from "../../api/client";
import type { DatasetEvaluation } from "../../api/types";
import { BenchmarkComparePanel } from "./BenchmarkComparePanel";
import { BenchmarkCreatePanel } from "./BenchmarkCreatePanel";
import { BenchmarkDetailView } from "./BenchmarkDetailView";
import { BenchmarkListTable } from "./BenchmarkListTable";

export interface DatasetBenchmarksViewProps {
  selectedBenchmarkId?: string;
  onBenchmarkOpen: (evaluationId?: string) => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}

function toErrorText(error: unknown): string {
  if (error instanceof PlatformApiError) return error.display;
  if (error instanceof Error) return error.message;
  return String(error);
}

export function DatasetBenchmarksView({ selectedBenchmarkId, onBenchmarkOpen, onOpenCase }: DatasetBenchmarksViewProps) {
  const [items, setItems] = useState<DatasetEvaluation[]>([]);
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setItems(await listDatasetBenchmarks());
      setError(null);
    } catch (reason) {
      // Non-destructive: keep the current list and structure visible, only surface the error.
      setError(toErrorText(reason));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const start = async (id: string) => {
    try {
      await runDatasetBenchmark(id);
      await refresh();
      onBenchmarkOpen(id);
    } catch (reason) {
      setError(toErrorText(reason));
    }
  };

  const retry = async (id: string) => {
    try {
      await retryDatasetBenchmark(id);
      await runDatasetBenchmark(id);
      await refresh();
      onBenchmarkOpen(id);
    } catch (reason) {
      // Never auto-switch benchmark or auto-recreate the evaluation.
      setError(toErrorText(reason));
    }
  };

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
      {error ? <Alert type="error" showIcon message={error} closable onClose={() => setError(null)} /> : null}
      {creating ? <BenchmarkCreatePanel onCreated={(id) => { setCreating(false); void refresh(); onBenchmarkOpen(id); }} /> : null}
      {selectedIds.length === 2 ? <Button onClick={() => setShowCompare(true)}>Compare Selected</Button> : null}
      {showCompare && selectedIds.length === 2 ? (
        <BenchmarkComparePanel
          evaluationAId={selectedIds[0]}
          evaluationBId={selectedIds[1]}
          onOpenCase={onOpenCase}
        />
      ) : null}
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