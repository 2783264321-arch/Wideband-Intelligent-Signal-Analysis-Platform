import type { DetectionResult, FFTData, GroundTruthResult, RecordingDetail, SpectrogramMeta, WaveformData } from "./types";

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

export async function listRecordings(): Promise<RecordingDetail[]> {
  return (await apiGet<RecordingWire[]>("/api/recordings")).map(mapRecording);
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
