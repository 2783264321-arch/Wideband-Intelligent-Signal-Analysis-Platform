import { Alert, Button, Card, Checkbox, Select, Space, Spin, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createAnalysisRun, getAnalysisRun, getDetections, getExecutorSelection, getGroundTruth, getRecording, getSpectrogram, listPipelines } from "../api/client";
import { toErrorText } from "../api/errors";
import type { AnalysisRun, DetectionResult, ExecutorSelection, GroundTruthResult, PipelineDefinition, RecordingDetail, SpectrogramMeta } from "../api/types";
import { buildAnalysisRunRequest } from "../features/analysis-run/requestBuilder";
import { RunStatusBadge } from "../features/analysis-run/RunStatusBadge";
import { ExportAnalysisRunButton } from "../features/analysis-bundle/ExportAnalysisRunButton";
import { useRunPolling } from "../features/analysis-run/useRunPolling";
import { useLocalization } from "../localization/useLocalization";
import type { MessageKey } from "../localization/types";
import { ExecutionEnvironmentSelector } from "../features/execution-environment/ExecutionEnvironmentSelector";
import { effectiveSelectionForScope, optionsFromSelection, scopeKeyFor, type BoundExecutorSelection } from "../features/execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../features/execution-environment/types";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

const activeStatuses = new Set(["pending", "running"]);

// Long recordings cannot show both "everything" and "fine detail" in one image, so
// the preview is either the full-duration overview or a shorter, higher-resolution
// slice. Slices are a fixed size so the selector stays predictable.
const OVERVIEW_VALUE = "overview";
const WINDOW_SLICE_S = 2;

interface TimeWindowOption {
  value: string;
  tStartS: number;
  tEndS: number;
}

function timeWindowOptions(durationS: number): TimeWindowOption[] {
  const options: TimeWindowOption[] = [{ value: OVERVIEW_VALUE, tStartS: 0, tEndS: durationS }];
  if (durationS <= WINDOW_SLICE_S * 1.5) return options;
  const count = Math.ceil(durationS / WINDOW_SLICE_S);
  for (let index = 0; index < count; index += 1) {
    const start = index * WINDOW_SLICE_S;
    const end = Math.min(start + WINDOW_SLICE_S, durationS);
    if (end - start < 0.05) continue;
    options.push({ value: `w${index}`, tStartS: start, tEndS: end });
  }
  return options;
}

function pipelineOptionLabel(item: PipelineDefinition, t: (key: MessageKey) => string): string {
  const base = `${item.name} · ${item.recommendedDevice}`;
  if (item.taskCapability === "detection_localization") {
    return `${base} · ${t("spectrum.capabilityDetectionLocalization")}`;
  }
  return base;
}

/**
 * User-friendly initial pipeline: prefer the built-in STFT Energy Detector,
 * then any detection/localization pipeline, then the first available pipeline.
 * Never keep a stale/absent selection, and never default to a placeholder.
 */
