import { Descriptions, Typography } from "antd";
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
          <Descriptions.Item label="Classification">
            {`N/A — ${aggregate.classificationReason ?? "not applicable"}`}
          </Descriptions.Item>
        )}
      </Descriptions>
    </div>
  );
}
