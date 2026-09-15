import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { AlgorithmLabCase, RunMatchState } from "../../api/types";
import type { MessageKey } from "../../localization/types";
import { useLocalization } from "../../localization/useLocalization";
import { comparisonStateKey } from "../analysis-run/statusModel";

function comparisonColor(state: string): string {
  return state === "both_detected" ? "green" : state === "both_missed" ? "red" : "orange";
}

interface Props {
  cases: AlgorithmLabCase[];
  onSelectCase?: (groundTruthId: string) => void;
}

export function CaseComparisonTable({ cases, onSelectCase }: Props) {
  const { t } = useLocalization();

  const comparisonTag = (state: string) => {
    const key: MessageKey | null = comparisonStateKey(state);
    return <Tag color={comparisonColor(state)}>{key !== null ? t(key) : state}</Tag>;
  };

  const matchCell = (state: RunMatchState) => {
    if (!state.matched) return <Tag color="red">{t("caseAnalysis.missed")}</Tag>;
    const iouText = state.iou === null ? "—" : state.iou.toFixed(3);
    return (
      <span>
        {iouText}<br />
        <span>{state.className ?? "?"}</span>{" "}
        {state.classCorrect === null ? <Tag>{t("caseAnalysis.classNa")}</Tag> : state.classCorrect ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag>}
      </span>
    );
  };

  const columns: ColumnsType<AlgorithmLabCase> = [
    {
      title: t("caseAnalysis.columnGtType"),
      key: "gt",
      render: (_, record) => (
        <span>{record.groundTruthId} · {record.className}</span>
      ),
    },
    {
      title: t("caseAnalysis.columnRunA"),
      dataIndex: "runA",
      key: "runA",
      render: (_, record) => matchCell(record.runA),
    },
    {
      title: t("caseAnalysis.columnRunB"),
      dataIndex: "runB",
      key: "runB",
      render: (_, record) => matchCell(record.runB),
    },
    {
      title: t("caseAnalysis.columnComparison"),
      dataIndex: "comparison",
      key: "comparison",
      render: (value: string) => comparisonTag(value),
    },
  ];

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
