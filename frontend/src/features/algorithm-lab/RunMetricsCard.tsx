import { Card, Descriptions, Empty, Statistic, Tag, Typography } from "antd";
import type { RunComparison } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

const formatRatio = (value: number) => value.toFixed(4);

function ClassificationSection({ run }: { run: RunComparison }) {
  const { t } = useLocalization();
  if (!run.classificationApplicable) {
    return (
      <div style={{ marginTop: 8 }}>
        <Typography.Text type="secondary">
          {t("runMetrics.notApplicable", { reason: run.classificationReason ?? t("runMetrics.reasonUnknown") })}
        </Typography.Text>
      </div>
    );
  }
  const classification = run.classification;
  if (!classification) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text strong>{t("runMetrics.classificationOnMatched")}</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label={t("runMetrics.matched")}>{classification.matchedCount}</Descriptions.Item>
        <Descriptions.Item label={t("runMetrics.correct")}>{classification.classCorrect}</Descriptions.Item>
        <Descriptions.Item label={t("runMetrics.wrong")}>{classification.classWrong}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.matchedAccuracy")}>
          {classification.matchedAccuracy === null ? "—" : formatRatio(classification.matchedAccuracy)}
        </Descriptions.Item>
      </Descriptions>
      <Typography.Text strong>{t("runMetrics.confusions")}</Typography.Text>
      {classification.confusions.length === 0 ? (
        <div><Typography.Text type="secondary">{t("common.none")}</Typography.Text></div>
      ) : (
        <ul style={{ margin: 4, paddingLeft: 20 }}>
          {classification.confusions.map((confusion) => (
            <li key={`${confusion.gtClassId}-${confusion.predClassId}`}>
              {confusion.gtClassName} ({confusion.gtClassId}) → {confusion.predClassName} ({confusion.predClassId}) × {confusion.count}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EndToEndSection({ run }: { run: RunComparison }) {
  const { t } = useLocalization();
  if (!run.classificationApplicable || !run.classAware) {
    return (
      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">{t("runMetrics.notApplicableNoReason")}</Typography.Text>
      </div>
    );
  }
  const aware = run.classAware;
  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text strong>{t("runMetrics.endToEndClassAware")}</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label={t("runMetrics.classAwarePrecision")}>{formatRatio(aware.precision)}</Descriptions.Item>
        <Descriptions.Item label={t("runMetrics.classAwareRecall")}>{formatRatio(aware.recall)}</Descriptions.Item>
        <Descriptions.Item label={t("runMetrics.classAwareF1")}>{formatRatio(aware.f1)}</Descriptions.Item>
        <Descriptions.Item label="TP / FP / FN">{`${aware.tp} / ${aware.fp} / ${aware.fn}`}</Descriptions.Item>
      </Descriptions>
    </div>
  );
}

export function RunMetricsCard({ run, side }: { run: RunComparison; side: "A" | "B" }) {
  const { t } = useLocalization();
  const { metrics } = run;
  return (
    <Card
      size="small"
      title={<span>{t("runMetrics.runTitle", { side, name: run.pipelineName })}</span>}
      extra={<Tag color={side === "A" ? "blue" : "green"}>{run.pipelineId}</Tag>}
    >
      <Typography.Text strong>{t("runMetrics.localization")}</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label={t("metrics.precision")}>{formatRatio(metrics.precision)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.recall")}>{formatRatio(metrics.recall)}</Descriptions.Item>
        <Descriptions.Item label={t("metrics.f1")}>{formatRatio(metrics.f1)}</Descriptions.Item>
        <Descriptions.Item label={t("runMetrics.meanIou")}>
          {metrics.meanMatchedIou === null ? "—" : formatRatio(metrics.meanMatchedIou)}
        </Descriptions.Item>
      </Descriptions>
      <Statistic
        title="TP / FP / FN"
        value={`${metrics.tp} / ${metrics.fp} / ${metrics.fn}`}
        style={{ marginTop: 8 }}
      />
      <ClassificationSection run={run} />
      <EndToEndSection run={run} />
    </Card>
  );
}
