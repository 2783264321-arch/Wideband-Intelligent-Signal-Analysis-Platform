import type { DetectionResult, FFTData, GroundTruthResult, RecordingDetail, SpectrogramMeta, WaveformData } from "./types";
import type {
  DatasetBenchmarkAggregateMetrics,
  DatasetBenchmarkCompareResult,
  DatasetBenchmarkConfusion,
  DatasetBenchmarkPerClassMetric,
  DatasetBenchmarkRecordingComparison,
  DatasetEvaluation,
  DatasetEvaluationItem,
  DatasetEvaluationStatus,
  DatasetRecordingComparison,
  ImportedBatchResolution,
  ImportedBenchmarkBatch,
  OperatingMetrics,
} from "./types";
import type { ExecutionMode, ExecutionSelectionScope, ExecutorSelection, AnalysisRunCreateRequest } from "./types";
import type {
  DatasetAnalysisHistoryPage,
  DatasetPage,
  DatasetProjectionListPage,
  DatasetProjectionSummary,
  DatasetSamplePage,
  DatasetSummary,
  DeleteBlocker,
  StandaloneSamplePage,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path}`;
}

export interface PlatformApiErrorPayload {
  status: number;
  code: string;
  message: string;
  details: Record<string, unknown>;
}

/**
 * Structured platform API error that preserves the backend error contract
 * (`{ error: { code, message, details } }`). When the body is not a parseable
 * JSON error, `code`/`message` fall back to a generic HTTP status description.
 */
export class PlatformApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(payload: PlatformApiErrorPayload) {
    super(payload.message);
    this.name = "PlatformApiError";
    this.status = payload.status;
    this.code = payload.code;
    this.details = payload.details;
  }

  get display(): string {
    return `${this.code}: ${this.message}`;
  }
}

/**
 * Single structured-error seam: preserves the backend contract
 * (`{ error: { code, message, details } }`) or falls back to a generic HTTP
 * description. Used by JSON, multipart and compare request paths alike.
 */
async function structuredErrorFromResponse(response: Response): Promise<PlatformApiError> {
  let code = `HTTP_${response.status}`;
  let message = `API request failed: ${response.status}`;
  let details: Record<string, unknown> = {};
  try {
    const body = await response.json() as { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
    if (body.error && typeof body.error.code === "string" && typeof body.error.message === "string") {
      code = body.error.code;
      message = body.error.message;
      details = body.error.details ?? {};
    }
  } catch {
    // Non-JSON body: keep the generic HTTP fallback.
  }
  return new PlatformApiError({ status: response.status, code, message, details });
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  if (!response.ok) {
    throw await structuredErrorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path);
}

export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiDelete(path: string): Promise<void> {
  const response = await fetch(apiUrl(path), { method: "DELETE" });
  if (!response.ok) {
    throw await structuredErrorFromResponse(response);
  }
}

interface RecordingWire {
  id: string;
  name: string;
  data_format: string;
  source: string;
  external_path: string | null;
  sample_rate_hz: number;
  center_frequency_hz: number;
  frequency_low_hz: number;
  frequency_high_hz: number;
  num_samples: number;
  duration_s: number;
  dataset_name: string | null;
  dataset_split: string | null;
  label_space: string | null;
  has_ground_truth: boolean;
  dataset_id?: string | null;
  sample_key?: string | null;
}

interface SpectrogramWire {
  representation: "stft" | "ls-stft";
  image_url: string;
  t_start_s: number;
  t_end_s: number;
  f_low_hz: number;
  f_high_hz: number;
  num_frames?: number;
}

interface DetectionWire {
  id: string;
  run_id: string;
  recording_id: string;
  t_start_s: number;
  t_end_s: number;
  f_low_hz: number;
  f_high_hz: number;
  class_id: number;
  class_name: string;
  confidence: number;
  scores_json?: Record<string, number> | null;
}

interface GroundTruthWire {
  id: string;
  recording_id: string;
  t_start_s: number;
  t_end_s: number;
  f_low_hz: number;
  f_high_hz: number;
  class_id: number;
  class_name: string;
}

const mapRecording = (item: RecordingWire): RecordingDetail => ({
  id: item.id,
  name: item.name,
  dataFormat: item.data_format,
  source: item.source,
  externalPath: item.external_path,
  sampleRateHz: item.sample_rate_hz,
  centerFrequencyHz: item.center_frequency_hz,
  frequencyLowHz: item.frequency_low_hz,
  frequencyHighHz: item.frequency_high_hz,
  numSamples: item.num_samples,
  durationS: item.duration_s,
  datasetName: item.dataset_name,
  datasetSplit: item.dataset_split,
  labelSpace: item.label_space,
  hasGroundTruth: item.has_ground_truth,
  datasetId: item.dataset_id ?? null,
  sampleKey: item.sample_key ?? null,
});

const mapDetection = (item: DetectionWire): DetectionResult => ({
  id: item.id,
  runId: item.run_id,
  recordingId: item.recording_id,
  tStartS: item.t_start_s,
  tEndS: item.t_end_s,
  fLowHz: item.f_low_hz,
  fHighHz: item.f_high_hz,
  classId: item.class_id,
  className: item.class_name,
  confidence: item.confidence,
  scores: item.scores_json,
});

export interface RecordingPage {
  items: RecordingDetail[];
  total: number;
}

export async function listRecordings(limit = 100, offset = 0): Promise<RecordingPage> {
  const payload = await apiGet<{ items: RecordingWire[]; total: number }>(
    `/api/recordings?limit=${limit}&offset=${offset}`,
  );
  return { items: payload.items.map(mapRecording), total: payload.total };
}

export interface SpaceNetRegistrationSummary {
  created: number;
  skipped: number;
  invalid: number;
  total: number;
}

export async function registerSpaceNetDataset(datasetPath: string, split = "test"): Promise<SpaceNetRegistrationSummary> {
  return apiPostJson<SpaceNetRegistrationSummary>("/api/datasets/spacenet/register", {
    dataset_path: datasetPath,
    split,
  });
}

export async function importRecording(form: FormData): Promise<RecordingDetail> {
  const response = await fetch(apiUrl("/api/recordings"), { method: "POST", body: form });
  if (!response.ok) throw await structuredErrorFromResponse(response);
  return mapRecording(await response.json() as RecordingWire);
}

export interface DiscoveredLocalRuntime {
  pythonPath: string;
  available: boolean;
  version: string | null;
  torch: boolean;
  ultralytics: boolean;
  isControlPlane: boolean;
  isConfiguredLocalCpu: boolean;
}

/**
 * Operator diagnostic: which local interpreters can actually run pytorch
 * inference. Read-only, never identity material, safe to fail silently.
 */
export async function getDiscoveredLocalRuntimes(): Promise<DiscoveredLocalRuntime[]> {
  const wire = await apiGet<{ runtimes: Array<Record<string, unknown>> }>("/api/runtime-discovery");
  return wire.runtimes.map((item) => ({
    pythonPath: String(item.python_path ?? ""),
    available: Boolean(item.available),
    version: typeof item.version === "string" ? item.version : null,
    torch: Boolean(item.torch),
    ultralytics: Boolean(item.ultralytics),
    isControlPlane: Boolean(item.is_control_plane),
    isConfiguredLocalCpu: Boolean(item.is_configured_local_cpu),
  }));
}

export interface RegisterRecordingPathRequest {
  path: string;
  name: string;
  dataFormat: string;
  sampleRateHz: number;
  centerFrequencyHz: number;
  labelSpace?: string | null;
}

/** Register an existing on-host IQ file WITHOUT copying it into WISA storage. */
export async function registerRecordingPath(
  request: RegisterRecordingPathRequest,
): Promise<RecordingDetail> {
  const wire: Record<string, unknown> = {
    path: request.path,
    name: request.name,
    data_format: request.dataFormat,
    sample_rate_hz: request.sampleRateHz,
    center_frequency_hz: request.centerFrequencyHz,
  };
  if (request.labelSpace != null && request.labelSpace !== "") wire.label_space = request.labelSpace;
  return mapRecording(await apiPostJson<RecordingWire>("/api/recordings/register-path", wire));
}

export async function getRecording(recordingId: string): Promise<RecordingDetail> {
  return mapRecording(await apiGet<RecordingWire>(`/api/recordings/${recordingId}`));
}

export async function getSpectrogram(
  recordingId: string,
  window?: { tStartS: number; tEndS: number },
): Promise<SpectrogramMeta> {
  const query = new URLSearchParams({ representation: "stft" });
  if (window) {
    query.set("t_start_s", String(window.tStartS));
    query.set("t_end_s", String(window.tEndS));
  }
  const item = await apiGet<SpectrogramWire>(`/api/recordings/${recordingId}/spectrogram?${query}`);
  return {
    representation: item.representation,
    imageUrl: apiUrl(item.image_url),
    tStartS: item.t_start_s,
    tEndS: item.t_end_s,
    fLowHz: item.f_low_hz,
    fHighHz: item.f_high_hz,
    numFrames: item.num_frames ?? 0,
  };
}

export async function getDetections(runId: string): Promise<DetectionResult[]> {
  return (await apiGet<DetectionWire[]>(`/api/analysis-runs/${runId}/detections`)).map(mapDetection);
}

export async function getDetection(detectionId: string): Promise<DetectionResult> {
  return mapDetection(await apiGet<DetectionWire>(`/api/detections/${detectionId}`));
}

export async function getGroundTruth(recordingId: string): Promise<GroundTruthResult[]> {
  const items = await apiGet<GroundTruthWire[]>(`/api/recordings/${recordingId}/ground-truth`);
  return items.map((item) => ({
    id: item.id,
    recordingId: item.recording_id,
    tStartS: item.t_start_s,
    tEndS: item.t_end_s,
    fLowHz: item.f_low_hz,
    fHighHz: item.f_high_hz,
    classId: item.class_id,
    className: item.class_name,
  }));
}

export async function getWaveform(recordingId: string, tStartS: number, tEndS: number, maxPoints = 4000): Promise<WaveformData> {
  const query = new URLSearchParams({ t_start_s: String(tStartS), t_end_s: String(tEndS), max_points: String(maxPoints) });
  const item = await apiGet<{ time_s: number[]; i: number[]; q: number[] }>(`/api/recordings/${recordingId}/waveform?${query}`);
  return { timeS: item.time_s, i: item.i, q: item.q };
}

export async function getSpectrum(
  recordingId: string,
  params: { fftSize?: number; segments?: number } = {},
): Promise<import("./types").SpectrumData> {
  const query = new URLSearchParams();
  if (params.fftSize != null) query.set("fft_size", String(params.fftSize));
  if (params.segments != null) query.set("segments", String(params.segments));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const item = await apiGet<{ frequency_hz: number[]; power_db: number[]; fft_size: number; segment_count: number }>(
    `/api/recordings/${encodeURIComponent(recordingId)}/spectrum${suffix}`,
  );
  return {
    frequencyHz: item.frequency_hz,
    powerDb: item.power_db,
    fftSize: item.fft_size,
    segmentCount: item.segment_count,
  };
}

export async function getFFT(detectionId: string, maxPoints = 2048): Promise<FFTData> {
  const item = await apiGet<{ frequency_hz: number[]; magnitude_db: number[] }>(`/api/detections/${detectionId}/fft?max_points=${maxPoints}`);
  return { frequencyHz: item.frequency_hz, magnitudeDb: item.magnitude_db };
}

interface PipelineDefinitionWire {
  id: string;
  name: string;
  version: string;
  label_space: string;
  recommended_device: string;
  cpu_supported: boolean;
  executors_supported: string[];
  recommended_executor: string | null;
  stages: string[];
  inspectable_stages: string[];
  task_capability: string;
  plugin_api_version?: number;
  output_label_space?: string;
  input_compatibility?: string[];
  dataset_adapters?: string[];
  model_release_required?: boolean;
  technical_execution_capabilities?: {
    executor: string;
    device_type: string;
    precision: string;
  }[];
  recommended_execution?: string | null;
}

interface AnalysisRunWire {
  id: string;
  recording_id: string;
  pipeline_id: string;
  pipeline_version: string;
  executor: string;
  status: import("./types").AnalysisRunStatus;
  parameters_json: Record<string, unknown>;
  hardware_info_json?: Record<string, unknown> | null;
  execution_metadata_json?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_type?: string | null;
  error_message?: string | null;
  worker_pid?: number | null;
  created_at?: string | null;
}

function mapAnalysisRun(item: AnalysisRunWire): import("./types").AnalysisRun {
  return {
    id: item.id,
    recordingId: item.recording_id,
    pipelineId: item.pipeline_id,
    pipelineVersion: item.pipeline_version,
    executor: item.executor,
    status: item.status,
    parameters: item.parameters_json,
    hardwareInfo: item.hardware_info_json,
    executionMetadata: item.execution_metadata_json,
    startedAt: item.started_at,
    finishedAt: item.finished_at,
    errorType: item.error_type,
    errorMessage: item.error_message,
    workerPid: item.worker_pid,
    createdAt: item.created_at,
  };
}

export async function listPipelines(): Promise<import("./types").PipelineDefinition[]> {
  const items = await apiGet<PipelineDefinitionWire[]>("/api/pipelines");
  return items.map((item) => ({
    id: item.id,
    name: item.name,
    version: item.version,
    labelSpace: item.label_space,
    recommendedDevice: item.recommended_device,
    cpuSupported: item.cpu_supported,
    executorsSupported: item.executors_supported,
    recommendedExecutor: item.recommended_executor,
    stages: item.stages,
    inspectableStages: item.inspectable_stages,
    taskCapability: item.task_capability,
    pluginApiVersion: item.plugin_api_version,
    outputLabelSpace: item.output_label_space,
    inputCompatibility: item.input_compatibility,
    datasetAdapters: item.dataset_adapters,
    modelReleaseRequired: item.model_release_required,
    technicalExecutionCapabilities: item.technical_execution_capabilities?.map((capability) => ({
      executor: capability.executor,
      deviceType: capability.device_type,
      precision: capability.precision,
    })),
    recommendedExecution: item.recommended_execution,
  }));
}

interface ExecutorAvailabilityWire {
  executor: string;
  available: boolean;
  reason_code: string | null;
  reason_message: string | null;
  remote_profile: string | null;
  recommended: boolean;
}

export async function getExecutorAvailability(
  recordingId: string,
  pipelineId: string,
): Promise<import("./types").ExecutorAvailability> {
  const item = await apiGet<ExecutorAvailabilityWire>(
    `/api/executor-availability?recording_id=${encodeURIComponent(recordingId)}&pipeline_id=${encodeURIComponent(pipelineId)}`,
  );
  return {
    executor: item.executor,
    available: item.available,
    reasonCode: item.reason_code,
    reasonMessage: item.reason_message,
    remoteProfile: item.remote_profile,
    recommended: item.recommended,
  };
}

interface ExecutionCandidateWire {
  executor: string;
  technical: boolean;
  configured: boolean;
  certified: boolean;
  available: boolean;
  reason_code: string | null;
  reason_message: string | null;
}

interface ExecutorSelectionWire {
  requested_mode: string;
  resolved_executor: string | null;
  reason_code: string;
  reason: string;
  workload_class: string;
  candidates: ExecutionCandidateWire[];
}

/**
 * Compile-time scope safety comes from the ExecutionSelectionScope discriminated
 * union. This runtime guard covers malformed JS/casts before any request is built.
 */
function assertSelectionScope(scope: ExecutionSelectionScope): void {
  if (scope === null || typeof scope !== "object") {
    throw new Error("Execution selection scope is required.");
  }
  if (scope.kind === "recording") {
    if ("datasetName" in scope || "datasetSplit" in scope || "datasetLabelSpace" in scope) {
      throw new Error("Execution selection scope is invalid (mixed recording/dataset scope).");
    }
    if (typeof scope.recordingId !== "string" || scope.recordingId.length === 0) {
      throw new Error("Recording scope requires a non-empty recordingId.");
    }
    return;
  }
  if (scope.kind === "dataset_id") {
    if ("recordingId" in scope) {
      throw new Error("Execution selection scope is invalid (mixed recording/dataset-id scope).");
    }
    if (typeof scope.datasetId !== "string" || scope.datasetId.length === 0) {
      throw new Error("Dataset-id scope requires a non-empty datasetId.");
    }
    return;
  }
  if (scope.kind === "dataset") {
    if ("recordingId" in scope) {
      throw new Error("Execution selection scope is invalid (mixed recording/dataset scope).");
    }
    if (
      typeof scope.datasetName !== "string" || scope.datasetName.length === 0 ||
      typeof scope.datasetSplit !== "string" || scope.datasetSplit.length === 0 ||
      typeof scope.datasetLabelSpace !== "string" || scope.datasetLabelSpace.length === 0
    ) {
      throw new Error(
        "Dataset scope requires non-empty datasetName, datasetSplit and datasetLabelSpace.",
      );
    }
    return;
  }
  if (scope.kind === "dataset_projection") {
    if ("recordingId" in scope) {
      throw new Error("Execution selection scope is invalid (mixed recording/dataset-projection scope).");
    }
    if (typeof scope.datasetProjectionId !== "string" || scope.datasetProjectionId.length === 0) {
      throw new Error("Dataset projection scope requires a non-empty datasetProjectionId.");
    }
    return;
  }
  throw new Error("Execution selection scope is invalid (mixed or unknown scope).");
}

export async function getExecutorSelection(params: {
  scope: ExecutionSelectionScope;
  pipelineId: string;
  modelReleaseId?: string | null;
}): Promise<ExecutorSelection> {
  assertSelectionScope(params.scope);
  const query = new URLSearchParams();
  if (params.scope.kind === "recording") {
    query.set("recording_id", params.scope.recordingId);
  } else if (params.scope.kind === "dataset_id") {
    query.set("dataset_id", params.scope.datasetId);
  } else if (params.scope.kind === "dataset_projection") {
    query.set("dataset_projection_id", params.scope.datasetProjectionId);
  } else {
    query.set("dataset_name", params.scope.datasetName);
    query.set("dataset_split", params.scope.datasetSplit);
    query.set("dataset_label_space", params.scope.datasetLabelSpace);
  }
  query.set("pipeline_id", params.pipelineId);
  if (params.modelReleaseId != null) {
    query.set("model_release_id", params.modelReleaseId);
  }
  const item = await apiGet<ExecutorSelectionWire>(`/api/executor-selection?${query.toString()}`);
  return {
    requestedMode: item.requested_mode as ExecutionMode,
    resolvedExecutor: item.resolved_executor,
    reasonCode: item.reason_code,
    reason: item.reason,
    workloadClass: item.workload_class,
    candidates: item.candidates.map((candidate) => ({
      executor: candidate.executor,
      technical: candidate.technical,
      configured: candidate.configured,
      certified: candidate.certified,
      available: candidate.available,
      reasonCode: candidate.reason_code,
      reasonMessage: candidate.reason_message,
    })),
  };
}

interface AnalysisRunCreateWire {
  recording_id: string;
  pipeline_id: string;
  executor?: string;
  execution_mode?: ExecutionMode;
  model_release_id?: string | null;
  parameters: Record<string, unknown>;
}

export async function createAnalysisRun(
  request: AnalysisRunCreateRequest,
): Promise<import("./types").AnalysisRun> {
  const wire: AnalysisRunCreateWire = {
    recording_id: request.recordingId,
    pipeline_id: request.pipelineId,
    parameters: request.parameters,
  };
  if (request.executor !== undefined) wire.executor = request.executor;
  if (request.executionMode !== undefined) wire.execution_mode = request.executionMode;
  if (request.modelReleaseId !== undefined) wire.model_release_id = request.modelReleaseId;
  return mapAnalysisRun(await apiPostJson<AnalysisRunWire>("/api/analysis-runs", wire));
}

export async function getAnalysisRun(runId: string): Promise<import("./types").AnalysisRun> {
  return mapAnalysisRun(await apiGet<AnalysisRunWire>(`/api/analysis-runs/${runId}`));
}

export async function listAnalysisRuns(
  recordingId: string,
  status: import("./types").AnalysisRunStatus | null = "completed",
): Promise<import("./types").AnalysisRun[]> {
  const base = `/api/analysis-runs?recording_id=${encodeURIComponent(recordingId)}`;
  const query = status === null ? base : `${base}&status=${encodeURIComponent(status)}`;
  const items = await apiGet<AnalysisRunWire[]>(query);
  return items.map(mapAnalysisRun);
}

interface DatasetExperimentWire {
  id: string;
  name: string;
  dataset_name: string;
  dataset_split: string;
  dataset_label_space: string;
  dataset_projection_id: string | null;
  dataset_id?: string | null;
  recording_manifest_hash: string;
  plugin_id: string;
  plugin_version: string;
  model_release_id: string | null;
  asset_manifest_sha256: string | null;
  parameters_json: Record<string, unknown>;
  executor: string;
  evaluation_protocol: string;
  max_concurrency: number;
  status: string;
  dataset_evaluation_id: string | null;
  error_type: string | null;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  requested_execution_mode?: string | null;
  auto_reason_code?: string | null;
  auto_reason?: string | null;
  workload_class?: string | null;
  expected_items?: number;
  queued_items?: number;
  running_items?: number;
  completed_items?: number;
  failed_items?: number;
  attempt_count?: number;
}

interface DatasetExperimentItemWire {
  id: string;
  experiment_id: string;
  manifest_order: number;
  recording_id: string;
  recording_name: string;
  status: string;
  last_error_type: string | null;
  last_error_message: string | null;
  latest_analysis_run_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface DatasetExperimentAttemptWire {
  id: string;
  experiment_item_id: string;
  attempt_number: number;
  analysis_run_id: string;
  launch_requested_at: string | null;
  created_at: string | null;
}

interface DatasetExperimentCreateWire {
  name: string;
  dataset_name?: string;
  dataset_split?: string;
  dataset_label_space?: string;
  dataset_projection_id?: string | null;
  dataset_id?: string | null;
  plugin_id: string;
  plugin_version: string;
  execution_mode: ExecutionMode;
  executor?: string;
  model_release_id?: string | null;
  parameters: Record<string, unknown>;
  evaluation_protocol?: string;
  max_concurrency: number;
}

function mapDatasetExperiment(item: DatasetExperimentWire): import("./types").DatasetExperiment {
  return {
    id: item.id,
    name: item.name,
    datasetName: item.dataset_name,
    datasetSplit: item.dataset_split,
    datasetLabelSpace: item.dataset_label_space,
    datasetProjectionId: item.dataset_projection_id,
    datasetId: item.dataset_id ?? null,
    recordingManifestHash: item.recording_manifest_hash,
    pluginId: item.plugin_id,
    pluginVersion: item.plugin_version,
    modelReleaseId: item.model_release_id,
    assetManifestSha256: item.asset_manifest_sha256,
    parameters: item.parameters_json,
    executor: item.executor,
    evaluationProtocol: item.evaluation_protocol,
    maxConcurrency: item.max_concurrency,
    status: item.status,
    datasetEvaluationId: item.dataset_evaluation_id,
    errorType: item.error_type,
    errorMessage: item.error_message,
    requestedExecutionMode: (item.requested_execution_mode ?? null) as ExecutionMode | null,
    autoReasonCode: item.auto_reason_code ?? null,
    autoReason: item.auto_reason ?? null,
    workloadClass: item.workload_class ?? null,
    expectedItems: item.expected_items ?? 0,
    queuedItems: item.queued_items ?? 0,
    runningItems: item.running_items ?? 0,
    completedItems: item.completed_items ?? 0,
    failedItems: item.failed_items ?? 0,
    attemptCount: item.attempt_count ?? 0,
    createdAt: item.created_at,
    startedAt: item.started_at,
    completedAt: item.completed_at,
  };
}

function mapDatasetExperimentItem(item: DatasetExperimentItemWire): import("./types").DatasetExperimentItem {
  return {
    id: item.id,
    experimentId: item.experiment_id,
    manifestOrder: item.manifest_order,
    recordingId: item.recording_id,
    recordingName: item.recording_name,
    status: item.status,
    lastErrorType: item.last_error_type,
    lastErrorMessage: item.last_error_message,
    latestAnalysisRunId: item.latest_analysis_run_id,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

function mapDatasetExperimentAttempt(item: DatasetExperimentAttemptWire): import("./types").DatasetExperimentAttempt {
  return {
    id: item.id,
    experimentItemId: item.experiment_item_id,
    attemptNumber: item.attempt_number,
    analysisRunId: item.analysis_run_id,
    launchRequestedAt: item.launch_requested_at,
    createdAt: item.created_at,
  };
}

export async function listDatasetExperiments(
  datasetId?: string,
): Promise<import("./types").DatasetExperiment[]> {
  const suffix = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : "";
  const items = await apiGet<DatasetExperimentWire[]>(`/api/dataset-experiments${suffix}`);
  return items.map(mapDatasetExperiment);
}

export async function getDatasetExperiment(id: string): Promise<import("./types").DatasetExperiment> {
  return mapDatasetExperiment(await apiGet<DatasetExperimentWire>(`/api/dataset-experiments/${id}`));
}

export async function createDatasetExperiment(
  request: import("./types").DatasetExperimentCreateRequest,
): Promise<import("./types").DatasetExperiment> {
  const wire: DatasetExperimentCreateWire = {
    name: request.name,
    plugin_id: request.pluginId,
    plugin_version: request.pluginVersion,
    execution_mode: request.executionMode,
    parameters: request.parameters,
    max_concurrency: request.maxConcurrency,
  };
  if (request.datasetName !== undefined) wire.dataset_name = request.datasetName;
  if (request.datasetSplit !== undefined) wire.dataset_split = request.datasetSplit;
  if (request.datasetLabelSpace !== undefined) wire.dataset_label_space = request.datasetLabelSpace;
  if (request.datasetProjectionId != null) wire.dataset_projection_id = request.datasetProjectionId;
  if (request.datasetId != null) wire.dataset_id = request.datasetId;
  if (request.executor !== undefined) wire.executor = request.executor;
  if (request.modelReleaseId !== undefined) wire.model_release_id = request.modelReleaseId;
  if (request.evaluationProtocol !== undefined) wire.evaluation_protocol = request.evaluationProtocol;
  return mapDatasetExperiment(await apiPostJson<DatasetExperimentWire>("/api/dataset-experiments", wire));
}

export async function runDatasetExperiment(id: string): Promise<import("./types").DatasetExperiment> {
  return mapDatasetExperiment(await apiPostJson<DatasetExperimentWire>(`/api/dataset-experiments/${id}/run`, {}));
}

export async function retryFailedDatasetExperimentItems(id: string): Promise<import("./types").DatasetExperiment> {
  return mapDatasetExperiment(await apiPostJson<DatasetExperimentWire>(`/api/dataset-experiments/${id}/retry-failed`, {}));
}

export async function retryDatasetExperimentEvaluation(id: string): Promise<import("./types").DatasetExperiment> {
  return mapDatasetExperiment(await apiPostJson<DatasetExperimentWire>(`/api/dataset-experiments/${id}/retry-evaluation`, {}));
}

export async function listDatasetExperimentItems(id: string): Promise<import("./types").DatasetExperimentItem[]> {
  const items = await apiGet<DatasetExperimentItemWire[]>(`/api/dataset-experiments/${id}/items`);
  return items.map(mapDatasetExperimentItem);
}

export async function listDatasetExperimentItemAttempts(
  experimentId: string,
  itemId: string,
): Promise<import("./types").DatasetExperimentAttempt[]> {
  const items = await apiGet<DatasetExperimentAttemptWire[]>(
    `/api/dataset-experiments/${experimentId}/items/${itemId}/attempts`,
  );
  return items.map(mapDatasetExperimentAttempt);
}

interface CompareWire {
  recording_id: string;
  iou_threshold: number;
  run_a: RunComparisonWire;
  run_b: RunComparisonWire;
  cases: CaseWire[];
}

interface RunComparisonWire {
  run_id: string;
  pipeline_id: string;
  pipeline_name: string;
  metrics: {
    tp: number;
    fp: number;
    fn: number;
    precision: number;
    recall: number;
    f1: number;
    mean_matched_iou: number | null;
  };
  classification_applicable: boolean;
  classification_reason: string | null;
  classification: {
    matched_count: number;
    class_correct: number;
    class_wrong: number;
    matched_accuracy: number | null;
    confusions: {
      gt_class_id: number;
      gt_class_name: string;
      pred_class_id: number;
      pred_class_name: string;
      count: number;
    }[];
  } | null;
  class_aware: {
    tp: number;
    fp: number;
    fn: number;
    precision: number;
    recall: number;
    f1: number;
  } | null;
}

interface CaseWire {
  ground_truth_id: string;
  class_id: number;
  class_name: string;
  bbox: { t_start_s: number; t_end_s: number; f_low_hz: number; f_high_hz: number };
  comparison: import("./types").ComparisonState;
  run_a: RunMatchWire;
  run_b: RunMatchWire;
}

interface RunMatchWire {
  matched: boolean;
  detection_id: string | null;
  iou: number | null;
  class_id: number | null;
  class_name: string | null;
  confidence: number | null;
  class_correct: boolean | null;
  bbox: { t_start_s: number; t_end_s: number; f_low_hz: number; f_high_hz: number } | null;
}

function mapCompare(wire: CompareWire): import("./types").AlgorithmLabCompareResponse {
  const mapMatch = (item: RunMatchWire): import("./types").RunMatchState => ({
    matched: item.matched,
    detectionId: item.detection_id,
    iou: item.iou,
    classId: item.class_id,
    className: item.class_name,
    confidence: item.confidence,
    classCorrect: item.class_correct,
    bbox: item.bbox ? {
      tStartS: item.bbox.t_start_s,
      tEndS: item.bbox.t_end_s,
      fLowHz: item.bbox.f_low_hz,
      fHighHz: item.bbox.f_high_hz,
    } : null,
  });
  const mapRun = (item: RunComparisonWire): import("./types").RunComparison => ({
    runId: item.run_id,
    pipelineId: item.pipeline_id,
    pipelineName: item.pipeline_name,
    metrics: {
      tp: item.metrics.tp,
      fp: item.metrics.fp,
      fn: item.metrics.fn,
      precision: item.metrics.precision,
      recall: item.metrics.recall,
      f1: item.metrics.f1,
      meanMatchedIou: item.metrics.mean_matched_iou,
    },
    classificationApplicable: item.classification_applicable,
    classificationReason: item.classification_reason,
    classification: item.classification ? {
      matchedCount: item.classification.matched_count,
      classCorrect: item.classification.class_correct,
      classWrong: item.classification.class_wrong,
      matchedAccuracy: item.classification.matched_accuracy,
      confusions: item.classification.confusions.map((c) => ({
        gtClassId: c.gt_class_id,
        gtClassName: c.gt_class_name,
        predClassId: c.pred_class_id,
        predClassName: c.pred_class_name,
        count: c.count,
      })),
    } : null,
    classAware: item.class_aware ? {
      tp: item.class_aware.tp,
      fp: item.class_aware.fp,
      fn: item.class_aware.fn,
      precision: item.class_aware.precision,
      recall: item.class_aware.recall,
      f1: item.class_aware.f1,
    } : null,
  });
  return {
    recordingId: wire.recording_id,
    iouThreshold: wire.iou_threshold,
    runA: mapRun(wire.run_a),
    runB: mapRun(wire.run_b),
    cases: wire.cases.map((item) => ({
      groundTruthId: item.ground_truth_id,
      classId: item.class_id,
      className: item.class_name,
      bbox: {
        tStartS: item.bbox.t_start_s,
        tEndS: item.bbox.t_end_s,
        fLowHz: item.bbox.f_low_hz,
        fHighHz: item.bbox.f_high_hz,
      },
      comparison: item.comparison,
      runA: mapMatch(item.run_a),
      runB: mapMatch(item.run_b),
    })),
  };
}

export async function compareAnalysisRuns(payload: {
  recordingId: string;
  runAId: string;
  runBId: string;
}): Promise<import("./types").AlgorithmLabCompareResponse> {
  const response = await fetch(apiUrl("/api/algorithm-lab/compare"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recording_id: payload.recordingId,
      run_a_id: payload.runAId,
      run_b_id: payload.runBId,
      iou_threshold: 0.5,
    }),
  });
  if (!response.ok) throw await structuredErrorFromResponse(response);
  return mapCompare(await response.json() as CompareWire);
}

export async function importAnalysisPackage(recordingId: string, file: File): Promise<import("./types").AnalysisRun> {
  const body = new FormData();
  body.append("recording_id", recordingId);
  body.append("file", file);
  const response = await fetch(apiUrl("/api/imported-runs"), { method: "POST", body });
  if (!response.ok) throw await structuredErrorFromResponse(response);
  return mapAnalysisRun(await response.json() as AnalysisRunWire);
}

interface BatchRunMappingWire {
  recording_id: string;
  recording_name: string;
  analysis_run_id: string;
}

interface BatchImportSummaryWire {
  batch_id: string;
  import_fingerprint: string;
  archive_sha256: string;
  dataset_name: string;
  dataset_split: string;
  pipeline_id: string;
  pipeline_version: string;
  label_space: string;
  item_count: number;
  detection_count: number;
  already_imported: boolean;
  created_runs: number;
  existing_runs: number;
  created_detections: number;
  matched_recordings: number;
  missing_recordings: number;
  ambiguous_recordings: number;
  fingerprint_mismatches: number;
  recording_run_mapping: BatchRunMappingWire[];
}

/** Snake_case wire -> camelCase domain mapping for the batch import summary. */
function mapBatchImportSummary(item: BatchImportSummaryWire): import("./types").BatchImportSummary {
  return {
    batchId: item.batch_id,
    importFingerprint: item.import_fingerprint,
    archiveSha256: item.archive_sha256,
    datasetName: item.dataset_name,
    datasetSplit: item.dataset_split,
    pipelineId: item.pipeline_id,
    pipelineVersion: item.pipeline_version,
    labelSpace: item.label_space,
    itemCount: item.item_count,
    detectionCount: item.detection_count,
    alreadyImported: item.already_imported,
    createdRuns: item.created_runs,
    existingRuns: item.existing_runs,
    createdDetections: item.created_detections,
    matchedRecordings: item.matched_recordings,
    missingRecordings: item.missing_recordings,
    ambiguousRecordings: item.ambiguous_recordings,
    fingerprintMismatches: item.fingerprint_mismatches,
    recordingRunMapping: item.recording_run_mapping.map((row) => ({
      recordingId: row.recording_id,
      recordingName: row.recording_name,
      analysisRunId: row.analysis_run_id,
    })),
  };
}

/**
 * Import a Batch Analysis Package (BAPv1) ZIP via the existing production
 * endpoint. Presentation-only seam: no SSH credentials, no server job controls.
 */
export async function importBatchRun(file: File): Promise<import("./types").BatchImportSummary> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(apiUrl("/api/imported-runs/batch"), { method: "POST", body });
  if (!response.ok) throw await structuredErrorFromResponse(response);
  return mapBatchImportSummary(await response.json() as BatchImportSummaryWire);
}

// ---------------------------------------------------------------------------
// Dataset Benchmark API boundary (typed snake_case wire -> camelCase domain)
// ---------------------------------------------------------------------------

interface OperatingMetricsWire {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

interface DatasetBenchmarkAggregateWire {
  ground_truth?: {
    raw_count: number;
    canonical_count: number;
    duplicates_removed: number;
    duplicate_policy: string;
  };
  classification_applicable: boolean;
  classification_reason: string | null;
  localization: { ap50: number | null; ap50_95: number | null; operating: OperatingMetricsWire };
  classification_on_matched: {
    matched_count: number;
    class_correct: number;
    class_wrong: number;
    matched_accuracy: number | null;
  } | null;
  class_aware: { map50: number | null; map50_95: number | null; operating: OperatingMetricsWire } | null;
}

interface DatasetBenchmarkPerClassWire {
  class_id: number;
  class_name: string;
  gt_count: number;
  prediction_count: number;
  ap50: number | null;
  ap50_95: number | null;
  operating: OperatingMetricsWire;
}

interface DatasetBenchmarkConfusionWire {
  gt_class_id: number;
  gt_class_name: string;
  pred_class_id: number;
  pred_class_name: string;
  count: number;
}

interface DatasetEvaluationWire {
  id: string;
  name: string;
  dataset_name: string;
  dataset_split: string;
  label_space: string;
  dataset_projection_id: string | null;
  dataset_id?: string | null;
  pipeline_id: string;
  pipeline_version: string;
  status: DatasetEvaluationStatus;
  expected_recordings: number;
  evaluated_recordings: number;
  missing_recordings: number;
  coverage: number;
  comparable: boolean;
  recording_manifest_hash: string;
  evaluation_protocol: string;
  protocol_config_json: Record<string, unknown>;
  aggregate_metrics_json: DatasetBenchmarkAggregateWire | null;
  per_class_metrics_json: DatasetBenchmarkPerClassWire[] | null;
  confusion_json: DatasetBenchmarkConfusionWire[] | null;
  progress_stage: string | null;
  progress_current: number | null;
  progress_total: number | null;
  error_type: string | null;
  error_message: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

interface DatasetEvaluationItemWire {
  id: string;
  evaluation_id: string;
  manifest_order: number;
  recording_id: string;
  recording_name: string;
  analysis_run_id: string | null;
  status: string;
  gt_count: number;
  prediction_count: number;
  error_reason: string | null;
}

interface ImportedBenchmarkBatchWire {
  import_fingerprint: string;
  pipeline_id: string | null;
  pipeline_version: string | null;
  dataset_name: string | null;
  dataset_split: string | null;
  label_space: string | null;
  run_count: number;
  detection_count: number;
  archive_sha256: string | null;
  result_provenance: Record<string, unknown>;
  transport_provenance: Record<string, unknown>;
  ready: boolean;
  inconsistency_reasons: string[];
}

interface ImportedBatchResolutionWire {
  import_fingerprint: string;
  dataset_name: string;
  dataset_split: string;
  label_space: string;
  dataset_projection_id: string | null;
  pipeline_id: string;
  pipeline_version: string;
  recording_manifest_hash: string;
  expected_recordings: number;
  resolved_recordings: number;
  missing_recordings: number;
  conflict_count: number;
  entries: Array<{
    manifest_order: number;
    recording_id: string;
    recording_name: string;
    analysis_run_id: string;
    item_key: string;
  }>;
}

interface DatasetBenchmarkCompareRecordingWire {
  recording_id: string;
  recording_name: string;
  evaluation_a_run_id: string | null;
  evaluation_b_run_id: string | null;
  comparison: DatasetRecordingComparison;
}

interface DatasetBenchmarkCompareWire {
  comparable: boolean;
  reasons: string[];
  evaluation_a_id: string;
  evaluation_b_id: string;
  aggregate_a: DatasetBenchmarkAggregateWire | null;
  aggregate_b: DatasetBenchmarkAggregateWire | null;
  deltas: Record<string, number | null>;
  recordings?: DatasetBenchmarkCompareRecordingWire[] | null;
}

function mapOperating(item: OperatingMetricsWire): OperatingMetrics {
  return { tp: item.tp, fp: item.fp, fn: item.fn, precision: item.precision, recall: item.recall, f1: item.f1 };
}

function mapAggregate(item: DatasetBenchmarkAggregateWire | null): DatasetBenchmarkAggregateMetrics | null {
  if (!item) return null;
  return {
    groundTruth: item.ground_truth ? {
      rawCount: item.ground_truth.raw_count,
      canonicalCount: item.ground_truth.canonical_count,
      duplicatesRemoved: item.ground_truth.duplicates_removed,
      duplicatePolicy: item.ground_truth.duplicate_policy,
    } : undefined,
    classificationApplicable: item.classification_applicable,
    classificationReason: item.classification_reason,
    localization: {
      ap50: item.localization.ap50,
      ap50_95: item.localization.ap50_95,
      operating: mapOperating(item.localization.operating),
    },
    classificationOnMatched: item.classification_on_matched ? {
      matchedCount: item.classification_on_matched.matched_count,
      classCorrect: item.classification_on_matched.class_correct,
      classWrong: item.classification_on_matched.class_wrong,
      matchedAccuracy: item.classification_on_matched.matched_accuracy,
    } : null,
    classAware: item.class_aware ? {
      map50: item.class_aware.map50,
      map50_95: item.class_aware.map50_95,
      operating: mapOperating(item.class_aware.operating),
    } : null,
  };
}

function mapPerClass(item: DatasetBenchmarkPerClassWire): DatasetBenchmarkPerClassMetric {
  return {
    classId: item.class_id,
    className: item.class_name,
    gtCount: item.gt_count,
    predictionCount: item.prediction_count,
    ap50: item.ap50,
    ap50_95: item.ap50_95,
    operating: mapOperating(item.operating),
  };
}

function mapConfusion(item: DatasetBenchmarkConfusionWire): DatasetBenchmarkConfusion {
  return {
    gtClassId: item.gt_class_id,
    gtClassName: item.gt_class_name,
    predClassId: item.pred_class_id,
    predClassName: item.pred_class_name,
    count: item.count,
  };
}

function mapDatasetEvaluation(item: DatasetEvaluationWire): DatasetEvaluation {
  return {
    id: item.id,
    name: item.name,
    datasetName: item.dataset_name,
    datasetSplit: item.dataset_split,
    labelSpace: item.label_space,
    datasetProjectionId: item.dataset_projection_id,
    datasetId: item.dataset_id ?? null,
    pipelineId: item.pipeline_id,
    pipelineVersion: item.pipeline_version,
    status: item.status,
    expectedRecordings: item.expected_recordings,
    evaluatedRecordings: item.evaluated_recordings,
    missingRecordings: item.missing_recordings,
    coverage: item.coverage,
    comparable: item.comparable,
    recordingManifestHash: item.recording_manifest_hash,
    evaluationProtocol: item.evaluation_protocol,
    protocolConfig: item.protocol_config_json,
    aggregateMetrics: mapAggregate(item.aggregate_metrics_json),
    perClassMetrics: item.per_class_metrics_json?.map(mapPerClass) ?? null,
    confusion: item.confusion_json?.map(mapConfusion) ?? null,
    progressStage: item.progress_stage,
    progressCurrent: item.progress_current,
    progressTotal: item.progress_total,
    errorType: item.error_type,
    errorMessage: item.error_message,
    createdAt: item.created_at ?? null,
    completedAt: item.completed_at ?? null,
  };
}

const mapDatasetEvaluationItem = (item: DatasetEvaluationItemWire): DatasetEvaluationItem => ({
  id: item.id,
  evaluationId: item.evaluation_id,
  manifestOrder: item.manifest_order,
  recordingId: item.recording_id,
  recordingName: item.recording_name,
  analysisRunId: item.analysis_run_id,
  status: item.status,
  gtCount: item.gt_count,
  predictionCount: item.prediction_count,
  errorReason: item.error_reason,
});

const mapImportedBatch = (item: ImportedBenchmarkBatchWire): ImportedBenchmarkBatch => ({
  importFingerprint: item.import_fingerprint,
  pipelineId: item.pipeline_id,
  pipelineVersion: item.pipeline_version,
  datasetName: item.dataset_name,
  datasetSplit: item.dataset_split,
  labelSpace: item.label_space,
  runCount: item.run_count,
  detectionCount: item.detection_count,
  archiveSha256: item.archive_sha256,
  resultProvenance: item.result_provenance,
  transportProvenance: item.transport_provenance,
  ready: item.ready,
  inconsistencyReasons: item.inconsistency_reasons,
});

const mapImportedResolution = (item: ImportedBatchResolutionWire): ImportedBatchResolution => ({
  importFingerprint: item.import_fingerprint,
  datasetName: item.dataset_name,
  datasetSplit: item.dataset_split,
  labelSpace: item.label_space,
  datasetProjectionId: item.dataset_projection_id,
  pipelineId: item.pipeline_id,
  pipelineVersion: item.pipeline_version,
  recordingManifestHash: item.recording_manifest_hash,
  expectedRecordings: item.expected_recordings,
  resolvedRecordings: item.resolved_recordings,
  missingRecordings: item.missing_recordings,
  conflictCount: item.conflict_count,
  entries: item.entries.map((entry) => ({
    manifestOrder: entry.manifest_order,
    recordingId: entry.recording_id,
    recordingName: entry.recording_name,
    analysisRunId: entry.analysis_run_id,
    itemKey: entry.item_key,
  })),
});

export async function listDatasetBenchmarks(datasetId?: string): Promise<DatasetEvaluation[]> {
  const suffix = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : "";
  return (await apiGet<DatasetEvaluationWire[]>(`/api/dataset-benchmarks${suffix}`)).map(mapDatasetEvaluation);
}

export async function getDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiGet<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}`));
}

