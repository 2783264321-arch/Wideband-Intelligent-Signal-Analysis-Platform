import { Empty, Select, Space, Table, Typography } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetExperimentItemAttempts, listDatasetExperimentItems, PlatformApiError } from "../../api/client";
import type { DatasetExperimentAttempt, DatasetExperimentItem } from "../../api/types";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/**
 * Attempts for one item. Attempts always carry a concrete `analysisRunId`
 * (mandatory in the backend read model), so launch ambiguity is never inferred
 * here from a missing run id.
 */
export function AttemptTimeline({ experimentId, itemId }: { experimentId: string; itemId: string }) {
  const [attempts, setAttempts] = useState<DatasetExperimentAttempt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listDatasetExperimentItemAttempts(experimentId, itemId)
      .then((next) => { if (active) setAttempts(next); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [experimentId, itemId]);

  return (
    <Table
      rowKey="id"
      loading={loading}
      dataSource={attempts}
      locale={{ emptyText: <Empty description={error ?? "No attempts."} /> }}
      columns={[
        { title: "Attempt", dataIndex: "attemptNumber", width: 90 },
        {
          title: "Run",
          key: "run",
          render: (_: unknown, record: DatasetExperimentAttempt) => (
            <Link to={`/signals/${record.analysisRunId}`}>{record.analysisRunId}</Link>
          ),
        },
        {
          title: "Launch requested",
          dataIndex: "launchRequestedAt",
          render: (value: string | null) => <Typography.Text>{value ?? "—"}</Typography.Text>,
        },
      ]}
    />
  );
}

/** Items selector + attempt timeline for the selected item. */
export function ExperimentAttemptsTab({ experimentId }: { experimentId: string }) {
  const [items, setItems] = useState<DatasetExperimentItem[]>([]);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listDatasetExperimentItems(experimentId)
      .then((next) => {
        if (!active) return;
        setItems(next);
        setSelectedItemId(next.length > 0 ? next[0].id : null);
      })
      .catch(() => { if (active) setItems([]); });
    return () => { active = false; };
  }, [experimentId]);

  if (items.length === 0) {
    return <Empty description="No items." />;
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Select
        aria-label="Item"
        style={{ width: 320 }}
        value={selectedItemId ?? undefined}
        onChange={setSelectedItemId}
        options={items.map((item) => ({ value: item.id, label: item.recordingName }))}
      />
      {selectedItemId !== null ? <AttemptTimeline experimentId={experimentId} itemId={selectedItemId} /> : null}
    </Space>
  );
}