function preferredPipelineId(pipelines: PipelineDefinition[], current: string): string {
  if (current && pipelines.some((item) => item.id === current)) return current;
  const preferred =
    pipelines.find((item) => item.id === "stft_energy_detector") ??
    pipelines.find((item) => item.taskCapability === "detection_localization") ??
    pipelines[0];
  return preferred?.id ?? "";
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
  const [timeWindow, setTimeWindow] = useState<string>(OVERVIEW_VALUE);
  const [detections, setDetections] = useState<DetectionResult[]>([]);
  const [groundTruth, setGroundTruth] = useState<GroundTruthResult[]>([]);
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [pipelineId, setPipelineId] = useState("");
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
  // Execution is always automatic on this page; the backend resolves the executor.
  const [environment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });
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
        if (nextPipelines.length) setPipelineId((current) => preferredPipelineId(nextPipelines, current));
        const [nextGroundTruth, nextRun] = await Promise.all([
          nextRecording.hasGroundTruth ? getGroundTruth(recordingId) : Promise.resolve([]),
          runId ? getAnalysisRun(runId) : Promise.resolve(null),
        ]);
        if (!active) return;
        // Only a terminal run has stable results. A pending/running run is owned
        // by the polling lifecycle, so fetching here would race it with an empty
        // result set.
        const nextDetections =
          runId && nextRun?.status === "completed" ? await getDetections(runId) : [];
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

  // The initial load fetches the full-duration overview; any window change
  // (including switching back to the overview) refetches just the spectrogram.
  const windowOptions = useMemo(
    () => (recording ? timeWindowOptions(recording.durationS) : []),
    [recording],
  );
  const activeWindow = useMemo(
    () => windowOptions.find((option) => option.value === timeWindow) ?? null,
    [windowOptions, timeWindow],
  );
  const resolutionHint = useMemo(() => {
    const frames = spectrogram?.numFrames ?? 0;
    if (!spectrogram || frames <= 0) return null;
    const secondsPerColumn = (spectrogram.tEndS - spectrogram.tStartS) / frames;
    return t("spectrum.resolutionHint", { ms: (secondsPerColumn * 1000).toFixed(2) });
  }, [spectrogram, t]);
  useEffect(() => {
    if (!recording || !activeWindow) return undefined;
    // The initial load already fetched the overview, so skip the first redundant
    // request only when the shown spectrogram already covers the whole recording.
    const shown = spectrogram;
    const alreadyOverview =
      activeWindow.value === OVERVIEW_VALUE &&
      shown != null &&
      shown.tStartS <= 0 &&
      shown.tEndS >= recording.durationS;
    if (alreadyOverview) return undefined;
    let active = true;
    getSpectrogram(recordingId, { tStartS: activeWindow.tStartS, tEndS: activeWindow.tEndS })
      .then((next) => {
        if (active) setSpectrogram(next);
      })
      .catch((reason: unknown) => {
        if (active) setError(toErrorText(reason, t("spectrum.loadRecordingError")));
      });
    return () => { active = false; };
    // Deliberately not keyed on `spectrogram`: that would refetch right after we set it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingId, recording, activeWindow, t]);

  // Execution environment selection for the SELECTED pipeline/recording only.
  // On change the stale selection is cleared immediately and an in-flight response
  // for the previous pipeline is ignored (active flag torn down). Auto is reset to
  // avoid carrying a manual choice across pipelines.
  useEffect(() => {
    setBoundSelection(null);
    setSelectionError(null);
    setSelectionLoading(false);
    if (!recording || !pipelines.length || !pipelineId) return undefined;
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
  // Execution stays automatic; the environment is never a user-facing choice here.
  const noRunnableExecutor =
    !selectionLoading && effectiveSelection !== null && !environmentOptions.some((option) => option.enabled);

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
      <div>
        <Space style={{ marginBottom: 10 }}>
          <Button
            data-testid="spectrum-back-library"
            onClick={() =>
              navigate(
                recording.datasetId != null
                  ? "/data-library?tab=datasets"
                  : "/data-library?tab=standalone",
              )
            }
          >
            {t("common.backToLibrary")}
          </Button>
          <Button data-testid="spectrum-back" onClick={() => navigate(`/samples/${recordingId}`)}>
            {t("common.backTo")}
          </Button>
        </Space>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div>
            <Typography.Title level={3} style={{ margin: 0 }}>{recording.name}</Typography.Title>
            <Typography.Text type="secondary" style={{ display: "block" }}>
              Fs {(recording.sampleRateHz / 1e6).toFixed(3)} MHz · Fc {(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz · {recording.durationS.toFixed(6)} s
            </Typography.Text>
          </div>
          <Space wrap size="middle" align="end">
            <div>
              <Typography.Text type="secondary" style={{ display: "block", fontSize: 12 }}>{t("spectrum.pipelineLabel")}</Typography.Text>
              <Select
                value={pipelineId}
                style={{ width: 320 }}
                onChange={setPipelineId}
                options={pipelines.map((item) => ({ value: item.id, label: pipelineOptionLabel(item, t) }))}
              />
            </div>
            <Button type="primary" loading={runActive} disabled={!canRun} onClick={() => void runAnalysis()}>
              {runActive ? t("common.analyzing") : t("common.runAnalysis")}
            </Button>
          </Space>
        </div>
      </div>
      {/*
        Execution environment is an internal resolution detail: it is never shown
        as a selector. It only surfaces, compactly, when NOTHING can run locally,
        so a disabled Run button is never unexplained.
      */}
      {noRunnableExecutor ? (
        <ExecutionEnvironmentSelector
          selection={effectiveSelection}
          loading={selectionLoading}
          error={selectionError}
          value={environment}
          onChange={() => {}}
          disabled={runActive}
          showSelector={false}
        />
      ) : null}
      <Space wrap>
        <Checkbox checked={showPredictions} onChange={(event) => setShowPredictions(event.target.checked)}>{t("common.prediction")}</Checkbox>
        <Checkbox checked={showGroundTruth} disabled={!groundTruth.length} onChange={(event) => setShowGroundTruth(event.target.checked)}>{t("common.groundTruth")}</Checkbox>
        {windowOptions.length > 1 ? (
          <Space size={6} align="center">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t("spectrum.timeWindowLabel")}</Typography.Text>
            <Select
              data-testid="spectrum-time-window"
              size="small"
              value={timeWindow}
              style={{ width: 150 }}
              onChange={setTimeWindow}
              options={windowOptions.map((option) =>
                option.value === OVERVIEW_VALUE
                  ? { value: option.value, label: t("spectrum.timeWindowOverview") }
                  : {
                      value: option.value,
                      label: `${option.tStartS.toFixed(1)}-${option.tEndS.toFixed(1)} s`,
                    },
              )}
            />
            {resolutionHint ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{resolutionHint}</Typography.Text>
            ) : null}
          </Space>
        ) : null}
        {currentRun ? (
          <RunStatusBadge status={currentRun.status} errorType={currentRun.errorType} errorMessage={currentRun.errorMessage} />
        ) : <Typography.Text type="secondary">{t("common.noRunSelected")}</Typography.Text>}
        {currentRun ? (
          <ExportAnalysisRunButton runId={currentRun.id} status={currentRun.status} />
        ) : null}
      </Space>
      <div
        data-testid="spectrum-workspace"
        style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "stretch" }}
      >
        <Card data-testid="spectrum-viewer-pane" style={{ flex: "1 1 520px", minWidth: 0 }}>
          <SpectrogramViewer
            meta={spectrogram}
            detections={detections}
            showDetections={showPredictions}
            groundTruth={showGroundTruth ? groundTruth : []}
            selectedDetectionId={selectedId}
            onSelectDetection={selectDetection}
          />
          {selected ? <Typography.Text style={{ display: "block", marginTop: 12 }}>{t("spectrum.selected")}: {selected.className}</Typography.Text> : null}
        </Card>
        <Card
          data-testid="spectrum-results-pane"
          style={{ flex: "0 1 320px", minWidth: 240, maxWidth: 340, height: "auto" }}
        >
          <SignalResultsPanel
            detections={detections}
            selectedId={selectedId}
            onSelect={selectDetection}
            onViewDetails={(id) => currentRun && navigate(`/signals/${currentRun.id}/${id}`)}
            onViewAll={() => currentRun && navigate(`/signals/${currentRun.id}`)}
          />
        </Card>
      </div>
    </Space>
  );
}
