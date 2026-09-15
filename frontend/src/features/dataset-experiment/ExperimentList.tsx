import { Alert, Empty, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperiments, PlatformApiError } from "../../api/client";
import type { DatasetExperiment } from "../../api/types";

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

export function ExperimentList() {
  const [experiments, setExperiments] = useState<DatasetExperiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listDatasetExperiments()
      .then((items) => { if (active) setExperiments(items); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  if (error !== null) {
    return <Alert type="error" showIcon message="Unable to load experiments" description={error} />;
  }

  return (
    <Table
      rowKey="id"
      loading={loading}
      dataSource={experiments}
      locale={{ emptyText: <Empty description="No dataset experiments yet." /> }}
      columns={[
        {
          title: "Name",
          dataIndex: "name",
          render: (_: unknown, record: DatasetExperiment) => <Link to={`/experiments/${record.id}`}>{record.name}</Link>,
        },
        {
          title: "Dataset",
          key: "dataset",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.datasetName} / {record.datasetSplit}</Typography.Text>
          ),
        },
        {
          title: "Plugin",
          key: "plugin",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.pluginId} {record.pluginVersion}</Typography.Text>
          ),
        },
        {
          title: "Executor",
          dataIndex: "executor",
          render: (executor: string) => <Typography.Text>{executor}</Typography.Text>,
        },
        {
          title: "Status",
          dataIndex: "status",
          render: (status: string) => <Tag color={STATUS_COLORS[status] ?? "default"}>{status}</Tag>,
        },
        {
          title: "Items",
          key: "items",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.completedItems} / {record.expectedItems}</Typography.Text>
          ),
        },
      ]}
    />
  );
}
