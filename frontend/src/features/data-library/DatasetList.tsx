import { Button, Card, Empty, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  deleteBlockersFromError,
  deleteDatasetProjection,
  listDatasetProjections,
} from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { DatasetProjectionSummary, DeleteBlocker } from "../../api/types";
import { DeleteConfirmModal } from "./DeleteConfirmModal";
import { DeleteConflictAlert } from "./DeleteConflictAlert";

export interface DatasetListProps {
  onImportResults?: (datasetProjectionId: string) => void;
}

export function DatasetList({ onImportResults }: DatasetListProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<DatasetProjectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingRemove, setPendingRemove] = useState<DatasetProjectionSummary | null>(null);
  const [removing, setRemoving] = useState(false);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listDatasetProjections();
      setItems(page.items);
    } catch (reason) {
      setError(toErrorText(reason, t("common.noData")));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const confirmRemove = async () => {
    if (!pendingRemove) return;
    setRemoving(true);
    setBlockers([]);
    try {
      await deleteDatasetProjection(pendingRemove.datasetProjectionId);
      setPendingRemove(null);
      await refresh();
    } catch (reason) {
      const found = deleteBlockersFromError(reason);
      setBlockers(found);
      if (found.length === 0) setError(toErrorText(reason, t("common.noData")));
    } finally {
      setRemoving(false);
    }
  };

  if (loading) return <Typography.Text>{t("common.loading")}</Typography.Text>;
  if (items.length === 0) return <Empty description={t("dataLibrary.empty")} />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <DeleteConflictAlert blockers={blockers} />
      {items.map((dataset) => (
        <Card
          key={dataset.datasetProjectionId}
          data-testid="dataset-card"
          title={`${dataset.datasetName} · ${dataset.datasetSplit}`}
          extra={
            <Tag color={dataset.external ? "geekblue" : "green"}>
              {dataset.external ? t("dataLibrary.external") : t("dataLibrary.local")}
            </Tag>
          }
          actions={[
            <Button
              key="browse"
              type="link"
              onClick={() => navigate(`/data-library/datasets/${dataset.datasetProjectionId}`)}
            >
              {t("dataLibrary.browseSamples")}
            </Button>,
            <Button
              key="create"
              type="link"
              onClick={() => navigate(`/experiments?datasetProjectionId=${dataset.datasetProjectionId}`)}
            >
              {t("dataLibrary.createExperiment")}
            </Button>,
            <Button key="import" type="link" onClick={() => onImportResults?.(dataset.datasetProjectionId)}>
              {t("dataLibrary.importResults")}
            </Button>,
            <Button
              key="remove"
              type="link"
              danger
              onClick={() => {
                setBlockers([]);
                setPendingRemove(dataset);
              }}
            >
              {t("dataLibrary.removeDataset")}
            </Button>,
          ]}
        >
          <Space wrap>
            <Tag>
              {t("dataLibrary.sampleCount")} {dataset.sampleCount}
            </Tag>
            <Tag>
              {t("dataLibrary.groundTruth")} {dataset.groundTruthSampleCount}
            </Tag>
            {dataset.labelSpace ? <Tag>{dataset.labelSpace}</Tag> : null}
            <Tag>
              {t("dataLibrary.source")}: {dataset.source}
            </Tag>
          </Space>
        </Card>
      ))}
      <DeleteConfirmModal
        open={pendingRemove !== null}
        title={t("dataLibrary.removeDataset")}
        body={t("dataLibrary.removeDatasetConfirm")}
        confirmLabel={t("dataLibrary.removeDataset")}
        loading={removing}
        onConfirm={() => void confirmRemove()}
        onCancel={() => {
          setPendingRemove(null);
          setBlockers([]);
        }}
      />
    </Space>
  );
}
