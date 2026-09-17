import { Button, Radio, Space, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { DatasetBenchmarkCompareResult, DatasetRecordingComparison } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import type { MessageKey } from "../../localization/types";

type ComparisonFilter = DatasetRecordingComparison | "all";

const STATE_LABEL: Record<DatasetRecordingComparison, MessageKey> = {
  both_detected: "sampleDifferences.filterBothDetected",
  a_only: "sampleDifferences.filterAOnly",
  b_only: "sampleDifferences.filterBOnly",
  both_missed: "sampleDifferences.filterBothMissed",
};

const STATE_COLOR: Record<DatasetRecordingComparison, string> = {
  both_detected: "success",
  a_only: "processing",
  b_only: "gold",
  both_missed: "error",
};

export interface SampleDifferencesTableProps {
  result: DatasetBenchmarkCompareResult;
}

/**
 * Sample-level difference table for two compared evaluations.
 *
 * Detection state per pipeline is derived purely from the backend
 * `comparison` value; run ids are backend-provided and never invented. When a
 * run id is null the corresponding drill-down action is disabled.
 */
export function SampleDifferencesTable({ result }: SampleDifferencesTableProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<ComparisonFilter>("all");

  const rows = result.recordings.filter((row) => filter === "all" || row.comparison === filter);
  const aDetected = (row: DatasetBenchmarkCompareResult["recordings"][number]) =>
    row.comparison === "both_detected" || row.comparison === "a_only";
  const bDetected = (row: DatasetBenchmarkCompareResult["recordings"][number]) =>
    row.comparison === "both_detected" || row.comparison === "b_only";

  const resultCell = (detected: boolean, runId: string | null) => (
    <Space direction="vertical" size={0}>
      <Typography.Text>{detected ? t("sampleDifferences.detected") : t("sampleDifferences.missed")}</Typography.Text>
      {runId !== null ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{runId}</Typography.Text>
      ) : null}
    </Space>
  );

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }} data-testid="sample-differences">
      <Typography.Title level={5} style={{ margin: 0 }}>{t("sampleDifferences.title")}</Typography.Title>
      <Radio.Group
        value={filter}
        onChange={(event) => setFilter(event.target.value as ComparisonFilter)}
        aria-label={t("sampleDifferences.title")}
      >
        <Radio.Button value="all">{t("sampleDifferences.filterAll")}</Radio.Button>
        <Radio.Button value="both_detected">{t("sampleDifferences.filterBothDetected")}</Radio.Button>
        <Radio.Button value="a_only">{t("sampleDifferences.filterAOnly")}</Radio.Button>
        <Radio.Button value="b_only">{t("sampleDifferences.filterBOnly")}</Radio.Button>
        <Radio.Button value="both_missed">{t("sampleDifferences.filterBothMissed")}</Radio.Button>
      </Radio.Group>
      <Table
        rowKey={(row) => row.recordingId}
        pagination={false}
        size="small"
        dataSource={rows}
        locale={{ emptyText: t("sampleDifferences.empty") }}
        columns={[
          {
            title: t("sampleDifferences.columnSample"),
            render: (_: unknown, row) => (
              <span data-testid={`sample-difference-row-${row.recordingId}`}>{row.recordingName}</span>
            ),
          },
          {
            title: t("sampleDifferences.columnComparison"),
            render: (_: unknown, row) => (
              <Tag data-testid={`sample-difference-state-${row.recordingId}`} color={STATE_COLOR[row.comparison]}>
                {t(STATE_LABEL[row.comparison])}
              </Tag>
            ),
          },
          {
            title: t("sampleDifferences.columnResultA"),
            render: (_: unknown, row) => resultCell(aDetected(row), row.evaluationARunId),
          },
          {
            title: t("sampleDifferences.columnResultB"),
            render: (_: unknown, row) => resultCell(bDetected(row), row.evaluationBRunId),
          },
          {
            title: "",
            render: (_: unknown, row) => (
              <Space size={4} wrap data-testid={`sample-difference-actions-${row.recordingId}`}>
                <Button
                  size="small"
                  data-testid={`open-sample-${row.recordingId}`}
                  onClick={() => navigate(`/samples/${encodeURIComponent(row.recordingId)}`)}
                >
                  {t("sampleDifferences.openSample")}
                </Button>
                <Button
                  size="small"
                  data-testid={`view-a-${row.recordingId}`}
                  disabled={row.evaluationARunId === null}
                  onClick={() => {
                    if (row.evaluationARunId !== null) {
                      navigate(`/spectrum/${encodeURIComponent(row.recordingId)}?run=${encodeURIComponent(row.evaluationARunId)}`);
                    }
                  }}
                >
                  {t("sampleDifferences.viewA")}
                </Button>
                <Button
                  size="small"
                  data-testid={`view-b-${row.recordingId}`}
                  disabled={row.evaluationBRunId === null}
                  onClick={() => {
                    if (row.evaluationBRunId !== null) {
                      navigate(`/spectrum/${encodeURIComponent(row.recordingId)}?run=${encodeURIComponent(row.evaluationBRunId)}`);
                    }
                  }}
                >
                  {t("sampleDifferences.viewB")}
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </Space>
  );
}
