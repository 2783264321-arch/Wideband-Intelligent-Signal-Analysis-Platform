import { Space, Table } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetBenchmarkItems } from "../../api/client";
import type { DatasetBenchmarkCompareResult } from "../../api/types";

const DELTA_ROWS: Array<[string, string]> = [
  ["localization_ap50", "Localization AP50"],
  ["localization_ap50_95", "Localization AP50:95"],
  ["class_aware_map50", "Class-aware mAP50"],
  ["class_aware_map50_95", "Class-aware mAP50:95"],
  ["matched_accuracy", "Matched accuracy"],
];

function formatDelta(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "N/A";
}

interface SharedRecording {
  recordingId: string;
  runAId: string;
  runBId: string;
}

/**
 * Aggregate deltas (A vs B) plus a shared-recording drilldown into Algorithm Lab.
 * Null deltas render `N/A` (never 0); run ids come only from backend evaluation
 * items (never invented).
 */
export function CompareDeltaTable({ result }: { result: DatasetBenchmarkCompareResult }) {
  const [shared, setShared] = useState<SharedRecording | null>(null);

  useEffect(() => {
    if (!result.comparable) return undefined;
    let active = true;
    Promise.all([
      listDatasetBenchmarkItems(result.evaluationAId),
      listDatasetBenchmarkItems(result.evaluationBId),
    ])
      .then(([a, b]) => {
        if (!active) return;
        const bByRecording = new Map(b.map((item) => [item.recordingId, item]));
        for (const item of a) {
          const other = bByRecording.get(item.recordingId);
          if (item.analysisRunId !== null && other !== undefined && other.analysisRunId !== null) {
            setShared({ recordingId: item.recordingId, runAId: item.analysisRunId, runBId: other.analysisRunId });
            break;
          }
        }
      })
      .catch(() => { /* drilldown is best-effort */ });
    return () => { active = false; };
  }, [result]);

  return (
    <Space direction="vertical" style={{ width: "100%" }} data-testid="compare-delta-table">
      <Table
        rowKey="metric"
        pagination={false}
        dataSource={DELTA_ROWS.map(([key, label]) => ({ metric: label, delta: formatDelta(result.deltas[key]) }))}
        columns={[
          { title: "Metric", dataIndex: "metric" },
          { title: "Delta", dataIndex: "delta" },
        ]}
      />
      {shared !== null ? (
        <Link to={`/algorithm-lab?recording=${shared.recordingId}&runA=${shared.runAId}&runB=${shared.runBId}`}>
          Open recording in Algorithm Lab
        </Link>
      ) : null}
    </Space>
  );
}
