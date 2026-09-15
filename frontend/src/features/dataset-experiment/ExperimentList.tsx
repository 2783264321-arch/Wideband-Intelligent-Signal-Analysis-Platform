import { Alert, Button, Empty, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperiments, PlatformApiError, retryFailedDatasetExperimentItems, runDatasetExperiment } from "../../api/client";
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
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listDatasetExperiments()
      .then((items) => { if (active) setExperiments(items); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reloadToken]);

  const trigger = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      setReloadToken((token) => token + 1);
    } catch (reason) {
      setError(toErrorText(reason));
    }
  };

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
        {
          title: "Actions",
          key: "actions",
          render: (_: unknown, record: DatasetExperiment) => {
            if (record.status === "pending") {
              return <Button size="small" onClick={() => void trigger(() => runDatasetExperiment(record.id))}>Run</Button>;
            }
            if (record.status === "completed_with_failures" && record.failedItems > 0) {
              return <Button size="small" onClick={() => void trigger(() => retryFailedDatasetExperimentItems(record.id))}>Retry Failed</Button>;
            }
            return null;
          },
        },
      ]}
    />
  );
}
