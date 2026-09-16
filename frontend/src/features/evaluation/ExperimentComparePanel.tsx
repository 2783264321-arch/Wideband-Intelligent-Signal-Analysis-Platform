import { Alert, Button, Select, Space } from "antd";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { compareDatasetBenchmarks, listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetExperiment } from "../../api/types";
import { CompareDeltaTable } from "./CompareDeltaTable";
import { useLocalization } from "../../localization/useLocalization";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export function ExperimentComparePanel() {
  const { t } = useLocalization();
  const [searchParams] = useSearchParams();
  const [experiments, setExperiments] = useState<DatasetExperiment[]>([]);
  const [aId, setAId] = useState<string | null>(null);
  const [bId, setBId] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetBenchmarkCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Only the request that owns the current generation may set result/error.
  // Selector changes invalidate outstanding work synchronously.
  const compareGenerationRef = useRef(0);
  const autoRanRef = useRef(false);

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

  const compareEvaluations = async (evaluationAId: string, evaluationBId: string) => {
    const generation = ++compareGenerationRef.current;
    setError(null);
    try {
      const next = await compareDatasetBenchmarks(evaluationAId, evaluationBId);
      if (generation !== compareGenerationRef.current) return;
      setResult(next);
    } catch (reason) {
      if (generation !== compareGenerationRef.current) return;
      setError(toErrorText(reason));
    }
  };

  // URL-preselected comparison: ?a=<evaluationAId>&b=<evaluationBId>.
  const preselectA = searchParams.get("a");
  const preselectB = searchParams.get("b");
  useEffect(() => {
    if (autoRanRef.current || !preselectA || !preselectB || experiments.length === 0) return;
    const a = eligible.find((experiment) => experiment.datasetEvaluationId === preselectA);
    const b = eligible.find((experiment) => experiment.datasetEvaluationId === preselectB);
    if (!a || !b || a.id === b.id || a.datasetEvaluationId === null || b.datasetEvaluationId === null) return;
    autoRanRef.current = true;
    setAId(a.id);
    setBId(b.id);
    void compareEvaluations(a.datasetEvaluationId, b.datasetEvaluationId);
  }, [preselectA, preselectB, experiments]);

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
        <Alert type="info" showIcon message={t("compare.empty")} />
      ) : null}
      <Space wrap>
        <Select aria-label={t("compare.selectA")} style={{ width: 240 }} value={aId ?? undefined} onChange={changeA} options={options} />
        <Select aria-label={t("compare.selectB")} style={{ width: 240 }} value={bId ?? undefined} onChange={changeB} options={options} />
        <Button
          type="primary"
          disabled={aId === null || bId === null || aId === bId}
          onClick={() => void run()}
        >
          {t("common.compare")}
        </Button>
      </Space>
      {result !== null && !result.comparable ? (
        <Alert
          type="warning"
          showIcon
          message={t("compare.notComparable")}
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
