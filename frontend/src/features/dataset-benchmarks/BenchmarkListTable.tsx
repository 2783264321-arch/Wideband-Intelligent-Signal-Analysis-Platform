import { Button, Table } from "antd";
import type { DatasetEvaluation, DatasetEvaluationStatus } from "../../api/types";

function actionLabel(status: DatasetEvaluationStatus): string {
  if (status === "pending") return "Run";
  if (status === "running") return "View Progress";
  if (status === "completed") return "Open";
  return "Retry";
}

export interface BenchmarkListTableProps {
  items: DatasetEvaluation[];
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onRun: (id: string) => void;
  onRetry: (id: string) => void;
}

export function BenchmarkListTable(props: BenchmarkListTableProps) {
  return (
    <Table
      rowKey="id"
      dataSource={props.items}
      pagination={{ pageSize: 20 }}
      rowSelection={{
        selectedRowKeys: props.selectedIds,
        onChange: (keys) => props.onSelectedIdsChange(keys.slice(-2).map(String)),
        getCheckboxProps: (row) => ({ disabled: row.status !== "completed" }),
      }}
      columns={[
        { title: "Name", dataIndex: "name" },
        { title: "Pipeline", render: (_, row) => `${row.pipelineId} · ${row.pipelineVersion}` },
        { title: "Dataset", render: (_, row) => `${row.datasetName} / ${row.datasetSplit}` },
        { title: "Protocol", dataIndex: "evaluationProtocol" },
        { title: "Coverage", render: (_, row) => `${row.evaluatedRecordings}/${row.expectedRecordings}` },
        { title: "Status", dataIndex: "status" },
        {
          title: "mAP50:95",
          render: (_, row) => {
            if (row.status !== "completed") return "—";
            if (!row.aggregateMetrics?.classAware) return "N/A";
            return row.aggregateMetrics.classAware.map50_95?.toFixed(4) ?? "N/A";
          },
        },
        {
          title: "Action",
          render: (_, row) => (
            <Button onClick={() => {
              if (row.status === "pending") props.onRun(row.id);
              else if (row.status === "failed" || row.status === "interrupted") props.onRetry(row.id);
              else props.onOpen(row.id);
            }}>
              {actionLabel(row.status)}
            </Button>
          ),
        },
      ]}
    />
  );
}