export async function listDatasetBenchmarkItems(id: string): Promise<DatasetEvaluationItem[]> {
  return (await apiGet<DatasetEvaluationItemWire[]>(`/api/dataset-benchmarks/${id}/items`))
    .map(mapDatasetEvaluationItem);
}

export async function listImportedBenchmarkBatches(): Promise<ImportedBenchmarkBatch[]> {
  return (await apiGet<ImportedBenchmarkBatchWire[]>("/api/dataset-benchmarks/imported-batches"))
    .map(mapImportedBatch);
}

export async function resolveImportedBenchmarkBatch(importFingerprint: string): Promise<ImportedBatchResolution> {
  const wire = await apiPostJson<ImportedBatchResolutionWire>(
    "/api/dataset-benchmarks/resolve-imported-batch",
    { import_fingerprint: importFingerprint },
  );
  return mapImportedResolution(wire);
}

export async function createDatasetBenchmark(payload: {
  name: string;
  resolution: ImportedBatchResolution;
}): Promise<DatasetEvaluation> {
  const body: Record<string, unknown> = {
    name: payload.name,
    dataset_name: payload.resolution.datasetName,
    dataset_split: payload.resolution.datasetSplit,
    label_space: payload.resolution.labelSpace,
    recording_manifest_hash: payload.resolution.recordingManifestHash,
    allow_incomplete: false,
    items: payload.resolution.entries.map((entry) => ({
      recording_id: entry.recordingId,
      analysis_run_id: entry.analysisRunId,
    })),
  };
  if (payload.resolution.datasetProjectionId != null) {
    body.dataset_projection_id = payload.resolution.datasetProjectionId;
  }
  return mapDatasetEvaluation(
    await apiPostJson<DatasetEvaluationWire>("/api/dataset-benchmarks", body),
  );
}

