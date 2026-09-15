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
import { getDatasetBenchmark, listDatasetBenchmarkItems, PlatformApiError, retryDatasetBenchmark, runDatasetBenchmark } from "../../api/client";
import type {
  DatasetBenchmarkAggregateMetrics,
  DatasetBenchmarkConfusion,
  DatasetBenchmarkPerClassMetric,
  DatasetEvaluation,
  DatasetEvaluationItem,
  GroundTruthProvenance,
} from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import type { MessageKey, MessageVars } from "../../localization/types";
import { evaluationStatusKey } from "../analysis-run/statusModel";

type Translate = (key: MessageKey, vars?: MessageVars) => string;

function toErrorText(error: unknown): string {
  if (error instanceof PlatformApiError) return error.display;
  if (error instanceof Error) return error.message;
  return String(error);
}

export interface BenchmarkDetailViewProps {
  evaluationId: string;
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}

function fmtMetric(t: Translate, value: number | null | undefined): string {
  return value == null ? t("metrics.notAvailable") : value.toFixed(4);
}

function renderCompletedBenchmark(args: {
  t: Translate;
  evaluation: DatasetEvaluation;
  aggregate: DatasetBenchmarkAggregateMetrics;
  gt?: GroundTruthProvenance;
  perClass: DatasetBenchmarkPerClassMetric[];
  confusions: DatasetBenchmarkConfusion[];
  items: DatasetEvaluationItem[];
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}) {
  const { t, evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase } = args;
  const classAware = aggregate.classAware;
  const matched = aggregate.classificationOnMatched;
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <Button onClick={onBack}>{t("benchmarks.backToList")}</Button>
        <Typography.Title level={3} style={{ margin: 0 }}>{evaluation.name}</Typography.Title>
      </Space>

      <Row gutter={16}>
        <Col span={6}><Statistic title={t("benchmarks.statEndToEndMap5095")} value={fmtMetric(t, classAware?.map50_95)} /></Col>
        <Col span={6}><Statistic title={t("metrics.classAwareMap50")} value={fmtMetric(t, classAware?.map50)} /></Col>
        <Col span={6}><Statistic title={t("compare.metricLocalizationAp50_95")} value={fmtMetric(t, aggregate.localization.ap50_95)} /></Col>
        <Col span={6}><Statistic title={t("metrics.matchedAccuracy")} value={fmtMetric(t, matched?.matchedAccuracy)} /></Col>
      </Row>

      <Card title={t("benchmarks.gtProvenance")}>
        {gt ? (
          <Descriptions column={4}>
            <Descriptions.Item label={t("benchmarks.rawAnnotations")}>{gt.rawCount}</Descriptions.Item>
            <Descriptions.Item label={t("benchmarks.evaluationGt")}>{gt.canonicalCount}</Descriptions.Item>
            <Descriptions.Item label={t("benchmarks.duplicatesRemoved")}>{gt.duplicatesRemoved}</Descriptions.Item>
            <Descriptions.Item label={t("benchmarks.policy")}>{gt.duplicatePolicy}</Descriptions.Item>
          </Descriptions>
        ) : <Typography.Text>{t("benchmarks.rawGtProtocol")}</Typography.Text>}
      </Card>

      <Row gutter={16}>
        <Col span={8}><Card title={t("metrics.localization")}><Descriptions column={1}>
          <Descriptions.Item label={t("metrics.ap50")}>{fmtMetric(t, aggregate.localization.ap50)}</Descriptions.Item>
          <Descriptions.Item label={t("metrics.ap50_95")}>{fmtMetric(t, aggregate.localization.ap50_95)}</Descriptions.Item>
          <Descriptions.Item label="TP / FP / FN">{`${aggregate.localization.operating.tp} / ${aggregate.localization.operating.fp} / ${aggregate.localization.operating.fn}`}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(t, aggregate.localization.operating.precision)} / ${fmtMetric(t, aggregate.localization.operating.recall)} / ${fmtMetric(t, aggregate.localization.operating.f1)}`}</Descriptions.Item>
        </Descriptions></Card></Col>
        <Col span={8}><Card title={t("runMetrics.classificationOnMatched")}>{matched ? <Descriptions column={1}>
          <Descriptions.Item label={t("runMetrics.matched")}>{matched.matchedCount}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.correctWrong")}>{matched.classCorrect} / {matched.classWrong}</Descriptions.Item>
          <Descriptions.Item label={t("metrics.matchedAccuracy")}>{fmtMetric(t, matched.matchedAccuracy)}</Descriptions.Item>
        </Descriptions> : <Typography.Text>{t("metrics.notAvailable")}</Typography.Text>}</Card></Col>
        <Col span={8}><Card title={t("metrics.endToEnd")}>{classAware ? <Descriptions column={1}>
          <Descriptions.Item label={t("metrics.classAwareMap50")}>{fmtMetric(t, classAware.map50)}</Descriptions.Item>
          <Descriptions.Item label={t("metrics.classAwareMap50_95")}>{fmtMetric(t, classAware.map50_95)}</Descriptions.Item>
          <Descriptions.Item label="TP / FP / FN">{`${classAware.operating.tp} / ${classAware.operating.fp} / ${classAware.operating.fn}`}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(t, classAware.operating.precision)} / ${fmtMetric(t, classAware.operating.recall)} / ${fmtMetric(t, classAware.operating.f1)}`}</Descriptions.Item>
        </Descriptions> : <Typography.Text>{t("metrics.notAvailable")}</Typography.Text>}</Card></Col>
      </Row>

      <Card title={t("benchmarks.perClassMetrics")}><Table
        rowKey="classId"
        pagination={false}
        dataSource={[...perClass].sort((a, b) => a.classId - b.classId)}
        columns={[
          { title: t("metrics.class"), render: (_, row) => <span>{row.classId} · <span>{row.className}</span></span> },
          { title: t("metrics.gt"), dataIndex: "gtCount" },
          { title: t("metrics.pred"), dataIndex: "predictionCount" },
          { title: t("metrics.ap50"), render: (_, row) => fmtMetric(t, row.ap50) },
          { title: t("metrics.ap50_95"), render: (_, row) => fmtMetric(t, row.ap50_95), sorter: (a, b) => (a.ap50_95 ?? -1) - (b.ap50_95 ?? -1) },
          { title: "P", render: (_, row) => fmtMetric(t, row.operating.precision) },
          { title: "R", render: (_, row) => fmtMetric(t, row.operating.recall) },
          { title: t("metrics.f1"), render: (_, row) => fmtMetric(t, row.operating.f1) },
        ]}
      /></Card>

      <Card title={t("benchmarks.topConfusions")}><Table
        rowKey={(row) => `${row.gtClassId}-${row.predClassId}`}
        pagination={{ pageSize: 10 }}
        dataSource={confusions}
        columns={[
          { title: t("metrics.gt"), render: (_, row) => `${row.gtClassId} · ${row.gtClassName}` },
          { title: t("metrics.pred"), render: (_, row) => `${row.predClassId} · ${row.predClassName}` },
          { title: t("metrics.count"), dataIndex: "count" },
        ]}
      /></Card>

      <Collapse defaultActiveKey={["protocol"]} items={[{ key: "protocol", label: t("benchmarks.protocolProvenance"), children: (
        <Descriptions column={1}>
          <Descriptions.Item label={t("benchmarks.evaluationProtocol")}>{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.manifestSha")}>{evaluation.recordingManifestHash}</Descriptions.Item>
          <Descriptions.Item label={t("form.pipeline")}>{evaluation.pipelineId} · {evaluation.pipelineVersion}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.protocolConfig")}><pre>{JSON.stringify(evaluation.protocolConfig, null, 2)}</pre></Descriptions.Item>
        </Descriptions>
      ) }]} />

      <Card title={t("benchmarks.evaluationItems", { count: items.length })}><Table
        rowKey="id"
        pagination={{ pageSize: 50 }}
        dataSource={items}
        columns={[
          { title: t("items.columnRecording"), dataIndex: "recordingName" },
          { title: t("metrics.gt"), dataIndex: "gtCount" },
          { title: t("benchmarks.predictions"), dataIndex: "predictionCount" },
          { title: t("benchmarks.analysisRun"), dataIndex: "analysisRunId" },
          { title: t("benchmarks.columnAction"), render: (_, row) => (
            <Button disabled={!row.analysisRunId} onClick={() => row.analysisRunId && onOpenCase(row.recordingId, row.analysisRunId)}>{t("benchmarks.inspect")}</Button>
          ) },
        ]}
      /></Card>
    </Space>
  );
}

