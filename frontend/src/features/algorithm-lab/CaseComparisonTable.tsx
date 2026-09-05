import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AlgorithmLabCase } from "../../api/types";

const comparisonTag = (state: string) => {
  const color = state === "both_detected" ? "green" : state === "both_missed" ? "red" : "orange";
  return <Tag color={color}>{state}</Tag>;
};

const matchCell = (matched: boolean, iou: number | null) => {
  if (!matched) return <Tag color="red">Missed</Tag>;
  return <span>{iou === null ? "—" : iou.toFixed(3)}</span>;
};

const columns: ColumnsType<AlgorithmLabCase> = [
  {
    title: "GT / Signal Type",
    key: "gt",
    render: (_, record) => (
      <span>{record.groundTruthId} · {record.className}</span>
    ),
  },
  {
    title: "Run A (Matched / IoU)",
    dataIndex: "runA",
    key: "runA",
    render: (_, record) => matchCell(record.runA.matched, record.runA.iou),
  },
  {
    title: "Run B (Matched / IoU)",
    dataIndex: "runB",
    key: "runB",
    render: (_, record) => matchCell(record.runB.matched, record.runB.iou),
  },
  {
    title: "Comparison",
    dataIndex: "comparison",
    key: "comparison",
    render: (value: string) => comparisonTag(value),
  },
];

interface Props {
  cases: AlgorithmLabCase[];
  onSelectCase?: (groundTruthId: string) => void;
}

export function CaseComparisonTable({ cases, onSelectCase }: Props) {
  return (
    <Table<AlgorithmLabCase>
      size="small"
      rowKey="groundTruthId"
      columns={columns}
      dataSource={cases}
      pagination={{ pageSize: 10 }}
      onRow={(record) => ({
        onClick: onSelectCase ? () => onSelectCase(record.groundTruthId) : undefined,
        style: { cursor: onSelectCase ? "pointer" : "default" },
      })}
    />
  );
}