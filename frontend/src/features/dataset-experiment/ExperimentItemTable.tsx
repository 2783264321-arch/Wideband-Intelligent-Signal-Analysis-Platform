import { Empty, Space, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperimentItems, PlatformApiError } from "../../api/client";
import { useLocalization } from "../../localization/useLocalization";
import { itemStatusKey } from "../analysis-run/statusModel";
import type { DatasetExperimentItem } from "../../api/types";

const STATUS_COLORS: Record<string, string> = {
  queued: "default",
  running: "processing",
  completed: "success",
  failed: "error",
};

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export function ExperimentItemTable({ experimentId }: { experimentId: string }) {
  const { t } = useLocalization();
  const [items, setItems] = useState<DatasetExperimentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listDatasetExperimentItems(experimentId)
      .then((next) => { if (active) setItems(next); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [experimentId]);

  return (
    <Table
      rowKey="id"
      loading={loading}
      dataSource={items}
      locale={{ emptyText: <Empty description={error ?? t("items.empty")} /> }}
      columns={[
        { title: t("items.columnOrder"), dataIndex: "manifestOrder", width: 80 },
        { title: t("items.columnRecording"), dataIndex: "recordingName" },
        {
          title: t("items.columnStatus"),
          dataIndex: "status",
          render: (status: string) => {
            const key = itemStatusKey(status);
            return <Tag color={STATUS_COLORS[status] ?? "default"}>{key !== null ? t(key) : status}</Tag>;
          },
        },
        {
          title: t("items.columnError"),
          dataIndex: "lastErrorType",
          render: (value: string | null) => (value !== null ? <Typography.Text code>{value}</Typography.Text> : null),
        },
        {
          title: "",
          key: "actions",
          render: (_: unknown, record: DatasetExperimentItem) => (
            <Space>
              {record.latestAnalysisRunId !== null && record.status === "completed" ? (
                <Link
                  data-testid="item-view-result"
                  to={`/spectrum/${record.recordingId}?run=${record.latestAnalysisRunId}`}
                >
                  {t("datasetAnalysis.viewResult")}
                </Link>
              ) : null}
              <Link data-testid="item-open-sample" to={`/samples/${record.recordingId}`}>
                {t("datasetAnalysis.openSample")}
              </Link>
            </Space>
          ),
        },
      ]}
    />
  );
}
