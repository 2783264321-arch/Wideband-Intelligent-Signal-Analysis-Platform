import { Alert, Button, Card, Checkbox, Col, Row, Select, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getDetections, getGroundTruth, getRecording, getSpectrogram } from "../api/client";
import type { DetectionResult, GroundTruthResult, RecordingDetail, SpectrogramMeta } from "../api/types";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

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
  const [showPredictions, setShowPredictions] = useState(true);
  const [showGroundTruth, setShowGroundTruth] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    Promise.all([getRecording(recordingId), getSpectrogram(recordingId)])
      .then(async ([nextRecording, nextSpectrogram]) => {
        if (!active) return;
        setRecording(nextRecording);
        setSpectrogram(nextSpectrogram);
        const [nextDetections, nextGroundTruth] = await Promise.all([
          runId ? getDetections(runId) : Promise.resolve([]),
          nextRecording.hasGroundTruth ? getGroundTruth(recordingId) : Promise.resolve([]),
        ]);
        if (!active) return;
        setDetections(nextDetections);
        setGroundTruth(nextGroundTruth);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Unable to load recording.");
      });
    return () => { active = false; };
  }, [recordingId, runId]);

  const selected = useMemo(() => detections.find((d) => d.id === selectedId), [detections, selectedId]);

  const selectDetection = (id: string) => {
    setSelectedId(id);
    const next = new URLSearchParams(searchParams);
    next.set("selected", id);
    setSearchParams(next);
  };

  if (error) return <Alert type="error" showIcon message="Unable to open spectrum workspace" description={error} />;
  if (!recording || !spectrogram) return <Spin tip="Loading recording and STFT spectrum..." />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center" }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>{recording.name}</Typography.Title>
          <Typography.Text type="secondary">
            Fs {(recording.sampleRateHz / 1e6).toFixed(3)} MHz · Fc {(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz · {recording.durationS.toFixed(6)} s
          </Typography.Text>
        </div>
        <Space wrap>
          <Select value="stft" style={{ width: 130 }} options={[{ value: "stft", label: "STFT" }]} />
          <Select defaultValue="dummy" style={{ width: 180 }} options={[{ value: "dummy", label: "Dummy Pipeline" }]} />
          <Button type="primary" disabled>Run Analysis</Button>
        </Space>
      </div>
      <Space>
        <Checkbox checked={showPredictions} onChange={(event) => setShowPredictions(event.target.checked)}>Prediction</Checkbox>
        <Checkbox checked={showGroundTruth} disabled={!groundTruth.length} onChange={(event) => setShowGroundTruth(event.target.checked)}>Ground Truth</Checkbox>
        {runId ? <Typography.Text type="secondary">Run: {runId}</Typography.Text> : <Typography.Text type="secondary">No AnalysisRun selected yet.</Typography.Text>}
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
              onViewDetails={(id) => runId && navigate(`/signals/${runId}/${id}`)}
              onViewAll={() => runId && navigate(`/signals/${runId}`)}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
