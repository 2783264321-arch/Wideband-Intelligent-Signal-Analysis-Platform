import { Alert, Button, Card, Col, Row, Select, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";
import {
  compareAnalysisRuns,
  getDetections,
  getGroundTruth,
  getRecording,
  getSpectrogram,
  listAnalysisRuns,
  listRecordings,
} from "../../api/client";
import type {
  AlgorithmLabCompareResponse,
  AnalysisRun,
  DetectionResult,
  GroundTruthResult,
  RecordingDetail,
  SpectrogramMeta,
} from "../../api/types";
import { CaseComparisonTable } from "./CaseComparisonTable";
import { RunComparisonPanel } from "./RunComparisonPanel";
import { RunMetricsCard } from "./RunMetricsCard";

const runLabel = (run: AnalysisRun) => `${run.pipelineId} · ${run.id}`;

export interface CaseAnalysisViewProps {
  recordingId?: string;
  runAId?: string;
  runBId?: string;
  onRecordingChange: (recordingId?: string) => void;
  onRunAChange: (runId?: string) => void;
  onRunBChange: (runId?: string) => void;
}

export function CaseAnalysisView({
  recordingId,
  runAId,
  runBId,
  onRecordingChange,
  onRunAChange,
  onRunBChange,
}: CaseAnalysisViewProps) {
  const [recordings, setRecordings] = useState<RecordingDetail[]>([]);
  const [selectedRecording, setSelectedRecording] = useState<RecordingDetail | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compare, setCompare] = useState<AlgorithmLabCompareResponse | null>(null);
  const [meta, setMeta] = useState<SpectrogramMeta | null>(null);
  const [groundTruth, setGroundTruth] = useState<GroundTruthResult[]>([]);
  const [detectionsA, setDetectionsA] = useState<DetectionResult[]>([]);
  const [detectionsB, setDetectionsB] = useState<DetectionResult[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string | undefined>();

  useEffect(() => {
    listRecordings(500, 0)
      .then((page) => setRecordings(page.items))
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Unable to load recordings."));
  }, []);

  // Resolve the selected Recording: from the paged list when present, otherwise
  // hydrate directly by id so any of the 2500 benchmark samples can be reached.
  // When the Recording identity changes, any derived display state for the previous
  // Recording must be invalidated so a stale comparison is never shown.
  useEffect(() => {
    let cancelled = false;
    setCompare(null);
    setMeta(null);
    setGroundTruth([]);
    setDetectionsA([]);
    setDetectionsB([]);
    setSelectedCaseId(undefined);
    if (!recordingId) {
      setSelectedRecording(null);
      setRuns([]);
      return;
    }
    const fromList = recordings.find((item) => item.id === recordingId);
    const load = async () => {
      const recording = fromList ?? (await getRecording(recordingId));
      if (cancelled) return;
      setSelectedRecording(recording);
      setError(null);
      const nextRuns = await listAnalysisRuns(recordingId);
      if (cancelled) return;
      setRuns(nextRuns);
    };
    void load().catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Unable to load analysis runs.");
    });
    return () => {
      cancelled = true;
    };
  }, [recordingId, recordings]);

  const runCompare = async () => {
    if (!recordingId || !runAId || !runBId || runAId === runBId) return;
    setLoading(true);
    setError(null);
    try {
      const [result, nextMeta, nextGroundTruth, nextA, nextB] = await Promise.all([
        compareAnalysisRuns({ recordingId, runAId, runBId }),
        getSpectrogram(recordingId),
        getGroundTruth(recordingId),
        getDetections(runAId),
        getDetections(runBId),
      ]);
      setCompare(result);
      setMeta(nextMeta);
      setGroundTruth(nextGroundTruth);
      setDetectionsA(nextA);
      setDetectionsB(nextB);
      setSelectedCaseId(undefined);
    } catch (reason) {
      setCompare(null);
      setError(reason instanceof Error ? reason.message : "Unable to compare runs.");
    } finally {
      setLoading(false);
    }
  };

  // Auto-drive the view from query-provided selection: A/B compare when both runs
  // are present, single-run inspection when only Run A is present.
  useEffect(() => {
    let cancelled = false;
    if (!recordingId || !runAId) return;
    if (runBId && runBId !== runAId) {
      setLoading(true);
      setError(null);
      Promise.all([
        compareAnalysisRuns({ recordingId, runAId, runBId }),
        getSpectrogram(recordingId),
        getGroundTruth(recordingId),
        getDetections(runAId),
        getDetections(runBId),
      ])
        .then(([result, nextMeta, nextGroundTruth, nextA, nextB]) => {
          if (cancelled) return;
          setCompare(result);
          setMeta(nextMeta);
          setGroundTruth(nextGroundTruth);
          setDetectionsA(nextA);
          setDetectionsB(nextB);
          setSelectedCaseId(undefined);
        })
        .catch((reason: unknown) => {
          if (!cancelled) {
            setCompare(null);
            setError(reason instanceof Error ? reason.message : "Unable to compare runs.");
          }
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    } else {
      Promise.all([
        getSpectrogram(recordingId),
        getGroundTruth(recordingId),
        getDetections(runAId),
      ])
        .then(([nextMeta, nextGroundTruth, nextA]) => {
          if (cancelled) return;
          setMeta(nextMeta);
          setGroundTruth(nextGroundTruth);
          setDetectionsA(nextA);
          setDetectionsB([]);
          setCompare(null);
          setSelectedCaseId(undefined);
        })
        .catch((reason: unknown) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : "Unable to load case inspection.");
        });
    }
    return () => {
      cancelled = true;
    };
  }, [recordingId, runAId, runBId]);

  const canCompare = Boolean(recordingId && runAId && runBId && runAId !== runBId);
  const singleRun = Boolean(recordingId && runAId && (!runBId || runBId === runAId));
  const selectedCase = compare?.cases.find((item) => item.groundTruthId === selectedCaseId);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={2} style={{ marginBottom: 4 }}>Algorithm Lab</Typography.Title>
        <Typography.Text type="secondary">Inspect one completed run or compare two completed runs on one Recording (localization matching IoU = 0.5).</Typography.Text>
      </div>

      <Card title="Experiment Setup">
        <Space wrap>
          <Select
            aria-label="Recording"
            placeholder="Select a Recording"
            style={{ width: 240 }}
            showSearch
            optionFilterProp="label"
            value={recordingId}
            onChange={(id: string) => onRecordingChange(id)}
            options={recordings.map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select
            aria-label="Run A"
            placeholder="Run A"
            style={{ width: 260 }}
            value={runAId}
            onChange={onRunAChange}
            options={runs.map((item) => ({ value: item.id, label: runLabel(item) }))}
          />
          <Typography.Text type="secondary">VS</Typography.Text>
          <Select
            aria-label="Run B"
            placeholder="Run B"
            style={{ width: 260 }}
            value={runBId}
            onChange={onRunBChange}
            options={runs.map((item) => ({ value: item.id, label: runLabel(item) }))}
          />
          <Button type="primary" disabled={!canCompare} loading={loading} onClick={() => void runCompare()}>
            Compare
          </Button>
        </Space>
        {selectedRecording ? (
          <div style={{ marginTop: 8 }}>
            <Typography.Text type="secondary">Recording:</Typography.Text>{" "}
            <Typography.Text>{selectedRecording.name}</Typography.Text>
          </div>
        ) : null}
      </Card>

      {error ? <Alert type="error" showIcon message={error} closable onClose={() => setError(null)} /> : null}

      {recordingId && runs.length < 2 ? (
        <Alert
          type="info"
          showIcon
          message="This recording has fewer than two completed AnalysisRuns to compare."
        />
      ) : null}

      {loading ? <Spin tip="Comparing runs..." /> : null}

      {singleRun && !compare ? (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="Select Run B to compare this result with another run."
          />
          {meta ? (
            <Row gutter={16}>
              <Col xs={24} lg={12}>
                <RunComparisonPanel
                  title={`Run A: ${runAId}`}
                  color="blue"
                  meta={meta}
                  groundTruth={groundTruth}
                  detections={detectionsA}
                />
              </Col>
            </Row>
          ) : null}
        </Space>
      ) : null}

      {compare && meta ? (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Row gutter={16}>
            <Col xs={24} lg={12}><RunMetricsCard run={compare.runA} side="A" /></Col>
            <Col xs={24} lg={12}><RunMetricsCard run={compare.runB} side="B" /></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} lg={12}>
              <RunComparisonPanel
                title={`Run A: ${compare.runA.pipelineName}`}
                color="blue"
                meta={meta}
                groundTruth={groundTruth}
                detections={detectionsA}
                selectedDetectionId={selectedCase?.runA.detectionId ?? undefined}
              />
            </Col>
            <Col xs={24} lg={12}>
              <RunComparisonPanel
                title={`Run B: ${compare.runB.pipelineName}`}
                color="green"
                meta={meta}
                groundTruth={groundTruth}
                detections={detectionsB}
                selectedDetectionId={selectedCase?.runB.detectionId ?? undefined}
              />
            </Col>
          </Row>
          <Card title="Case Comparison" size="small">
            <CaseComparisonTable cases={compare.cases} onSelectCase={setSelectedCaseId} />
          </Card>
        </Space>
      ) : null}

      {!singleRun && !compare && recordingId && !selectedRecording ? (
        <Spin tip="Loading recording..." />
      ) : null}
    </Space>
  );
}