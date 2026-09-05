import { Alert, Button, Card, Col, Row, Select, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";
import { compareAnalysisRuns, getDetections, getGroundTruth, getSpectrogram, listAnalysisRuns, listRecordings } from "../api/client";
import type { AlgorithmLabCompareResponse, AnalysisRun, DetectionResult, GroundTruthResult, RecordingDetail, SpectrogramMeta } from "../api/types";
import { CaseComparisonTable } from "../features/algorithm-lab/CaseComparisonTable";
import { RunComparisonPanel } from "../features/algorithm-lab/RunComparisonPanel";
import { RunMetricsCard } from "../features/algorithm-lab/RunMetricsCard";

const runLabel = (run: AnalysisRun) => `${run.pipelineId} · ${run.id}`;

export function AlgorithmLabPage() {
  const [recordings, setRecordings] = useState<RecordingDetail[]>([]);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [recordingId, setRecordingId] = useState<string | undefined>();
  const [runAId, setRunAId] = useState<string | undefined>();
  const [runBId, setRunBId] = useState<string | undefined>();
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

  const onRecordingChange = async (id: string) => {
    setRecordingId(id);
    setRunAId(undefined);
    setRunBId(undefined);
    setCompare(null);
    setSelectedCaseId(undefined);
    setError(null);
    try {
      setRuns(await listAnalysisRuns(id));
    } catch (reason) {
      setRuns([]);
      setError(reason instanceof Error ? reason.message : "Unable to load analysis runs.");
    }
  };

  const canCompare = Boolean(recordingId && runAId && runBId && runAId !== runBId);

  const runCompare = async () => {
    if (!recordingId || !runAId || !runBId) return;
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

  const selectedCase = compare?.cases.find((item) => item.groundTruthId === selectedCaseId);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={2} style={{ marginBottom: 4 }}>Algorithm Lab</Typography.Title>
        <Typography.Text type="secondary">Compare two completed analysis runs on one recording against Ground Truth (IoU = 0.5).</Typography.Text>
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
            onChange={(id: string) => void onRecordingChange(id)}
            options={recordings.map((item) => ({ value: item.id, label: item.name }))}
          />
          <Select
            aria-label="Run A"
            placeholder="Run A"
            style={{ width: 260 }}
            value={runAId}
            onChange={setRunAId}
            options={runs.map((item) => ({ value: item.id, label: runLabel(item) }))}
          />
          <Typography.Text type="secondary">VS</Typography.Text>
          <Select
            aria-label="Run B"
            placeholder="Run B"
            style={{ width: 260 }}
            value={runBId}
            onChange={setRunBId}
            options={runs.map((item) => ({ value: item.id, label: runLabel(item) }))}
          />
          <Button type="primary" disabled={!canCompare} loading={loading} onClick={() => void runCompare()}>
            Compare
          </Button>
        </Space>
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
    </Space>
  );
}