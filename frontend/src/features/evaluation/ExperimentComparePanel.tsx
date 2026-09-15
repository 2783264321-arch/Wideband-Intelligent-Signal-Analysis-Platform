import { Alert, Button, Select, Space } from "antd";
import { useEffect, useRef, useState } from "react";
import { compareDatasetBenchmarks, listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetExperiment } from "../../api/types";
import { CompareDeltaTable } from "./CompareDeltaTable";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export function ExperimentComparePanel() {
  const [experiments, setExperiments] = useState<DatasetExperiment[]>([]);
  const [aId, setAId] = useState<string | null>(null);
  const [bId, setBId] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetBenchmarkCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Only the request that owns the current generation may set result/error.
  // Selector changes invalidate outstanding work synchronously.
  const compareGenerationRef = useRef(0);

  useEffect(() => {
    let active = true;
    listDatasetExperiments()
      .then((items) => { if (active) setExperiments(items); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, []);

  const eligible = experiments.filter(
    (experiment) => experiment.datasetEvaluationId !== null && experiment.status === "completed",
  );
  const options = eligible.map((experiment) => ({ value: experiment.id, label: experiment.name }));

  const invalidateComparison = () => {
    compareGenerationRef.current += 1;
    setResult(null);
    setError(null);
  };

  const changeA = (id: string) => {
    invalidateComparison();
    setAId(id);
  };

  const changeB = (id: string) => {
    invalidateComparison();
    setBId(id);
  };

  const run = async () => {
    const a = eligible.find((experiment) => experiment.id === aId);
    const b = eligible.find((experiment) => experiment.id === bId);
    if (!a || !b || a.datasetEvaluationId === null || b.datasetEvaluationId === null) return;
    // Starting a newer compare invalidates any older compare still in flight.
    const generation = ++compareGenerationRef.current;
    setError(null);
    try {
      // Compare the experiments' linked evaluations (backend authority).
      const next = await compareDatasetBenchmarks(a.datasetEvaluationId, b.datasetEvaluationId);
      if (generation !== compareGenerationRef.current) return;
      setResult(next);
    } catch (reason) {
      if (generation !== compareGenerationRef.current) return;
      setError(toErrorText(reason));
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error !== null ? <Alert type="error" showIcon description={error} /> : null}
      {eligible.length === 0 ? (
        <Alert type="info" showIcon message="No completed experiments with linked evaluations to compare." />
      ) : null}
      <Space wrap>
        <Select aria-label="Experiment A" style={{ width: 240 }} value={aId ?? undefined} onChange={changeA} options={options} />
        <Select aria-label="Experiment B" style={{ width: 240 }} value={bId ?? undefined} onChange={changeB} options={options} />
        <Button
          type="primary"
          disabled={aId === null || bId === null || aId === bId}
          onClick={() => void run()}
        >
          Compare
        </Button>
      </Space>
      {result !== null && !result.comparable ? (
        <Alert
          type="warning"
          showIcon
          message="Not comparable"
          description={
            <ul>
              {result.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          }
        />
      ) : null}
      {result !== null && result.comparable ? <CompareDeltaTable result={result} /> : null}
    </Space>
  );
}
