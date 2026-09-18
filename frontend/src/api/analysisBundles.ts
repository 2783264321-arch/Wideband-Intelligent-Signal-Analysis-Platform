import { apiUrl, PlatformApiError } from "./client";

/**
 * Isolated Analysis Bundle wire boundary.
 *
 * The backend contract is fixed:
 *   GET  /api/dataset-experiments/{experimentId}/export   -> application/zip
 *   POST /api/analysis-bundles/import                      -> AnalysisBundleImportSummary
 *
 * This module keeps bundle-specific wire + domain types out of `api/client.ts`
 * and only reuses the shared `apiUrl` / `PlatformApiError` primitives.
 */

export interface AnalysisBundleRunMapping {
  sampleKey: string;
  sampleName: string;
  recordingId: string;
  analysisRunId: string;
}

export interface AnalysisBundleImportSummary {
  schemaVersion: number;
  bundleId: string;
  importFingerprint: string;
  archiveSha256: string;
  datasetId: string | null;
  datasetName: string;
  datasetSplit: string;
  pipelineId: string;
  pipelineVersion: string;
  labelSpace: string;
  sampleCount: number;
  detectionCount: number;
  alreadyImported: boolean;
  createdRuns: number;
  existingRuns: number;
  createdDetections: number;
  /** Durable first-class Dataset Analysis (imported DatasetExperiment) id. */
  datasetAnalysisId: string | null;
  sampleRunMapping: AnalysisBundleRunMapping[];
}

interface AnalysisBundleRunMappingWire {
  sample_key: string;
  sample_name: string;
  recording_id: string;
  analysis_run_id: string;
}

interface AnalysisBundleImportSummaryWire {
  schema_version: number;
  bundle_id: string;
  import_fingerprint: string;
  archive_sha256: string;
  dataset_id: string | null;
  dataset_name: string;
  dataset_split: string;
  pipeline_id: string;
  pipeline_version: string;
  label_space: string;
  sample_count: number;
  detection_count: number;
  already_imported: boolean;
  created_runs: number;
  existing_runs: number;
  created_detections: number;
  dataset_analysis_id: string | null;
  sample_run_mapping: AnalysisBundleRunMappingWire[];
}

export const ANALYSIS_BUNDLE_DATASET_MISMATCH = "ANALYSIS_BUNDLE_DATASET_MISMATCH";

/** Preserves the structured backend contract `{ error: { code, message, details } }`. */
async function structuredErrorFromResponse(response: Response): Promise<PlatformApiError> {
  let code = `HTTP_${response.status}`;
  let message = `API request failed: ${response.status}`;
  let details: Record<string, unknown> = {};
  try {
    const body = await response.json() as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    };
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

function decode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** Extract a filename from Content-Disposition, tolerating RFC 5987 `filename*`. */
export function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (extended) return decode(extended[1].trim().replace(/^["']|["']$/g, ""));
  const quoted = /filename\s*=\s*"([^"]+)"/i.exec(header);
  if (quoted) return quoted[1].trim();
  const bare = /filename\s*=\s*([^;]+)/i.exec(header);
  if (bare) return bare[1].trim().replace(/^["']|["']$/g, "");
  return null;
}

/** Keep only the basename so a hostile header cannot escape the download name. */
function sanitizeFilename(candidate: string | null, fallback: string): string {
  const base = (candidate ?? "").split(/[\\/]/).pop()?.trim() ?? "";
  return base.length > 0 ? base : fallback;
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Download a completed Dataset Analysis as a portable Analysis Bundle.
 * Returns the filename actually used for the browser download.
 */
export async function exportAnalysisBundle(experimentId: string): Promise<string> {
  const response = await fetch(
    apiUrl(`/api/dataset-experiments/${encodeURIComponent(experimentId)}/export`),
  );
  if (!response.ok) {
    throw await structuredErrorFromResponse(response);
  }
  const blob = await response.blob();
  const fallback = `wisa-analysis-${experimentId}.zip`;
  const filename = sanitizeFilename(
    filenameFromContentDisposition(response.headers.get("Content-Disposition")),
    fallback,
  );
  triggerBrowserDownload(blob, filename);
  return filename;
}

/**
 * Download one completed single-sample analysis run as a portable Analysis
 * Bundle. Returns the filename actually used for the browser download.
 */
export async function exportAnalysisRunBundle(runId: string): Promise<string> {
  const response = await fetch(
    apiUrl(`/api/analysis-runs/${encodeURIComponent(runId)}/export`),
  );
  if (!response.ok) {
    throw await structuredErrorFromResponse(response);
  }
  const blob = await response.blob();
  const fallback = `wisa-analysis-run-${runId}.zip`;
  const filename = sanitizeFilename(
    filenameFromContentDisposition(response.headers.get("Content-Disposition")),
    fallback,
  );
  triggerBrowserDownload(blob, filename);
  return filename;
}

/** Import a previously exported Analysis Bundle. Never reruns inference. */
export async function importAnalysisBundle(file: File): Promise<AnalysisBundleImportSummary> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(apiUrl("/api/analysis-bundles/import"), { method: "POST", body });
  if (!response.ok) {
    throw await structuredErrorFromResponse(response);
  }
  return mapImportSummary(await response.json() as AnalysisBundleImportSummaryWire);
}

export function isAnalysisBundleDatasetMismatch(reason: unknown): boolean {
  return reason instanceof PlatformApiError && reason.code === ANALYSIS_BUNDLE_DATASET_MISMATCH;
}

function mapImportSummary(wire: AnalysisBundleImportSummaryWire): AnalysisBundleImportSummary {
  return {
    schemaVersion: wire.schema_version,
    bundleId: wire.bundle_id,
    importFingerprint: wire.import_fingerprint,
    archiveSha256: wire.archive_sha256,
    datasetId: wire.dataset_id,
    datasetName: wire.dataset_name,
    datasetSplit: wire.dataset_split,
    pipelineId: wire.pipeline_id,
    pipelineVersion: wire.pipeline_version,
    labelSpace: wire.label_space,
    sampleCount: wire.sample_count,
    detectionCount: wire.detection_count,
    alreadyImported: wire.already_imported,
    createdRuns: wire.created_runs,
    existingRuns: wire.existing_runs,
    createdDetections: wire.created_detections,
    datasetAnalysisId: wire.dataset_analysis_id,
    sampleRunMapping: wire.sample_run_mapping.map((row) => ({
      sampleKey: row.sample_key,
      sampleName: row.sample_name,
      recordingId: row.recording_id,
      analysisRunId: row.analysis_run_id,
    })),
  };
}