export async function runDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/run`, {}));
}

export async function retryDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/retry`, {}));
}

const mapRecordingComparison = (
  item: DatasetBenchmarkCompareRecordingWire,
): DatasetBenchmarkRecordingComparison => ({
  recordingId: item.recording_id,
  recordingName: item.recording_name,
  evaluationARunId: item.evaluation_a_run_id,
  evaluationBRunId: item.evaluation_b_run_id,
  comparison: item.comparison,
});

export async function compareDatasetBenchmarks(a: string, b: string): Promise<DatasetBenchmarkCompareResult> {
  const wire = await apiPostJson<DatasetBenchmarkCompareWire>("/api/dataset-benchmarks/compare", {
    evaluation_a_id: a,
    evaluation_b_id: b,
  });
  return {
    comparable: wire.comparable,
    reasons: wire.reasons,
    evaluationAId: wire.evaluation_a_id,
    evaluationBId: wire.evaluation_b_id,
    aggregateA: mapAggregate(wire.aggregate_a),
    aggregateB: mapAggregate(wire.aggregate_b),
    deltas: wire.deltas,
    recordings: (wire.recordings ?? []).map(mapRecordingComparison),
  };
}

// ---------------------------------------------------------------------------
// V1.1 Data Library read + lifecycle contract
// ---------------------------------------------------------------------------