export function BenchmarkDetailView({ evaluationId, onBack, onOpenCase }: BenchmarkDetailViewProps) {
  const { t } = useLocalization();
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
        timer = setTimeout(() => void load().catch((e: unknown) => setError(toErrorText(e))), 1000);
      }
    };

    void load().catch((e: unknown) => setError(toErrorText(e)));
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [evaluationId, pollGeneration]);

  if (error) {
    return (
      <Card title={t("benchmarks.detailTitle")}>
        <Button onClick={onBack}>{t("benchmarks.backToList")}</Button>
        <Alert type="error" showIcon message={error} />
      </Card>
    );
  }
  if (!evaluation) return <Spin tip={t("benchmarks.loading")} />;

  if (evaluation.status === "pending" || evaluation.status === "running") {
    const statusKey = evaluationStatusKey(evaluation.status);
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>{t("benchmarks.backToList")}</Button>
        <Descriptions column={1}>
          <Descriptions.Item label={t("experiment.columnStatus")}>{statusKey !== null ? t(statusKey) : evaluation.status}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.stage")}>{evaluation.progressStage ?? "pending"}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.columnProtocol")}>{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label={t("benchmarks.columnCoverage")}>{evaluation.evaluatedRecordings} / {evaluation.expectedRecordings}</Descriptions.Item>
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
        setError(toErrorText(e));
      }
    };
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>{t("benchmarks.backToList")}</Button>
        <Alert
          type="error"
          showIcon
          message={evaluation.errorType ?? t("benchmarks.failed")}
          description={evaluation.errorMessage ?? undefined}
        />
        <Button onClick={() => void retry()}>{t("benchmarks.actionRetry")}</Button>
      </Card>
    );
  }

  const aggregate = evaluation.aggregateMetrics;
  if (!aggregate) return <Alert type="error" message={t("benchmarks.noAggregate")} />;
  const gt = aggregate.groundTruth;
  const confusions = [...(evaluation.confusion ?? [])].sort((a, b) => b.count - a.count);
  const perClass = evaluation.perClassMetrics ?? [];

  return renderCompletedBenchmark({ t, evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase });
}
