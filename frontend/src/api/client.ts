import type { RecordingDetail, SpectrogramMeta } from "./types";

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

export async function getRecording(recordingId: string): Promise<RecordingDetail> {
  const item = await apiGet<RecordingWire>(`/api/recordings/${recordingId}`);
  return {
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
  };
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