interface DatasetProjectionSummaryWire {
  dataset_projection_id: string;
  source: string;
  dataset_name: string;
  dataset_split: string;
  label_space: string | null;
  sample_count: number;
  ground_truth_sample_count: number;
  external: boolean;
  source_location: string | null;
}

interface DatasetProjectionListWire {
  items: DatasetProjectionSummaryWire[];
  total: number;
}

function mapDatasetProjection(wire: DatasetProjectionSummaryWire): DatasetProjectionSummary {
  return {
    datasetProjectionId: wire.dataset_projection_id,
    source: wire.source,
    datasetName: wire.dataset_name,
    datasetSplit: wire.dataset_split,
    labelSpace: wire.label_space,
    sampleCount: wire.sample_count,
    groundTruthSampleCount: wire.ground_truth_sample_count,
    external: wire.external,
    sourceLocation: wire.source_location,
  };
}

export async function listDatasetProjections(limit = 50, offset = 0): Promise<DatasetProjectionListPage> {
  const wire = await apiGet<DatasetProjectionListWire>(
    `/api/data-library/datasets?limit=${limit}&offset=${offset}`,
  );
  return { items: wire.items.map(mapDatasetProjection), total: wire.total };
}

export async function getDatasetProjection(datasetProjectionId: string): Promise<DatasetProjectionSummary> {
  const wire = await apiGet<DatasetProjectionSummaryWire>(
    `/api/data-library/datasets/${encodeURIComponent(datasetProjectionId)}`,
  );
  return mapDatasetProjection(wire);
}

