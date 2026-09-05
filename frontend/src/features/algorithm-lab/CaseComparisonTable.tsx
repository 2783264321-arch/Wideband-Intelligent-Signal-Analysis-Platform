import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AlgorithmLabCase, RunMatchState } from "../../api/types";

const comparisonTag = (state: string) => {
  const color = state === "both_detected" ? "green" : state === "both_missed" ? "red" : "orange";
  return <Tag color={color}>{state}</Tag>;
};

const matchCell = (state: RunMatchState) => {
  if (!state.matched) return <Tag color="red">Missed</Tag>;
  const iouText = state.iou === null ? "—" : state.iou.toFixed(3);
  return (
    <span>
      {iouText}<br />
      <span>{state.className ?? "?"}</span>{" "}
      {state.classCorrect === null ? <Tag>Class N/A</Tag> : state.classCorrect ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag>}
    </span>
  );
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
    title: "Run A (IoU / class)",
    dataIndex: "runA",
    key: "runA",
    render: (_, record) => matchCell(record.runA),
  },
  {
    title: "Run B (IoU / class)",
    dataIndex: "runB",
    key: "runB",
    render: (_, record) => matchCell(record.runB),
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