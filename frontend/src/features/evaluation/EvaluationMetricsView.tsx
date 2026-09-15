import { Descriptions, Table, Typography } from "antd";
import type { DatasetEvaluation } from "../../api/types";

function formatMetric(value: unknown): string {
  return typeof value === "number" ? String(value) : "N/A";
}

/**
 * Evaluation metrics for a DatasetEvaluation.
 *
 * Classification applicability comes ONLY from
 * `aggregateMetrics.classificationApplicable` / `.classificationReason`
 * (never top-level fields). Null metrics render `N/A`, never 0. A null
 * `aggregateMetrics` renders an explicit unavailable state.
 */
export function EvaluationMetricsView({ evaluation }: { evaluation: DatasetEvaluation }) {
  const aggregate = evaluation.aggregateMetrics;
  if (aggregate === null) {
    return (
      <div data-testid="evaluation-metrics-view">
        <Typography.Text type="secondary">Evaluation metrics are not available yet.</Typography.Text>
      </div>
    );
  }

  const localization = aggregate.localization;
  const classification = aggregate.classificationOnMatched;
  const perClass = evaluation.perClassMetrics ?? [];
  const confusion = evaluation.confusion ?? [];
  const inapplicable = `N/A — ${aggregate.classificationReason ?? "not applicable"}`;

  return (
    <div data-testid="evaluation-metrics-view">
      <Descriptions column={2} size="small" title="Localization">
        <Descriptions.Item label="AP50">{formatMetric(localization?.ap50)}</Descriptions.Item>
        <Descriptions.Item label="AP50:95">{formatMetric(localization?.ap50_95)}</Descriptions.Item>
        <Descriptions.Item label="Precision">{formatMetric(localization?.operating?.precision)}</Descriptions.Item>
        <Descriptions.Item label="Recall">{formatMetric(localization?.operating?.recall)}</Descriptions.Item>
        <Descriptions.Item label="F1">{formatMetric(localization?.operating?.f1)}</Descriptions.Item>
      </Descriptions>
      <Descriptions column={2} size="small" title="Classification">
        {aggregate.classificationApplicable ? (
          <Descriptions.Item label="Matched accuracy">{formatMetric(classification?.matchedAccuracy)}</Descriptions.Item>
        ) : (
          <Descriptions.Item label="Classification">{inapplicable}</Descriptions.Item>
        )}
      </Descriptions>
      <Typography.Title level={5}>Per-class</Typography.Title>
      {!aggregate.classificationApplicable ? (
        <Typography.Text type="secondary">{inapplicable}</Typography.Text>
      ) : perClass.length > 0 ? (
        <Table
          rowKey={(record) => String(record.classId)}
          pagination={false}
          size="small"
          dataSource={perClass}
          columns={[
            { title: "Class", render: (_: unknown, record) => `${record.className} (${record.classId})` },
            { title: "GT", dataIndex: "gtCount" },
            { title: "Pred", dataIndex: "predictionCount" },
            { title: "AP50", render: (_: unknown, record) => formatMetric(record.ap50) },
            { title: "AP50:95", render: (_: unknown, record) => formatMetric(record.ap50_95) },
            { title: "Precision", render: (_: unknown, record) => formatMetric(record.operating?.precision) },
            { title: "Recall", render: (_: unknown, record) => formatMetric(record.operating?.recall) },
            { title: "F1", render: (_: unknown, record) => formatMetric(record.operating?.f1) },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">No per-class metrics available.</Typography.Text>
      )}
      <Typography.Title level={5}>Confusion</Typography.Title>
      {!aggregate.classificationApplicable ? (
        <Typography.Text type="secondary">{inapplicable}</Typography.Text>
      ) : confusion.length > 0 ? (
        <Table
          rowKey={(record) => `${record.gtClassId}->${record.predClassId}`}
          pagination={false}
          size="small"
          dataSource={confusion}
          columns={[
            { title: "GT class", render: (_: unknown, record) => `${record.gtClassName} (${record.gtClassId})` },
            { title: "Predicted class", render: (_: unknown, record) => `${record.predClassName} (${record.predClassId})` },
            { title: "Count", dataIndex: "count" },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">No confusion data available.</Typography.Text>
      )}
    </div>
  );
}
