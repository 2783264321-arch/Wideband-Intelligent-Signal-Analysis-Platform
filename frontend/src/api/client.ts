import type { DetectionResult, FFTData, GroundTruthResult, RecordingDetail, SpectrogramMeta, WaveformData } from "./types";
import type {
  DatasetBenchmarkAggregateMetrics,
  DatasetBenchmarkCompareResult,
  DatasetBenchmarkConfusion,
  DatasetBenchmarkPerClassMetric,
  DatasetEvaluation,
  DatasetEvaluationItem,
  DatasetEvaluationStatus,
  ImportedBatchResolution,
  ImportedBenchmarkBatch,
  OperatingMetrics,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE_URL}${path}`;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path));
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function apiPostJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return response.json() as Promise<T>;
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
}

interface SpectrogramWire {
  representation: "stft" | "ls-stft";
  image_url: string;
  t_start_s: number;
  t_end_s: number;
  f_low_hz: number;
  f_high_hz: number;
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
  if (!response.ok) throw new Error(`API request failed: ${response.status}`);
  return mapRecording(await response.json() as RecordingWire);
}

export async function getRecording(recordingId: string): Promise<RecordingDetail> {
  return mapRecording(await apiGet<RecordingWire>(`/api/recordings/${recordingId}`));
}

export async function getSpectrogram(recordingId: string): Promise<SpectrogramMeta> {
  const item = await apiGet<SpectrogramWire>(`/api/recordings/${recordingId}/spectrogram?representation=stft`);
  return {
    representation: item.representation,
    imageUrl: apiUrl(item.image_url),
    tStartS: item.t_start_s,
    tEndS: item.t_end_s,
    fLowHz: item.f_low_hz,
    fHighHz: item.f_high_hz,
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
  stages: string[];
  inspectable_stages: string[];
  task_capability: string;
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
  started_at?: string | null;
  finished_at?: string | null;
  error_type?: string | null;
  error_message?: string | null;
  worker_pid?: number | null;
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
    startedAt: item.started_at,
    finishedAt: item.finished_at,
    errorType: item.error_type,
    errorMessage: item.error_message,
    workerPid: item.worker_pid,
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
    stages: item.stages,
    inspectableStages: item.inspectable_stages,
    taskCapability: item.task_capability,
  }));
}

export async function createAnalysisRun(recordingId: string, pipelineId: string): Promise<import("./types").AnalysisRun> {
  return mapAnalysisRun(await apiPostJson<AnalysisRunWire>("/api/analysis-runs", {
    recording_id: recordingId,
    pipeline_id: pipelineId,
    executor: "local_cpu",
    parameters: {},
  }));
}

export async function getAnalysisRun(runId: string): Promise<import("./types").AnalysisRun> {
  return mapAnalysisRun(await apiGet<AnalysisRunWire>(`/api/analysis-runs/${runId}`));
}

export async function listAnalysisRuns(recordingId: string): Promise<import("./types").AnalysisRun[]> {
  const items = await apiGet<AnalysisRunWire[]>(
    `/api/analysis-runs?recording_id=${encodeURIComponent(recordingId)}&status=completed`,
  );
  return items.map(mapAnalysisRun);
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
  if (!response.ok) {
    let message = `API request failed: ${response.status}`;
    try {
      const payloadError = await response.json() as { error?: { message?: string } };
      if (payloadError.error?.message) message = payloadError.error.message;
    } catch {
      // Non-JSON error body; keep the generic message.
    }
    throw new Error(message);
  }
  return mapCompare(await response.json() as CompareWire);
}

export async function importAnalysisPackage(recordingId: string, file: File): Promise<import("./types").AnalysisRun> {
  const body = new FormData();
  body.append("recording_id", recordingId);
  body.append("file", file);
  const response = await fetch(apiUrl("/api/imported-runs"), { method: "POST", body });
  if (!response.ok) {
    let message = `API request failed: ${response.status}`;
    try {
      const payload = await response.json() as { error?: { message?: string } };
      if (payload.error?.message) message = payload.error.message;
    } catch {
      // Non-JSON error body; keep the generic message.
    }
    throw new Error(message);
  }
  return mapAnalysisRun(await response.json() as AnalysisRunWire);
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

interface DatasetBenchmarkCompareWire {
  comparable: boolean;
  reasons: string[];
  evaluation_a_id: string;
  evaluation_b_id: string;
  aggregate_a: DatasetBenchmarkAggregateWire | null;
  aggregate_b: DatasetBenchmarkAggregateWire | null;
  deltas: Record<string, number | null>;
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

export async function listDatasetBenchmarks(): Promise<DatasetEvaluation[]> {
  return (await apiGet<DatasetEvaluationWire[]>("/api/dataset-benchmarks")).map(mapDatasetEvaluation);
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
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>("/api/dataset-benchmarks", {
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
  }));
}

export async function runDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/run`, {}));
}

export async function retryDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/retry`, {}));
}

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
  };
}
