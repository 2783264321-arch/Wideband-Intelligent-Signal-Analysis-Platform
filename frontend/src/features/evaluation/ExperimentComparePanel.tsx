import { Alert, Button, Select, Space } from "antd";
import { useEffect, useState } from "react";
import { compareDatasetBenchmarks, listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetExperiment } from "../../api/types";

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

  const run = async () => {
    setError(null);
    const a = eligible.find((experiment) => experiment.id === aId);
    const b = eligible.find((experiment) => experiment.id === bId);
    if (!a || !b || a.datasetEvaluationId === null || b.datasetEvaluationId === null) return;
    try {
      // Compare the experiments' linked evaluations (backend authority).
      setResult(await compareDatasetBenchmarks(a.datasetEvaluationId, b.datasetEvaluationId));
    } catch (reason) {
      setError(toErrorText(reason));
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error !== null ? <Alert type="error" showIcon description={error} /> : null}
      <Space wrap>
        <Select aria-label="Experiment A" style={{ width: 240 }} value={aId ?? undefined} onChange={setAId} options={options} />
        <Select aria-label="Experiment B" style={{ width: 240 }} value={bId ?? undefined} onChange={setBId} options={options} />
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
    </Space>
  );
}
