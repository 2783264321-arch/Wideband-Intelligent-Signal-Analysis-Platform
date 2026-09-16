import { Alert, Button, Card, Checkbox, Col, Row, Select, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createAnalysisRun, getAnalysisRun, getDetections, getExecutorSelection, getGroundTruth, getRecording, getSpectrogram, listPipelines } from "../api/client";
import { toErrorText } from "../api/errors";
import type { AnalysisRun, DetectionResult, ExecutorSelection, GroundTruthResult, PipelineDefinition, RecordingDetail, SpectrogramMeta } from "../api/types";
import { buildAnalysisRunRequest } from "../features/analysis-run/requestBuilder";
import { RunProvenanceCard } from "../features/analysis-run/RunProvenanceCard";
import { RunStatusBadge } from "../features/analysis-run/RunStatusBadge";
import { useRunPolling } from "../features/analysis-run/useRunPolling";
import { useLocalization } from "../localization/useLocalization";
import type { MessageKey } from "../localization/types";
import { ExecutionEnvironmentSelector } from "../features/execution-environment/ExecutionEnvironmentSelector";
import { NoRunnableExecutorPanel } from "../features/execution-environment/NoRunnableExecutorPanel";
import { effectiveSelectionForScope, optionsFromSelection, scopeKeyFor, type BoundExecutorSelection } from "../features/execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../features/execution-environment/types";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

const activeStatuses = new Set(["pending", "running"]);

function pipelineOptionLabel(item: PipelineDefinition, t: (key: MessageKey) => string): string {
  const base = `${item.name} · ${item.recommendedDevice}`;
  if (item.taskCapability === "detection_localization") {
    return `${base} · ${t("spectrum.capabilityDetectionLocalization")}`;
  }
  return base;
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
  // Backend execution-environment projection (sole authority), bound to the exact
  // (recording, pipeline) scope identity that produced it.
  const selectionScopeKey = scopeKeyFor(recordingId, pipelineId);
  const [boundSelection, setBoundSelection] = useState<BoundExecutorSelection | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  // A stale selection (different scope) can never authorize the current request.
  const effectiveSelection = effectiveSelectionForScope(boundSelection, selectionScopeKey);
  // User's explicit execution environment value. Auto stays Auto across the request boundary.
  const [environment, setEnvironment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });
  const { t } = useLocalization();

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
        setError(toErrorText(reason, t("spectrum.loadRecordingError")));
      });
    return () => { active = false; };
  }, [recordingId, runId]);

  // Execution environment selection for the SELECTED pipeline/recording only.
  // On change the stale selection is cleared immediately and an in-flight response
  // for the previous pipeline is ignored (active flag torn down). Auto is reset to
  // avoid carrying a manual choice across pipelines.
  useEffect(() => {
    setBoundSelection(null);
    setSelectionError(null);
    setSelectionLoading(false);
    setEnvironment({ mode: "auto", executor: null });
    if (!recording || !pipelines.length) return undefined;
    const scopeKey = scopeKeyFor(recordingId, pipelineId);
    let active = true;
    setSelectionLoading(true);
    void getExecutorSelection({
      scope: { kind: "recording", recordingId },
      pipelineId,
    })
      .then((result) => {
        if (!active) return;
        setBoundSelection({ scopeKey, value: result });
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setSelectionError(toErrorText(reason, t("spectrum.loadExecutionEnvironmentsError")));
      })
      .finally(() => {
        if (!active) return;
        setSelectionLoading(false);
      });
    return () => { active = false; };
  }, [recordingId, pipelineId, recording, pipelines]);

  // Reusable, run-bound polling lifecycle. Only active while the run is non-terminal.
  useRunPolling({
    runId: currentRun !== null && activeStatuses.has(currentRun.status) ? currentRun.id : undefined,
    onRun: setCurrentRun,
    onDetections: setDetections,
    onError: (reason: unknown) => setError(toErrorText(reason, t("spectrum.pollRunError"))),
  });

  const selected = useMemo(() => detections.find((d) => d.id === selectedId), [detections, selectedId]);
  const runActive = currentRun ? activeStatuses.has(currentRun.status) : false;

  const environmentOptions = useMemo(() => optionsFromSelection(effectiveSelection), [effectiveSelection]);
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
      setError(toErrorText(reason, t("spectrum.startAnalysisError")));
    }
  };

  if (error && !recording) return <Alert type="error" showIcon message={t("spectrum.workspaceError")} description={error} />;
  if (!recording || !spectrogram) return <Spin tip={t("common.loadingRecording")} />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error ? <Alert type="error" showIcon message={t("spectrum.warning")} description={error} closable onClose={() => setError(null)} /> : null}
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
            options={pipelines.map((item) => ({ value: item.id, label: pipelineOptionLabel(item, t) }))}
          />
          <Button type="primary" loading={runActive} disabled={!canRun} onClick={() => void runAnalysis()}>
            {runActive ? t("common.analyzing") : t("common.runAnalysis")}
          </Button>
        </Space>
      </div>
      <ExecutionEnvironmentSelector
        selection={effectiveSelection}
        loading={selectionLoading}
        error={selectionError}
        value={environment}
        onChange={setEnvironment}
        disabled={runActive}
      />
      {!selectionLoading && selectionError === null && effectiveSelection !== null && !environmentOptions.some((option) => option.enabled) ? (
        <NoRunnableExecutorPanel selection={effectiveSelection} />
      ) : null}
      <Space wrap>
        <Checkbox checked={showPredictions} onChange={(event) => setShowPredictions(event.target.checked)}>{t("common.prediction")}</Checkbox>
        <Checkbox checked={showGroundTruth} disabled={!groundTruth.length} onChange={(event) => setShowGroundTruth(event.target.checked)}>{t("common.groundTruth")}</Checkbox>
        {currentRun ? (
          <RunStatusBadge status={currentRun.status} errorType={currentRun.errorType} errorMessage={currentRun.errorMessage} />
        ) : <Typography.Text type="secondary">{t("common.noRunSelected")}</Typography.Text>}
        {currentRun ? <RunProvenanceCard run={currentRun} /> : null}
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
            {selected ? <Typography.Text style={{ display: "block", marginTop: 12 }}>{t("spectrum.selected")}: {selected.className}</Typography.Text> : null}
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
