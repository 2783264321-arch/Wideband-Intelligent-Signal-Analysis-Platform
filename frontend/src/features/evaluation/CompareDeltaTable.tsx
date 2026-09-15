import { Space, Table } from "antd";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listDatasetBenchmarkItems } from "../../api/client";
import type { DatasetBenchmarkCompareResult } from "../../api/types";
import type { MessageKey } from "../../localization/types";
import { useLocalization } from "../../localization/useLocalization";

const DELTA_ROWS: Array<[string, MessageKey]> = [
  ["localization_ap50", "compare.metricLocalizationAp50"],
  ["localization_ap50_95", "compare.metricLocalizationAp50_95"],
  ["class_aware_map50", "compare.metricClassAwareMap50"],
  ["class_aware_map50_95", "compare.metricClassAwareMap50_95"],
  ["matched_accuracy", "compare.metricMatchedAccuracy"],
];

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
  const { t } = useLocalization();
  const [shared, setShared] = useState<SharedRecording | null>(null);

  const formatDelta = (value: number | null | undefined): string =>
    typeof value === "number" ? String(value) : t("metrics.notAvailable");

  useEffect(() => {
    if (!result.comparable) return undefined;
    let active = true;
    // A new result invalidates any previously resolved shared-recording drilldown.
    setShared(null);
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
        dataSource={DELTA_ROWS.map(([key, labelKey]) => ({ metric: t(labelKey), delta: formatDelta(result.deltas[key]) }))}
        columns={[
          { title: t("compare.columnMetric"), dataIndex: "metric" },
          { title: t("compare.columnDelta"), dataIndex: "delta" },
        ]}
      />
      {shared !== null ? (
        <Link to={`/algorithm-lab?recording=${shared.recordingId}&runA=${shared.runAId}&runB=${shared.runBId}`}>
          {t("compare.openRecordingInAlgorithmLab")}
        </Link>
      ) : null}
    </Space>
  );
}
