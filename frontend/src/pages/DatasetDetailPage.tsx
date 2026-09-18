import { Button, Descriptions, Space, Tabs, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { deleteBlockersFromError, deleteDataset, getDataset } from "../api/client";
import { useLocalization } from "../localization/useLocalization";
import type { DatasetSummary, DeleteBlocker } from "../api/types";
import { DatasetAnalysesPanel } from "../features/data-library/DatasetAnalysesPanel";
import { DatasetSamplesTable } from "../features/data-library/DatasetSamplesTable";
import { DeleteConfirmModal } from "../features/data-library/DeleteConfirmModal";
import { DeleteConflictAlert } from "../features/data-library/DeleteConflictAlert";

export function DatasetDetailPage() {
  const { datasetId = "" } = useParams();
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [dataset, setDataset] = useState<DatasetSummary | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);

  useEffect(() => {
    let active = true;
    getDataset(datasetId)
      .then((value) => {
        if (active) setDataset(value);
      })
      .catch(() => {
        if (active) setDataset(null);
      });
    return () => {
      active = false;
    };
  }, [datasetId]);

  const remove = async () => {
    setRemoving(true);
    setBlockers([]);
    try {
      await deleteDataset(datasetId);
      navigate("/data-library");
    } catch (reason) {
      setBlockers(deleteBlockersFromError(reason));
      setConfirmOpen(false);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <Typography.Title level={3} style={{ marginBottom: 0 }}>
          {dataset ? `${dataset.name} · ${dataset.split}` : t("dataLibrary.title")}
        </Typography.Title>
        <Button
          danger
          onClick={() => {
            setBlockers([]);
            setConfirmOpen(true);
          }}
        >
          {t("dataLibrary.removeDataset")}
        </Button>
      </div>

      <DeleteConflictAlert blockers={blockers} />

      <Tabs
        items={[
          {
            key: "overview",
            label: t("dataLibrary.overviewAndAnalysis"),
            children: dataset ? (
              <Space direction="vertical" size="large" style={{ width: "100%" }}>
                <Descriptions
                  column={2}
                  data-testid="dataset-overview"
                  items={[
                    { key: "name", label: t("dataLibrary.name"), children: dataset.name },
                    { key: "split", label: t("dataLibrary.split"), children: dataset.split },
                    { key: "count", label: t("dataLibrary.sampleCount"), children: dataset.sampleCount },
                    {
                      key: "gt",
                      label: t("dataLibrary.groundTruth"),
                      children: dataset.groundTruthSampleCount,
                    },
                    {
                      key: "label",
                      label: t("form.labelSpace"),
                      children: dataset.labelSpace ?? "—",
                    },
                    { key: "adapter", label: t("dataLibrary.adapter"), children: dataset.adapterId },
                    {
                      key: "root",
                      label: t("dataLibrary.localPath"),
                      children: dataset.localRoot,
                    },
                  ]}
                />
                <DatasetAnalysesPanel dataset={dataset} />
              </Space>
            ) : (
              <Typography.Text>{t("common.loading")}</Typography.Text>
            ),
          },
          {
            key: "samples",
            label: t("dataLibrary.samples"),
            children: <DatasetSamplesTable datasetId={datasetId} />,
          },
        ]}
      />

      <DeleteConfirmModal
        open={confirmOpen}
        title={t("dataLibrary.removeDataset")}
        body={t("dataLibrary.removeDatasetConfirm")}
        confirmLabel={t("dataLibrary.removeDataset")}
        loading={removing}
        onConfirm={() => void remove()}
        onCancel={() => setConfirmOpen(false)}
      />
    </Space>
  );
}
