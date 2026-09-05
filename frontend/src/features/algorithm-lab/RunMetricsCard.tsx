import { Card, Descriptions, Statistic, Tag } from "antd";
import type { RunComparison } from "../../api/types";

const formatRatio = (value: number) => value.toFixed(4);

export function RunMetricsCard({ run, side }: { run: RunComparison; side: "A" | "B" }) {
  const { metrics } = run;
  return (
    <Card
      size="small"
      title={<span>Run {side}: {run.pipelineName}</span>}
      extra={<Tag color={side === "A" ? "blue" : "green"}>{run.pipelineId}</Tag>}
    >
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
    </Card>
  );
}