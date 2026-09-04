import { Alert, Button, Card, Checkbox, Col, Row, Select, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createAnalysisRun, getAnalysisRun, getDetections, getGroundTruth, getRecording, getSpectrogram, listPipelines } from "../api/client";
import type { AnalysisRun, DetectionResult, GroundTruthResult, PipelineDefinition, RecordingDetail, SpectrogramMeta } from "../api/types";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

const activeStatuses = new Set(["pending", "running"]);

export function SpectrumAnalysisPage() {
  const navigate = useNavigate();
  const { recordingId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run") ?? undefined;
  const initial = searchParams.get("selected") ?? undefined;
  const [selectedId, setSelectedId] = useState<string | undefined>(initial);
  const [recording, setRecording] = useState<RecordingDetail | null>(null);
  const [spectrogram, setSpectrogram] = useState<SpectrogramMeta | null>(null);
  const [detections, setDetections] = useState<DetectionResult[]>([]);
  const [groundTruth, setGroundTruth] = useState<GroundTruthResult[]>([]);
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [pipelineId, setPipelineId] = useState("dummy");
  const [currentRun, setCurrentRun] = useState<AnalysisRun | null>(null);
  const [showPredictions, setShowPredictions] = useState(true);
  const [showGroundTruth, setShowGroundTruth] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    Promise.all([getRecording(recordingId), getSpectrogram(recordingId), listPipelines()])
      .then(async ([nextRecording, nextSpectrogram, nextPipelines]) => {
        if (!active) return;
        setRecording(nextRecording);
        setSpectrogram(nextSpectrogram);
        setPipelines(nextPipelines);
        if (nextPipelines.length && !nextPipelines.some((item) => item.id === pipelineId)) setPipelineId(nextPipelines[0].id);
        const [nextDetections, nextGroundTruth, nextRun] = await Promise.all([
          runId ? getDetections(runId) : Promise.resolve([]),
          nextRecording.hasGroundTruth ? getGroundTruth(recordingId) : Promise.resolve([]),
          runId ? getAnalysisRun(runId) : Promise.resolve(null),
        ]);
        if (!active) return;
        setDetections(nextDetections);
        setGroundTruth(nextGroundTruth);
        setCurrentRun(nextRun);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load recording.");
      });
    return () => { active = false; };
  }, [recordingId, runId]);

  useEffect(() => {
    if (!currentRun || !activeStatuses.has(currentRun.status)) return undefined;
    const timer = window.setInterval(() => {
      void getAnalysisRun(currentRun.id)
        .then(async (nextRun) => {
          setCurrentRun(nextRun);
          if (nextRun.status === "completed") setDetections(await getDetections(nextRun.id));
        })
        .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Unable to poll analysis run."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [currentRun?.id, currentRun?.status]);

  const selected = useMemo(() => detections.find((d) => d.id === selectedId), [detections, selectedId]);
  const selectedPipeline = pipelines.find((item) => item.id === pipelineId);
  const runActive = currentRun ? activeStatuses.has(currentRun.status) : false;

  const selectDetection = (id: string) => {
    setSelectedId(id);
    const next = new URLSearchParams(searchParams);
    next.set("selected", id);
    setSearchParams(next);
  };

  const runAnalysis = async () => {
    setError(null);
    try {
      const run = await createAnalysisRun(recordingId, pipelineId);
      setCurrentRun(run);
      setDetections([]);
      setSelectedId(undefined);
      const next = new URLSearchParams(searchParams);
      next.set("run", run.id);
      next.delete("selected");
      setSearchParams(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to start analysis.");
    }
  };

  if (error && !recording) return <Alert type="error" showIcon message="Unable to open spectrum workspace" description={error} />;
  if (!recording || !spectrogram) return <Spin tip="Loading recording and STFT spectrum..." />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error ? <Alert type="error" showIcon message="Analysis warning" description={error} closable onClose={() => setError(null)} /> : null}
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center" }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>{recording.name}</Typography.Title>
          <Typography.Text type="secondary">
            Fs {(recording.sampleRateHz / 1e6).toFixed(3)} MHz · Fc {(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz · {recording.durationS.toFixed(6)} s
          </Typography.Text>
        </div>
        <Space wrap>
          <Select value="stft" style={{ width: 130 }} options={[{ value: "stft", label: "STFT" }]} />
          <Select
            value={pipelineId}
            style={{ width: 210 }}
            onChange={setPipelineId}
            options={pipelines.map((item) => ({ value: item.id, label: `${item.name} · ${item.recommendedDevice}` }))}
          />
          <Button type="primary" loading={runActive} disabled={!selectedPipeline?.cpuSupported || runActive} onClick={() => void runAnalysis()}>
            {runActive ? "Analyzing..." : "Run Analysis"}
          </Button>
        </Space>
      </div>
      <Space wrap>
        <Checkbox checked={showPredictions} onChange={(event) => setShowPredictions(event.target.checked)}>Prediction</Checkbox>
        <Checkbox checked={showGroundTruth} disabled={!groundTruth.length} onChange={(event) => setShowGroundTruth(event.target.checked)}>Ground Truth</Checkbox>
        {currentRun ? <Tag>{currentRun.status}</Tag> : <Typography.Text type="secondary">No AnalysisRun selected yet.</Typography.Text>}
        {currentRun?.status === "failed" ? <Typography.Text type="danger">{currentRun.errorMessage ?? "Analysis failed."}</Typography.Text> : null}
      </Space>
      <Row gutter={16} align="stretch">
        <Col xs={24} xl={18}>
          <Card>
            <SpectrogramViewer
              meta={spectrogram}
              detections={showPredictions ? detections : []}
              groundTruth={showGroundTruth ? groundTruth : []}
              selectedDetectionId={selectedId}
              onSelectDetection={selectDetection}
            />
            {selected ? <Typography.Text style={{ display: "block", marginTop: 12 }}>Selected: {selected.className}</Typography.Text> : null}
          </Card>
        </Col>
        <Col xs={24} xl={6}>
          <Card style={{ height: "100%" }}>
            <SignalResultsPanel
              detections={detections}
              selectedId={selectedId}
              onSelect={selectDetection}
              onViewDetails={(id) => currentRun && navigate(`/signals/${currentRun.id}/${id}`)}
              onViewAll={() => currentRun && navigate(`/signals/${currentRun.id}`)}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
