import { Descriptions, Space, Tabs, Typography } from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getDatasetProjection } from "../api/client";
import { useLocalization } from "../localization/useLocalization";
import type { DatasetProjectionSummary } from "../api/types";
import { DatasetAnalysisHistory } from "../features/data-library/DatasetAnalysisHistory";
import { DatasetSamplesTable } from "../features/data-library/DatasetSamplesTable";

export function DatasetDetailPage() {
  const { datasetProjectionId = "" } = useParams();
  const { t } = useLocalization();
  const [dataset, setDataset] = useState<DatasetProjectionSummary | null>(null);

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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ marginBottom: 0 }}>
        {dataset ? `${dataset.datasetName} · ${dataset.datasetSplit}` : t("dataLibrary.title")}
      </Typography.Title>
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
            children: <DatasetAnalysisHistory datasetProjectionId={datasetProjectionId} />,
          },
        ]}
      />
    </Space>
  );
}
