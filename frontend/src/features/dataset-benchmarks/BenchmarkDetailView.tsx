import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Typography,
} from "antd";
import { useEffect, useState } from "react";
import { getDatasetBenchmark, listDatasetBenchmarkItems, retryDatasetBenchmark, runDatasetBenchmark } from "../../api/client";
import type {
  DatasetBenchmarkAggregateMetrics,
  DatasetBenchmarkConfusion,
  DatasetBenchmarkPerClassMetric,
  DatasetEvaluation,
  DatasetEvaluationItem,
  GroundTruthProvenance,
} from "../../api/types";

export interface BenchmarkDetailViewProps {
  evaluationId: string;
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}

function fmtMetric(value: number | null | undefined): string {
  return value == null ? "N/A" : value.toFixed(4);
}

function renderCompletedBenchmark(args: {
  evaluation: DatasetEvaluation;
  aggregate: DatasetBenchmarkAggregateMetrics;
  gt?: GroundTruthProvenance;
  perClass: DatasetBenchmarkPerClassMetric[];
  confusions: DatasetBenchmarkConfusion[];
  items: DatasetEvaluationItem[];
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}) {
  const { evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase } = args;
  const classAware = aggregate.classAware;
  const matched = aggregate.classificationOnMatched;
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <Button onClick={onBack}>Back to list</Button>
        <Typography.Title level={3} style={{ margin: 0 }}>{evaluation.name}</Typography.Title>
      </Space>

      <Row gutter={16}>
        <Col span={6}><Statistic title="End-to-End Class-aware mAP50:95" value={fmtMetric(classAware?.map50_95)} /></Col>
        <Col span={6}><Statistic title="mAP50" value={fmtMetric(classAware?.map50)} /></Col>
        <Col span={6}><Statistic title="Localization AP50:95" value={fmtMetric(aggregate.localization.ap50_95)} /></Col>
        <Col span={6}><Statistic title="Matched Accuracy" value={fmtMetric(matched?.matchedAccuracy)} /></Col>
      </Row>

      <Card title="Ground Truth Provenance">
        {gt ? (
          <Descriptions column={4}>
            <Descriptions.Item label="Raw annotations">{gt.rawCount}</Descriptions.Item>
            <Descriptions.Item label="Evaluation GT">{gt.canonicalCount}</Descriptions.Item>
            <Descriptions.Item label="Exact duplicates removed">{gt.duplicatesRemoved}</Descriptions.Item>
            <Descriptions.Item label="Policy">{gt.duplicatePolicy}</Descriptions.Item>
          </Descriptions>
        ) : <Typography.Text>Raw GT protocol</Typography.Text>}
      </Card>

      <Row gutter={16}>
        <Col span={8}><Card title="Localization"><Descriptions column={1}>
          <Descriptions.Item label="AP50">{fmtMetric(aggregate.localization.ap50)}</Descriptions.Item>
          <Descriptions.Item label="AP50:95">{fmtMetric(aggregate.localization.ap50_95)}</Descriptions.Item>
          <Descriptions.Item label="TP / FP / FN">{`${aggregate.localization.operating.tp} / ${aggregate.localization.operating.fp} / ${aggregate.localization.operating.fn}`}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(aggregate.localization.operating.precision)} / ${fmtMetric(aggregate.localization.operating.recall)} / ${fmtMetric(aggregate.localization.operating.f1)}`}</Descriptions.Item>
        </Descriptions></Card></Col>
        <Col span={8}><Card title="Classification on Matched">{matched ? <Descriptions column={1}>
          <Descriptions.Item label="Matched">{matched.matchedCount}</Descriptions.Item>
          <Descriptions.Item label="Correct / Wrong">{matched.classCorrect} / {matched.classWrong}</Descriptions.Item>
          <Descriptions.Item label="Accuracy">{fmtMetric(matched.matchedAccuracy)}</Descriptions.Item>
        </Descriptions> : <Typography.Text>N/A</Typography.Text>}</Card></Col>
        <Col span={8}><Card title="End-to-End">{classAware ? <Descriptions column={1}>
          <Descriptions.Item label="mAP50">{fmtMetric(classAware.map50)}</Descriptions.Item>
          <Descriptions.Item label="mAP50:95">{fmtMetric(classAware.map50_95)}</Descriptions.Item>
          <Descriptions.Item label="TP / FP / FN">{`${classAware.operating.tp} / ${classAware.operating.fp} / ${classAware.operating.fn}`}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(classAware.operating.precision)} / ${fmtMetric(classAware.operating.recall)} / ${fmtMetric(classAware.operating.f1)}`}</Descriptions.Item>
        </Descriptions> : <Typography.Text>N/A</Typography.Text>}</Card></Col>
      </Row>

      <Card title="Per-Class Metrics"><Table
        rowKey="classId"
        pagination={false}
        dataSource={[...perClass].sort((a, b) => a.classId - b.classId)}
        columns={[
          { title: "Class", render: (_, row) => <span>{row.classId} · <span>{row.className}</span></span> },
          { title: "GT", dataIndex: "gtCount" },
          { title: "Pred", dataIndex: "predictionCount" },
          { title: "AP50", render: (_, row) => fmtMetric(row.ap50) },
          { title: "AP50:95", render: (_, row) => fmtMetric(row.ap50_95), sorter: (a, b) => (a.ap50_95 ?? -1) - (b.ap50_95 ?? -1) },
          { title: "P", render: (_, row) => fmtMetric(row.operating.precision) },
          { title: "R", render: (_, row) => fmtMetric(row.operating.recall) },
          { title: "F1", render: (_, row) => fmtMetric(row.operating.f1) },
        ]}
      /></Card>

      <Card title="Top Classification Confusions"><Table
        rowKey={(row) => `${row.gtClassId}-${row.predClassId}`}
        pagination={{ pageSize: 10 }}
        dataSource={confusions}
        columns={[
          { title: "GT", render: (_, row) => `${row.gtClassId} · ${row.gtClassName}` },
          { title: "Pred", render: (_, row) => `${row.predClassId} · ${row.predClassName}` },
          { title: "Count", dataIndex: "count" },
        ]}
      /></Card>

      <Collapse defaultActiveKey={["protocol"]} items={[{ key: "protocol", label: "Protocol & Provenance", children: (
        <Descriptions column={1}>
          <Descriptions.Item label="Evaluation protocol">{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label="Manifest SHA256">{evaluation.recordingManifestHash}</Descriptions.Item>
          <Descriptions.Item label="Pipeline">{evaluation.pipelineId} · {evaluation.pipelineVersion}</Descriptions.Item>
          <Descriptions.Item label="Protocol config"><pre>{JSON.stringify(evaluation.protocolConfig, null, 2)}</pre></Descriptions.Item>
        </Descriptions>
      ) }]} />

      <Card title={`${items.length} Evaluation Items`}><Table
        rowKey="id"
        pagination={{ pageSize: 50 }}
        dataSource={items}
        columns={[
          { title: "Recording", dataIndex: "recordingName" },
          { title: "GT", dataIndex: "gtCount" },
          { title: "Predictions", dataIndex: "predictionCount" },
          { title: "Analysis Run", dataIndex: "analysisRunId" },
          { title: "Action", render: (_, row) => (
            <Button disabled={!row.analysisRunId} onClick={() => row.analysisRunId && onOpenCase(row.recordingId, row.analysisRunId)}>Inspect</Button>
          ) },
        ]}
      /></Card>
    </Space>
  );
}

