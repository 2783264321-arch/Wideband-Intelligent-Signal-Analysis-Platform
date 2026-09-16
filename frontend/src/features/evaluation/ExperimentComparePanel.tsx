import { Alert, Button, Select, Space } from "antd";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { compareDatasetBenchmarks, listDatasetBenchmarks, PlatformApiError } from "../../api/client";
import type { DatasetBenchmarkCompareResult, DatasetEvaluation } from "../../api/types";
import { CompareDeltaTable } from "./CompareDeltaTable";
import { useLocalization } from "../../localization/useLocalization";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/**
 * Dataset comparison surface.
 *
 * Comparison identity is the DatasetEvaluation itself, because
 * `compareDatasetBenchmarks(evaluationAId, evaluationBId)` accepts evaluation
 * IDs directly. Evaluations may come from imported (BAPv1) batches and need no
 * linked DatasetExperiment. Backend remains the authority for coverage,
 * manifest, label-space and protocol compatibility.
 */
export function ExperimentComparePanel() {
  const { t } = useLocalization();
  const [searchParams] = useSearchParams();
  const [evaluations, setEvaluations] = useState<DatasetEvaluation[]>([]);
  const [aId, setAId] = useState<string | null>(null);
  const [bId, setBId] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetBenchmarkCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Only the request that owns the current generation may set result/error.
  const compareGenerationRef = useRef(0);
  // Identity of the URL pair already auto-hydrated (business state is URL-authoritative).
  const lastAutoPairRef = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    listDatasetBenchmarks()
      .then((items) => { if (active) setEvaluations(items); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, []);

  const eligible = evaluations.filter((evaluation) => evaluation.status === "completed");
  const options = eligible.map((evaluation) => ({ value: evaluation.id, label: evaluation.name }));

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

  const run = () => {
    if (aId === null || bId === null || aId === bId) return;
    if (!eligible.some((evaluation) => evaluation.id === aId)) return;
    if (!eligible.some((evaluation) => evaluation.id === bId)) return;
    void compareEvaluations(aId, bId);
  };

  // URL-preselected comparison: ?a=<evaluationAId>&b=<evaluationBId>.
  const preselectA = searchParams.get("a");
  const preselectB = searchParams.get("b");
  useEffect(() => {
    if (!preselectA || !preselectB || evaluations.length === 0) return;
    const pairKey = `${preselectA}\u0000${preselectB}`;
    if (lastAutoPairRef.current === pairKey) return;
    const a = eligible.find((evaluation) => evaluation.id === preselectA);
    const b = eligible.find((evaluation) => evaluation.id === preselectB);
    if (!a || !b || a.id === b.id) return;
    lastAutoPairRef.current = pairKey;
    setResult(null);
    setError(null);
    setAId(a.id);
    setBId(b.id);
    void compareEvaluations(a.id, b.id);
  }, [preselectA, preselectB, evaluations]);

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
