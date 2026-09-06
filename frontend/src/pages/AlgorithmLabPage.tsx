import { Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import { CaseAnalysisView } from "../features/algorithm-lab/CaseAnalysisView";
import { DatasetBenchmarksView } from "../features/dataset-benchmarks/DatasetBenchmarksView";

export function AlgorithmLabPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "benchmarks" ? "benchmarks" : "case";
  const recordingId = params.get("recording") ?? undefined;
  const runAId = params.get("runA") ?? undefined;
  const runBId = params.get("runB") ?? undefined;
  const benchmarkId = params.get("benchmark") ?? undefined;

  const patch = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    setParams(next);
  };

  return (
    <Tabs
      activeKey={tab}
      onChange={(key) => patch({ tab: key })}
      items={[
        {
          key: "case",
          label: "Case Analysis",
          children: (
            <CaseAnalysisView
              recordingId={recordingId}
              runAId={runAId}
              runBId={runBId}
              onRecordingChange={(id) => patch({ recording: id, runA: undefined, runB: undefined })}
              onRunAChange={(id) => patch({ runA: id })}
              onRunBChange={(id) => patch({ runB: id })}
            />
          ),
        },
        {
          key: "benchmarks",
          label: "Dataset Benchmarks",
          children: (
            <DatasetBenchmarksView
              selectedBenchmarkId={benchmarkId}
              onBenchmarkOpen={(id) => patch({ tab: "benchmarks", benchmark: id })}
              onOpenCase={(rec, a, b) => patch({ tab: "case", recording: rec, runA: a, runB: b })}
            />
          ),
        },
      ]}
    />
  );
}