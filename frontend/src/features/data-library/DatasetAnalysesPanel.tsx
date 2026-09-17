import { Button, Checkbox, Empty, List, Space, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listDatasetBenchmarks, listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetEvaluation, DatasetExperiment, DatasetSummary } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { DatasetEvaluationComparePanel } from "../evaluation/DatasetEvaluationComparePanel";
import { DatasetEvaluationView } from "../evaluation/DatasetEvaluationView";
import { AnalyzeDatasetModal } from "./AnalyzeDatasetModal";

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "processing",
  evaluating: "processing",
  completed: "success",
  completed_with_failures: "warning",
  failed: "error",
};

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

type PanelView =
  | { kind: "list" }
  | { kind: "evaluation"; evaluationId: string }
  | { kind: "compare"; a: DatasetEvaluation; b: DatasetEvaluation };

/** Dataset-first Dataset Analysis entry: analysis list + Dataset Evaluation workflows. */
export function DatasetAnalysesPanel({ dataset }: { dataset: DatasetSummary }) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<DatasetExperiment[]>([]);
  const [evaluations, setEvaluations] = useState<DatasetEvaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [view, setView] = useState<PanelView>({ kind: "list" });

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    // dataset_id is authoritative for both analyses and their linked evaluations.
    Promise.all([listDatasetExperiments(dataset.id), listDatasetBenchmarks(dataset.id)])
      .then(([experiments, benchmarks]) => {
        setItems(experiments);
        setEvaluations(benchmarks);
      })
      .catch((reason: unknown) => setError(toErrorText(reason)))
      .finally(() => setLoading(false));
  }, [dataset.id]);

  useEffect(() => {
    setSelected([]);
    setView({ kind: "list" });
    refresh();
  }, [refresh]);

  const evaluationById = new Map(evaluations.map((evaluation) => [evaluation.id, evaluation]));
  const linkedEvaluation = (analysis: DatasetExperiment): DatasetEvaluation | null =>
    analysis.datasetEvaluationId !== null
      ? evaluationById.get(analysis.datasetEvaluationId) ?? null
      : null;
  const isEligible = (analysis: DatasetExperiment): boolean =>
    linkedEvaluation(analysis)?.status === "completed";

  const toggle = (evaluationId: string, checked: boolean) => {
    setSelected((current) => {
      if (checked) {
        if (current.includes(evaluationId) || current.length >= 2) return current;
        return [...current, evaluationId];
      }
      return current.filter((value) => value !== evaluationId);
    });
  };

  const selectedEvaluations = selected
    .map((id) => evaluationById.get(id))
    .filter((evaluation): evaluation is DatasetEvaluation => evaluation !== undefined);
  const canCompare = selected.length === 2 && selectedEvaluations.length === 2;

  if (view.kind === "evaluation") {
    return <DatasetEvaluationView evaluationId={view.evaluationId} onBack={() => setView({ kind: "list" })} />;
  }
  if (view.kind === "compare") {
    return (
      <DatasetEvaluationComparePanel
        evaluationAId={view.a.id}
        evaluationBId={view.b.id}
        evaluationAName={view.a.name}
        evaluationBName={view.b.name}
        onBack={() => setView({ kind: "list" })}
      />
    );
  }

  const eligibleCount = items.filter(isEligible).length;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Button type="primary" data-testid="analyze-dataset-button" onClick={() => setModalOpen(true)}>
          {t("datasetAnalysis.analyzeDataset")}
        </Button>
        {eligibleCount >= 2 ? (
          <Button
            data-testid="compare-evaluations-button"
            disabled={!canCompare}
            onClick={() => setView({ kind: "compare", a: selectedEvaluations[0], b: selectedEvaluations[1] })}
          >
            {t("pipelineCompare.compareEvaluations")}
          </Button>
        ) : null}
        {eligibleCount >= 2 && !canCompare ? (
          <Typography.Text type="secondary">{t("pipelineCompare.selectTwo")}</Typography.Text>
        ) : null}
      </Space>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <List
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <Empty description={t("datasetAnalysis.noAnalyses")} /> }}
        renderItem={(analysis) => {
          const linked = linkedEvaluation(analysis);
          const eligible = linked !== null && linked.status === "completed";
          const actions: ReactNode[] = [];
          if (eligible && linked !== null) {
            actions.push(
              <Button
                key="view-evaluation"
                type="link"
                data-testid={`view-evaluation-${analysis.id}`}
                onClick={() => setView({ kind: "evaluation", evaluationId: linked.id })}
              >
                {t("datasetEvaluation.viewEvaluation")}
              </Button>,
            );
          }
          actions.push(
            <Button key="open" type="link" onClick={() => navigate(`/experiments/${analysis.id}`)}>
              {t("datasetAnalysis.openAnalysis")}
            </Button>,
          );
          return (
            <List.Item data-testid="dataset-analysis-item" actions={actions}>
              <List.Item.Meta
                title={
                  <Space size={8}>
                    {eligible && linked !== null ? (
                      <Checkbox
                        aria-label={t("datasetEvaluation.selectForCompare", { name: analysis.name })}
                        checked={selected.includes(linked.id)}
                        onChange={(event) => toggle(linked.id, event.target.checked)}
                      />
                    ) : null}
                    <span>{analysis.name}</span>
                  </Space>
                }
                description={
                  <Space wrap>
                    <Tag color={STATUS_COLORS[analysis.status] ?? "default"}>{analysis.status}</Tag>
                    <span>{analysis.pluginId}</span>
                    <span data-testid="dataset-analysis-progress">
                      {analysis.completedItems} / {analysis.expectedItems}
                    </span>
                    {analysis.failedItems > 0 ? (
                      <span>
                        {t("status.failed")} {analysis.failedItems}
                      </span>
                    ) : null}
                    {analysis.datasetEvaluationId !== null ? (
                      <Tag color="success">{t("datasetAnalysis.hasEvaluation")}</Tag>
                    ) : null}
                  </Space>
                }
              />
            </List.Item>
          );
        }}
      />
      <AnalyzeDatasetModal
        dataset={dataset}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onStarted={(experimentId) => navigate(`/experiments/${experimentId}`)}
      />
    </Space>
  );
}