interface DatasetSummaryWire {
  id: string;
  name: string;
  split: string;
  adapter_id: string;
  label_space: string | null;
  local_root: string;
  portable_fingerprint: string | null;
  sample_count: number;
  ground_truth_sample_count: number;
  created_at: string;
}

interface DatasetListWire {
  items: DatasetSummaryWire[];
  total: number;
}

function mapDatasetSummary(wire: DatasetSummaryWire): DatasetSummary {
  return {
    id: wire.id,
    name: wire.name,
    split: wire.split,
    adapterId: wire.adapter_id,
    labelSpace: wire.label_space,
    localRoot: wire.local_root,
    portableFingerprint: wire.portable_fingerprint,
    sampleCount: wire.sample_count,
    groundTruthSampleCount: wire.ground_truth_sample_count,
    createdAt: wire.created_at,
  };
}

export async function listDatasets(limit = 50, offset = 0): Promise<DatasetPage> {
  const wire = await apiGet<DatasetListWire>(`/api/datasets?limit=${limit}&offset=${offset}`);
  return { items: wire.items.map(mapDatasetSummary), total: wire.total };
}

export async function getDataset(datasetId: string): Promise<DatasetSummary> {
  return mapDatasetSummary(await apiGet<DatasetSummaryWire>(`/api/datasets/${encodeURIComponent(datasetId)}`));
}