export function BenchmarkDetailView({ evaluationId, onBack, onOpenCase }: BenchmarkDetailViewProps) {
  const [evaluation, setEvaluation] = useState<DatasetEvaluation>();
  const [items, setItems] = useState<DatasetEvaluationItem[]>([]);
  const [error, setError] = useState<string>();
  const [pollGeneration, setPollGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      const next = await getDatasetBenchmark(evaluationId);
      if (cancelled) return;
      setEvaluation(next);
      if (next.status === "completed") {
        const nextItems = await listDatasetBenchmarkItems(evaluationId);
        if (!cancelled) setItems(nextItems);
        return;
      }
      if (next.status === "pending" || next.status === "running") {
        timer = setTimeout(() => void load().catch((e: unknown) => setError(String(e))), 1000);
      }
    };

    void load().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [evaluationId, pollGeneration]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (!evaluation) return <Spin tip="Loading benchmark..." />;

  if (evaluation.status === "pending" || evaluation.status === "running") {
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>Back to list</Button>
        <Descriptions column={1}>
          <Descriptions.Item label="Status">{evaluation.status}</Descriptions.Item>
          <Descriptions.Item label="Stage">{evaluation.progressStage ?? "pending"}</Descriptions.Item>
          <Descriptions.Item label="Protocol">{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label="Coverage">{evaluation.evaluatedRecordings} / {evaluation.expectedRecordings}</Descriptions.Item>
        </Descriptions>
      </Card>
    );
  }

  if (evaluation.status === "failed" || evaluation.status === "interrupted") {
    const retry = async () => {
      try {
        await retryDatasetBenchmark(evaluation.id);
        const restarted = await runDatasetBenchmark(evaluation.id);
        setEvaluation(restarted);
        setPollGeneration((value) => value + 1);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>Back to list</Button>
        <Alert
          type="error"
          showIcon
          message={evaluation.errorType ?? "Benchmark failed"}
          description={evaluation.errorMessage ?? undefined}
        />
        <Button onClick={() => void retry()}>Retry</Button>
      </Card>
    );
  }

  const aggregate = evaluation.aggregateMetrics;
  if (!aggregate) return <Alert type="error" message="Completed benchmark has no aggregate metrics." />;
  const gt = aggregate.groundTruth;
  const confusions = [...(evaluation.confusion ?? [])].sort((a, b) => b.count - a.count);
  const perClass = evaluation.perClassMetrics ?? [];

  return renderCompletedBenchmark({ evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase });
}