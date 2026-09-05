import { Card, Descriptions, Empty, Statistic, Tag, Typography } from "antd";
import type { RunComparison } from "../../api/types";

const formatRatio = (value: number) => value.toFixed(4);

function ClassificationSection({ run }: { run: RunComparison }) {
  if (!run.classificationApplicable) {
    return (
      <div style={{ marginTop: 8 }}>
        <Typography.Text type="secondary">Not applicable — {run.classificationReason ?? "unknown"}</Typography.Text>
      </div>
    );
  }
  const classification = run.classification;
  if (!classification) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text strong>Classification (on matched)</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label="Matched">{classification.matchedCount}</Descriptions.Item>
        <Descriptions.Item label="Correct">{classification.classCorrect}</Descriptions.Item>
        <Descriptions.Item label="Wrong">{classification.classWrong}</Descriptions.Item>
        <Descriptions.Item label="Matched Accuracy">
          {classification.matchedAccuracy === null ? "—" : formatRatio(classification.matchedAccuracy)}
        </Descriptions.Item>
      </Descriptions>
      <Typography.Text strong>Confusions</Typography.Text>
      {classification.confusions.length === 0 ? (
        <div><Typography.Text type="secondary">None</Typography.Text></div>
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
  if (!run.classificationApplicable || !run.classAware) {
    return (
      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">Not applicable</Typography.Text>
      </div>
    );
  }
  const aware = run.classAware;
  return (
    <div style={{ marginTop: 12 }}>
      <Typography.Text strong>End-to-End (class-aware)</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label="Class-aware Precision">{formatRatio(aware.precision)}</Descriptions.Item>
        <Descriptions.Item label="Class-aware Recall">{formatRatio(aware.recall)}</Descriptions.Item>
        <Descriptions.Item label="Class-aware F1">{formatRatio(aware.f1)}</Descriptions.Item>
        <Descriptions.Item label="TP / FP / FN">{`${aware.tp} / ${aware.fp} / ${aware.fn}`}</Descriptions.Item>
      </Descriptions>
    </div>
  );
}

export function RunMetricsCard({ run, side }: { run: RunComparison; side: "A" | "B" }) {
  const { metrics } = run;
  return (
    <Card
      size="small"
      title={<span>Run {side}: {run.pipelineName}</span>}
      extra={<Tag color={side === "A" ? "blue" : "green"}>{run.pipelineId}</Tag>}
    >
      <Typography.Text strong>Localization</Typography.Text>
      <Descriptions column={2} size="small">
        <Descriptions.Item label="Precision">{formatRatio(metrics.precision)}</Descriptions.Item>
        <Descriptions.Item label="Recall">{formatRatio(metrics.recall)}</Descriptions.Item>
        <Descriptions.Item label="F1">{formatRatio(metrics.f1)}</Descriptions.Item>
        <Descriptions.Item label="Mean IoU">
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