interface DatasetSampleWire {
  id: string;
  name: string;
  sample_key: string | null;
  data_format: string;
  sample_rate_hz: number;
  center_frequency_hz: number;
  frequency_low_hz: number;
  frequency_high_hz: number;
  num_samples: number;
  duration_s: number;
  has_ground_truth: boolean;
  analysis_count: number;
}

interface DatasetSampleListWire {
  dataset_id: string;
  items: DatasetSampleWire[];
  total: number;
}

export async function listDatasetSamples(
  datasetId: string,
  params: { limit?: number; offset?: number; search?: string } = {},
): Promise<DatasetSamplePage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.search) query.set("search", params.search);
  const wire = await apiGet<DatasetSampleListWire>(
    `/api/datasets/${encodeURIComponent(datasetId)}/samples?${query.toString()}`,
  );
  return {
    datasetId: wire.dataset_id,
    total: wire.total,
    items: wire.items.map((item) => ({
      id: item.id,
      name: item.name,
      sampleKey: item.sample_key,
      dataFormat: item.data_format,
      sampleRateHz: item.sample_rate_hz,
      centerFrequencyHz: item.center_frequency_hz,
      frequencyLowHz: item.frequency_low_hz,
      frequencyHighHz: item.frequency_high_hz,
      numSamples: item.num_samples,
      durationS: item.duration_s,
      hasGroundTruth: item.has_ground_truth,
      analysisCount: item.analysis_count,
    })),
  };
}

