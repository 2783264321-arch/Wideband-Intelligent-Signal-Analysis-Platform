import { Descriptions, Table, Typography } from "antd";
import type { DatasetEvaluation } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

/**
 * Evaluation metrics for a DatasetEvaluation.
 *
 * Classification applicability comes ONLY from
 * `aggregateMetrics.classificationApplicable` / `.classificationReason`
 * (never top-level fields). Null metrics render `N/A`, never 0. A null
 * `aggregateMetrics` renders an explicit unavailable state.
 */
export function EvaluationMetricsView({ evaluation }: { evaluation: DatasetEvaluation }) {
  const { t } = useLocalization();
  const formatMetric = (value: unknown): string =>
    typeof value === "number" ? String(value) : t("metrics.notAvailable");

  const aggregate = evaluation.aggregateMetrics;
  if (aggregate === null) {
    return (
      <div data-testid="evaluation-metrics-view">
        <Typography.Text type="secondary">{t("evaluation.metricsUnavailable")}</Typography.Text>
      </div>
    );
  }

  const localization = aggregate.localization;
  const classification = aggregate.classificationOnMatched;
  const perClass = evaluation.perClassMetrics ?? [];
  const confusion = evaluation.confusion ?? [];
  const inapplicable = t("metrics.notApplicable", {
    reason: aggregate.classificationReason ?? t("metrics.notApplicableDefault"),
  });

  return (
    <div data-testid="evaluation-metrics-view">
      <Descriptions column={2} size="small" title={t("metrics.localization")}>
        <Descriptions.Item label={t("metrics.ap50")}>{formatMetric(localization?.ap50)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.ap50_95")}>{formatMetric(localization?.ap50_95)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.precision")}>{formatMetric(localization?.operating?.precision)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.recall")}>{formatMetric(localization?.operating?.recall)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.f1")}>{formatMetric(localization?.operating?.f1)}</Descriptions.Item>
      </Descriptions>
      <Descriptions column={2} size="small" title={t("metrics.classification")}>
        {aggregate.classificationApplicable ? (
          <Descriptions.Item label={t("metrics.matchedAccuracy")}>{formatMetric(classification?.matchedAccuracy)}</Descriptions.Item>
        ) : (
          <Descriptions.Item label={t("metrics.classification")}>{inapplicable}</Descriptions.Item>
        )}
      </Descriptions>
      <Typography.Title level={5}>{t("metrics.perClass")}</Typography.Title>
      {!aggregate.classificationApplicable ? (
        <Typography.Text type="secondary">{inapplicable}</Typography.Text>
      ) : perClass.length > 0 ? (
        <Table
          rowKey={(record) => String(record.classId)}
          pagination={false}
          size="small"
          dataSource={perClass}
          columns={[
            { title: t("metrics.class"), render: (_: unknown, record) => `${record.className} (${record.classId})` },
            { title: t("metrics.gt"), dataIndex: "gtCount" },
            { title: t("metrics.pred"), dataIndex: "predictionCount" },
            { title: t("metrics.ap50"), render: (_: unknown, record) => formatMetric(record.ap50) },
            { title: t("metrics.ap50_95"), render: (_: unknown, record) => formatMetric(record.ap50_95) },
            { title: t("metrics.precision"), render: (_: unknown, record) => formatMetric(record.operating?.precision) },
            { title: t("metrics.recall"), render: (_: unknown, record) => formatMetric(record.operating?.recall) },
            { title: t("metrics.f1"), render: (_: unknown, record) => formatMetric(record.operating?.f1) },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">{t("metrics.noPerClass")}</Typography.Text>
      )}
      <Typography.Title level={5}>{t("metrics.confusion")}</Typography.Title>
      {!aggregate.classificationApplicable ? (
        <Typography.Text type="secondary">{inapplicable}</Typography.Text>
      ) : confusion.length > 0 ? (
        <Table
          rowKey={(record) => `${record.gtClassId}->${record.predClassId}`}
          pagination={false}
          size="small"
          dataSource={confusion}
          columns={[
            { title: t("metrics.gtClass"), render: (_: unknown, record) => `${record.gtClassName} (${record.gtClassId})` },
            { title: t("metrics.predClass"), render: (_: unknown, record) => `${record.predClassName} (${record.predClassId})` },
            { title: t("metrics.count"), dataIndex: "count" },
          ]}
        />
      ) : (
        <Typography.Text type="secondary">{t("metrics.noConfusion")}</Typography.Text>
      )}
    </div>
  );
}
