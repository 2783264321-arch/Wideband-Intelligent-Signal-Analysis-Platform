import { Button, Empty, List, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetExperiment, DatasetSummary } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
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

/** Dataset-first Dataset Analysis entry: one obvious action + prior analyses. */
export function DatasetAnalysesPanel({ dataset }: { dataset: DatasetSummary }) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<DatasetExperiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const refresh = () => {
    setLoading(true);
    setError(null);
    listDatasetExperiments(dataset.id)
      .then(setItems)
      .catch((reason: unknown) => setError(toErrorText(reason)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset.id]);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space>
        <Button type="primary" data-testid="analyze-dataset-button" onClick={() => setModalOpen(true)}>
          {t("datasetAnalysis.analyzeDataset")}
        </Button>
      </Space>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <List
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <Empty description={t("datasetAnalysis.noAnalyses")} /> }}
        renderItem={(analysis) => (
          <List.Item
            data-testid="dataset-analysis-item"
            actions={[
              <Button key="open" type="link" onClick={() => navigate(`/experiments/${analysis.id}`)}>
                {t("datasetAnalysis.openAnalysis")}
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={analysis.name}
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
        )}
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
