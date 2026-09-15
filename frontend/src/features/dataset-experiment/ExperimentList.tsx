import { Alert, Button, Empty, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperiments, PlatformApiError, retryFailedDatasetExperimentItems, runDatasetExperiment } from "../../api/client";
import { useLocalization } from "../../localization/useLocalization";
import type { DatasetExperiment } from "../../api/types";

const COLUMN_KEYS = {
  name: "experiment.columnName",
  dataset: "experiment.columnDataset",
  plugin: "experiment.columnPlugin",
  executor: "experiment.columnExecutor",
  status: "experiment.columnStatus",
  items: "experiment.columnItems",
  actions: "experiment.columnActions",
} as const;

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
  const { t } = useLocalization();
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
    return <Alert type="error" showIcon message={t("experiment.loadError")} description={error} />;
  }

  return (
    <Table
      rowKey="id"
      loading={loading}
      dataSource={experiments}
      locale={{ emptyText: <Empty description={t("experiment.empty")} /> }}
      columns={[
        {
          title: t(COLUMN_KEYS.name),
          dataIndex: "name",
          render: (_: unknown, record: DatasetExperiment) => <Link to={`/experiments/${record.id}`}>{record.name}</Link>,
        },
        {
          title: t(COLUMN_KEYS.dataset),
          key: "dataset",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.datasetName} / {record.datasetSplit}</Typography.Text>
          ),
        },
        {
          title: t(COLUMN_KEYS.plugin),
          key: "plugin",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.pluginId} {record.pluginVersion}</Typography.Text>
          ),
        },
        {
          title: t(COLUMN_KEYS.executor),
          dataIndex: "executor",
          render: (executor: string) => <Typography.Text>{executor}</Typography.Text>,
        },
        {
          title: t(COLUMN_KEYS.status),
          dataIndex: "status",
          render: (status: string) => <Tag color={STATUS_COLORS[status] ?? "default"}>{status}</Tag>,
        },
        {
          title: t(COLUMN_KEYS.items),
          key: "items",
          render: (_: unknown, record: DatasetExperiment) => (
            <Typography.Text>{record.completedItems} / {record.expectedItems}</Typography.Text>
          ),
        },
        {
          title: t(COLUMN_KEYS.actions),
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
