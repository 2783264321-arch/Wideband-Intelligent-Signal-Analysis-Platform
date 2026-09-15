import { Alert, Button, Card, Checkbox, Col, Row, Select, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createAnalysisRun, getAnalysisRun, getDetections, getExecutorSelection, getGroundTruth, getRecording, getSpectrogram, listPipelines } from "../api/client";
import type { AnalysisRun, DetectionResult, ExecutorSelection, GroundTruthResult, PipelineDefinition, RecordingDetail, SpectrogramMeta } from "../api/types";
import { buildAnalysisRunRequest } from "../features/analysis-run/requestBuilder";
import { ExecutionEnvironmentSelector } from "../features/execution-environment/ExecutionEnvironmentSelector";
import { optionsFromSelection } from "../features/execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../features/execution-environment/types";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

const activeStatuses = new Set(["pending", "running"]);

function pipelineOptionLabel(item: PipelineDefinition): string {
  const base = `${item.name} · ${item.recommendedDevice}`;
  if (item.taskCapability === "detection_localization") {
    return `${base} · Detection & localization only`;
  }
  return base;
}

const SHORT_SHA_LENGTH = 8;

function RemoteRunSummary({ run }: { run: AnalysisRun }) {
  const metadata = run.executionMetadata ?? {};
  const hardware = run.hardwareInfo ?? {};
  const runtimeCommit = typeof metadata.required_remote_runtime_commit === "string"
    ? metadata.required_remote_runtime_commit.slice(0, SHORT_SHA_LENGTH)
    : null;
  const payloadSha = typeof metadata.payload_sha256 === "string"
    ? metadata.payload_sha256.slice(0, SHORT_SHA_LENGTH)
    : null;
  const deviceName = typeof hardware.device_name === "string" ? hardware.device_name : null;
  const deviceType = typeof hardware.device_type === "string" ? hardware.device_type : null;
  const remoteProfile = typeof metadata.remote_profile === "string" ? metadata.remote_profile : null;
  const startedAt = typeof metadata.remote_started_at === "string" ? metadata.remote_started_at : null;
  const finishedAt = typeof metadata.remote_finished_at === "string" ? metadata.remote_finished_at : null;

  return (
    <Space direction="vertical" size={4} data-testid="remote-run-summary">
      <Tag color="geekblue">Executor: {run.executor}</Tag>
      {remoteProfile ? <Typography.Text type="secondary">Profile: {remoteProfile}</Typography.Text> : null}
      {deviceName || deviceType ? (
        <Typography.Text type="secondary">
          Device: {deviceName ?? deviceType}{deviceName && deviceType ? ` (${deviceType})` : ""}
        </Typography.Text>
      ) : null}
      {runtimeCommit ? <Typography.Text type="secondary">Runtime commit: {runtimeCommit}</Typography.Text> : null}
      {payloadSha ? <Typography.Text type="secondary">Payload SHA: {payloadSha}</Typography.Text> : null}
      {startedAt || finishedAt ? (
        <Typography.Text type="secondary">
          Remote: {startedAt ?? "—"} → {finishedAt ?? "—"}
        </Typography.Text>
      ) : null}
    </Space>
  );
}

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
  // Backend execution-environment projection (sole authority). Never computed client-side.
  const [selection, setSelection] = useState<ExecutorSelection | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  // User's explicit execution environment value. Auto stays Auto across the request boundary.
  const [environment, setEnvironment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });

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

  // Execution environment selection for the SELECTED pipeline/recording only.
  // On change the stale selection is cleared immediately and an in-flight response
  // for the previous pipeline is ignored (active flag torn down). Auto is reset to
  // avoid carrying a manual choice across pipelines.
  useEffect(() => {
    setSelection(null);
    setSelectionError(null);
    setSelectionLoading(false);
    setEnvironment({ mode: "auto", executor: null });
    if (!recording || !pipelines.length) return undefined;
    let active = true;
    setSelectionLoading(true);
    void getExecutorSelection({
      scope: { kind: "recording", recordingId },
      pipelineId,
    })
      .then((result) => {
        if (!active) return;
        setSelection(result);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setSelectionError(reason instanceof Error ? reason.message : "Unable to load execution environments.");
      })
      .finally(() => {
        if (!active) return;
        setSelectionLoading(false);
      });
    return () => { active = false; };
  }, [recordingId, pipelineId, recording, pipelines]);

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
  const runActive = currentRun ? activeStatuses.has(currentRun.status) : false;

  const environmentOptions = useMemo(() => optionsFromSelection(selection), [selection]);
  const selectedEnvironmentOption = useMemo(
    () => environmentOptions.find((option) => option.key === (environment.mode === "auto" ? "auto" : environment.executor)) ?? null,
    [environmentOptions, environment.mode, environment.executor],
  );
  const canRun = !runActive && selectedEnvironmentOption?.enabled === true;

  const selectDetection = (id: string) => {
    setSelectedId(id);
    const next = new URLSearchParams(searchParams);
    next.set("selected", id);
    setSearchParams(next);
  };

  const runAnalysis = async () => {
    setError(null);
    if (!canRun) return;
    try {
      // Auto stays Auto; the backend resolves the concrete executor.
      const request = buildAnalysisRunRequest({ recordingId, pipelineId, environment });
      const run = await createAnalysisRun(request);
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
            style={{ width: 360 }}
            onChange={setPipelineId}
            options={pipelines.map((item) => ({ value: item.id, label: pipelineOptionLabel(item) }))}
          />
          <Button type="primary" loading={runActive} disabled={!canRun} onClick={() => void runAnalysis()}>
            {runActive ? "Analyzing..." : "Run Analysis"}
          </Button>
        </Space>
      </div>
      <ExecutionEnvironmentSelector
        selection={selection}
        loading={selectionLoading}
        error={selectionError}
        value={environment}
        onChange={setEnvironment}
        disabled={runActive}
      />
      <Space wrap>
        <Checkbox checked={showPredictions} onChange={(event) => setShowPredictions(event.target.checked)}>Prediction</Checkbox>
        <Checkbox checked={showGroundTruth} disabled={!groundTruth.length} onChange={(event) => setShowGroundTruth(event.target.checked)}>Ground Truth</Checkbox>
        {currentRun ? <Tag>{currentRun.status}</Tag> : <Typography.Text type="secondary">No AnalysisRun selected yet.</Typography.Text>}
        {currentRun?.status === "failed" ? <Typography.Text type="danger">{currentRun.errorMessage ?? "Analysis failed."}</Typography.Text> : null}
        {currentRun?.executor === "remote_gpu" ? <RemoteRunSummary run={currentRun} /> : null}
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
