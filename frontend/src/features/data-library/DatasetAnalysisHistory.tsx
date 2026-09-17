import { Button, List, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listDatasetAnalysisHistory } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { DatasetAnalysisHistoryItem } from "../../api/types";
import { DatasetAnalysisCompareEntry } from "./DatasetAnalysisCompareEntry";

export interface DatasetAnalysisHistoryProps {
  datasetId: string;
  onImportBatchResults?: () => void;
}

export function DatasetAnalysisHistory({
  datasetId,
  onImportBatchResults,
}: DatasetAnalysisHistoryProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [items, setItems] = useState<DatasetAnalysisHistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listDatasetAnalysisHistory(datasetId)
      .then((page) => {
        if (active) setItems(page.items);
      })
      .catch((reason: unknown) => {
        if (active) setError(toErrorText(reason, t("common.noData")));
      });
    return () => {
      active = false;
    };
  }, [datasetId]);

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Button onClick={() => onImportBatchResults?.()}>{t("dataLibrary.importBatchResults")}</Button>
      </Space>
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      <List
        dataSource={items}
        locale={{ emptyText: t("dataLibrary.empty") }}
        renderItem={(item) => (
          <List.Item
            data-testid="analysis-history-item"
            actions={
              item.kind === "evaluation"
                ? [
                    <Button
                      key="open"
                      type="link"
                      onClick={() => navigate(`/experiments?tab=benchmarks&benchmark=${item.resourceId}`)}
                    >
                      {t("dataLibrary.openEvaluation")}
                    </Button>,
                  ]
                : undefined
            }
          >
            <List.Item.Meta
              title={`${item.name} · ${item.pipelineId} ${item.pipelineVersion}`}
              description={
                <Space wrap>
                  <Tag>{item.kind === "imported_batch" ? t("dataLibrary.importedBatch") : item.kind}</Tag>
                  <Tag>{item.status}</Tag>
                  {item.executor ? <Tag>{item.executor}</Tag> : null}
                  <span>
                    {item.completedItems} / {item.expectedItems}
                  </span>
                  {item.coverage !== null ? (
                    <span>
                      {t("dataLibrary.coverage")} {(item.coverage * 100).toFixed(1)}%
                    </span>
                  ) : null}
                  {item.createdAt ? <span>{item.createdAt}</span> : null}
                </Space>
              }
            />
          </List.Item>
        )}
      />
      <DatasetAnalysisCompareEntry items={items} />
    </Space>
  );
}
