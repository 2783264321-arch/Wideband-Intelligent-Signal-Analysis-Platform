import { Empty, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperimentItems, PlatformApiError } from "../../api/client";
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
      locale={{ emptyText: <Empty description={error ?? "No items."} /> }}
      columns={[
        { title: "Order", dataIndex: "manifestOrder", width: 80 },
        { title: "Recording", dataIndex: "recordingName" },
        {
          title: "Status",
          dataIndex: "status",
          render: (status: string) => <Tag color={STATUS_COLORS[status] ?? "default"}>{status}</Tag>,
        },
        {
          title: "Error",
          dataIndex: "lastErrorType",
          render: (value: string | null) => (value !== null ? <Typography.Text code>{value}</Typography.Text> : null),
        },
        {
          title: "Run",
          key: "run",
          render: (_: unknown, record: DatasetExperimentItem) =>
            record.latestAnalysisRunId !== null
              ? <Link to={`/signals/${record.latestAnalysisRunId}`}>{record.latestAnalysisRunId}</Link>
              : null,
        },
      ]}
    />
  );
}
