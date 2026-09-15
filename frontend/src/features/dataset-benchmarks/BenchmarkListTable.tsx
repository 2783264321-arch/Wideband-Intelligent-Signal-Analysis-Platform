import { Button, Table } from "antd";
import type { DatasetEvaluation, DatasetEvaluationStatus } from "../../api/types";
import type { MessageKey } from "../../localization/types";
import { useLocalization } from "../../localization/useLocalization";
import { evaluationStatusKey } from "../analysis-run/statusModel";

const ACTION_KEYS: Record<string, MessageKey> = {
  pending: "benchmarks.actionRun",
  running: "benchmarks.actionViewProgress",
  completed: "benchmarks.actionOpen",
};

function actionLabel(status: DatasetEvaluationStatus): MessageKey {
  return ACTION_KEYS[status] ?? "benchmarks.actionRetry";
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
  const { t } = useLocalization();
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
        { title: t("experiment.columnName"), dataIndex: "name" },
        { title: t("form.pipeline"), render: (_, row) => `${row.pipelineId} · ${row.pipelineVersion}` },
        { title: t("experiment.columnDataset"), render: (_, row) => `${row.datasetName} / ${row.datasetSplit}` },
        { title: t("benchmarks.columnProtocol"), dataIndex: "evaluationProtocol" },
        { title: t("benchmarks.columnCoverage"), render: (_, row) => `${row.evaluatedRecordings}/${row.expectedRecordings}` },
        {
          title: t("experiment.columnStatus"),
          dataIndex: "status",
          render: (status: string) => {
            const key = evaluationStatusKey(status);
            return key !== null ? t(key) : status;
          },
        },
        {
          title: "mAP50:95",
          render: (_, row) => {
            if (row.status !== "completed") return "—";
            if (!row.aggregateMetrics?.classAware) return t("metrics.notAvailable");
            return row.aggregateMetrics.classAware.map50_95?.toFixed(4) ?? t("metrics.notAvailable");
          },
        },
        {
          title: t("benchmarks.columnAction"),
          render: (_, row) => (
            <Button onClick={() => {
              if (row.status === "pending") props.onRun(row.id);
              else if (row.status === "failed" || row.status === "interrupted") props.onRetry(row.id);
              else props.onOpen(row.id);
            }}>
              {t(actionLabel(row.status))}
            </Button>
          ),
        },
      ]}
    />
  );
}