interface StandaloneSampleWire {
  id: string;
  name: string;
  source: string;
  sample_rate_hz: number;
  center_frequency_hz: number;
  frequency_low_hz: number;
  frequency_high_hz: number;
  duration_s: number;
  data_format: string;
  has_ground_truth: boolean;
  analysis_count: number;
}

interface StandaloneSampleListWire {
  items: StandaloneSampleWire[];
  total: number;
}

export async function listStandaloneSamples(
  params: { limit?: number; offset?: number; search?: string } = {},
): Promise<StandaloneSamplePage> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 50),
    offset: String(params.offset ?? 0),
  });
  if (params.search) query.set("search", params.search);
  const wire = await apiGet<StandaloneSampleListWire>(
    `/api/data-library/standalone-samples?${query.toString()}`,
  );
  return {
    total: wire.total,
    items: wire.items.map((item) => ({
      id: item.id,
      name: item.name,
      source: item.source,
      sampleRateHz: item.sample_rate_hz,
      centerFrequencyHz: item.center_frequency_hz,
      frequencyLowHz: item.frequency_low_hz,
      frequencyHighHz: item.frequency_high_hz,
      durationS: item.duration_s,
      dataFormat: item.data_format,
      hasGroundTruth: item.has_ground_truth,
      analysisCount: item.analysis_count,
    })),
  };
}

