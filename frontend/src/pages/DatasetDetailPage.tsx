import { Button, Descriptions, Space, Tabs, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  deleteBlockersFromError,
  deleteDatasetProjection,
  getDatasetProjection,
} from "../api/client";
import { useLocalization } from "../localization/useLocalization";
import type { DatasetProjectionSummary, DeleteBlocker } from "../api/types";
import { BatchImportModal } from "../features/imports/BatchImportModal";
import { DatasetAnalysisHistory } from "../features/data-library/DatasetAnalysisHistory";
import { DatasetSamplesTable } from "../features/data-library/DatasetSamplesTable";
import { DeleteConfirmModal } from "../features/data-library/DeleteConfirmModal";
import { DeleteConflictAlert } from "../features/data-library/DeleteConflictAlert";

export function DatasetDetailPage() {
  const { datasetProjectionId = "" } = useParams();
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [dataset, setDataset] = useState<DatasetProjectionSummary | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [blockers, setBlockers] = useState<DeleteBlocker[]>([]);

  useEffect(() => {
    let active = true;
    getDatasetProjection(datasetProjectionId)
      .then((value) => {
        if (active) setDataset(value);
      })
      .catch(() => {
        if (active) setDataset(null);
      });
    return () => {
      active = false;
    };
  }, [datasetProjectionId]);

  const remove = async () => {
    setRemoving(true);
    setBlockers([]);
    try {
      await deleteDatasetProjection(datasetProjectionId);
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
          {dataset ? `${dataset.datasetName} · ${dataset.datasetSplit}` : t("dataLibrary.title")}
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
            label: t("dataLibrary.overview"),
            children: dataset ? (
              <Descriptions
                column={2}
                data-testid="dataset-overview"
                items={[
                  { key: "name", label: t("dataLibrary.title"), children: dataset.datasetName },
                  { key: "split", label: t("dataLibrary.tabDatasets"), children: dataset.datasetSplit },
                  { key: "count", label: t("dataLibrary.sampleCount"), children: dataset.sampleCount },
                  {
                    key: "gt",
                    label: t("dataLibrary.groundTruth"),
                    children: dataset.groundTruthSampleCount,
                  },
                  { key: "source", label: t("dataLibrary.source"), children: dataset.source },
                  {
                    key: "scope",
                    label: t("dataLibrary.external"),
                    children: dataset.external ? t("dataLibrary.external") : t("dataLibrary.local"),
                  },
                  ...(dataset.labelSpace
                    ? [{ key: "label", label: t("form.labelSpace"), children: dataset.labelSpace }]
                    : []),
                ]}
              />
            ) : (
              <Typography.Text>{t("common.loading")}</Typography.Text>
            ),
          },
          {
            key: "samples",
            label: t("dataLibrary.samples"),
            children: <DatasetSamplesTable datasetProjectionId={datasetProjectionId} />,
          },
          {
            key: "history",
            label: t("dataLibrary.analysisHistory"),
            children: (
              <DatasetAnalysisHistory
                datasetProjectionId={datasetProjectionId}
                onCreateExperiment={() =>
                  navigate(`/experiments?datasetProjectionId=${datasetProjectionId}`)
                }
                onImportBatchResults={() => setBatchOpen(true)}
              />
            ),
          },
        ]}
      />

      <BatchImportModal open={batchOpen} onClose={() => setBatchOpen(false)} />
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