interface DatasetAnalysisHistoryItemWire {
  kind: "experiment" | "evaluation" | "imported_batch";
  resource_id: string;
  name: string;
  pipeline_id: string;
  pipeline_version: string;
  status: string;
  executor: string | null;
  expected_items: number;
  completed_items: number;
  failed_items: number;
  coverage: number | null;
  created_at: string | null;
  dataset_evaluation_id: string | null;
  batch_id: string | null;
  archive_sha256: string | null;
}

interface DatasetAnalysisHistoryWire {
  dataset_id: string;
  items: DatasetAnalysisHistoryItemWire[];
  total: number;
}

export async function listDatasetAnalysisHistory(
  datasetId: string,
): Promise<DatasetAnalysisHistoryPage> {
  const wire = await apiGet<DatasetAnalysisHistoryWire>(
    `/api/datasets/${encodeURIComponent(datasetId)}/analysis-history`,
  );
  return {
    datasetId: wire.dataset_id,
    total: wire.total,
    items: wire.items.map((item) => ({
      kind: item.kind,
      resourceId: item.resource_id,
      name: item.name,
      pipelineId: item.pipeline_id,
      pipelineVersion: item.pipeline_version,
      status: item.status,
      executor: item.executor,
      expectedItems: item.expected_items,
      completedItems: item.completed_items,
      failedItems: item.failed_items,
      coverage: item.coverage,
      createdAt: item.created_at,
      datasetEvaluationId: item.dataset_evaluation_id,
      batchId: item.batch_id,
      archiveSha256: item.archive_sha256,
    })),
  };
}

export async function deleteRecording(recordingId: string): Promise<void> {
  await apiDelete(`/api/recordings/${encodeURIComponent(recordingId)}`);
}

export async function deleteDatasetProjection(datasetProjectionId: string): Promise<void> {
  await apiDelete(`/api/data-library/datasets/${encodeURIComponent(datasetProjectionId)}`);
}

/** First-class dataset removal by dataset_id (membership-based). */
export async function deleteDataset(datasetId: string): Promise<void> {
  await apiDelete(`/api/datasets/${encodeURIComponent(datasetId)}`);
}

export async function deleteAnalysisRun(runId: string): Promise<void> {
  await apiDelete(`/api/analysis-runs/${encodeURIComponent(runId)}`);
}

export function deleteBlockersFromError(error: unknown): DeleteBlocker[] {
  if (!(error instanceof PlatformApiError)) {
    return [];
  }
  const raw = (error.details as { blockers?: unknown }).blockers;
  if (!Array.isArray(raw)) {
    return [];
  }
  const blockers: DeleteBlocker[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const { kind, resource_id, reference } = entry as Record<string, unknown>;
    if (typeof kind !== "string" || typeof resource_id !== "string" || typeof reference !== "string") {
      continue;
    }
    blockers.push({
      kind: kind as DeleteBlocker["kind"],
      resourceId: resource_id,
      reference: reference as DeleteBlocker["reference"],
    });
  }
  return blockers;
}
