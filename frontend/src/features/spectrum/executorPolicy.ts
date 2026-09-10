/**
 * Deterministic executor resolution for the Spectrum run button.
 *
 * Runnable choices are derived ONLY from the deployment-qualified
 * `executorsSupported` projection (never from legacy `cpuSupported`):
 *   remoteOnly = supports remote_gpu AND NOT local_cpu
 *   localOnly  = supports local_cpu AND NOT remote_gpu
 *   dual       = supports local_cpu AND remote_gpu
 *
 * remoteOnly: available -> remote_gpu; loading/idle/error/unavailable -> DISABLED.
 * localOnly:  -> local_cpu.
 * dual (recommended remote): loading/idle -> DISABLED; available -> remote_gpu;
 *   unavailable/error -> fall back to local_cpu.
 * dual (recommended local/null): -> local_cpu.
 * neither usable ([] or unknown) -> DISABLED.
 */
export type AvailabilityState =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "available" }
  | { state: "unavailable"; reason?: string | null }
  | { state: "error"; reason?: string | null };

export interface ExecutorResolution {
  executor: "remote_gpu" | "local_cpu" | null;
  disabledReason: string | null;
}

export interface ExecutorPolicyPipeline {
  executorsSupported: string[];
  recommendedExecutor: string | null;
}

export function resolveExecutorForPipeline(
  pipeline: ExecutorPolicyPipeline,
  availability: AvailabilityState,
): ExecutorResolution {
  const supportsRemote = pipeline.executorsSupported.includes("remote_gpu");
  const supportsLocal = pipeline.executorsSupported.includes("local_cpu");
  const remoteOnly = supportsRemote && !supportsLocal;
  const localOnly = supportsLocal && !supportsRemote;
  const dual = supportsLocal && supportsRemote;

  if (remoteOnly) {
    if (availability.state === "available") {
      return { executor: "remote_gpu", disabledReason: null };
    }
    const reason =
      availability.state === "loading" || availability.state === "idle"
        ? "Checking remote GPU availability..."
        : availability.state === "error"
          ? "Unable to check remote GPU availability."
          : (availability.reason ?? "Remote GPU executor is unavailable.");
    return { executor: null, disabledReason: reason };
  }

  if (localOnly) {
    return { executor: "local_cpu", disabledReason: null };
  }

  if (dual) {
    if (pipeline.recommendedExecutor === "remote_gpu") {
      if (availability.state === "available") {
        return { executor: "remote_gpu", disabledReason: null };
      }
      if (availability.state === "idle" || availability.state === "loading") {
        return { executor: null, disabledReason: "Checking remote GPU availability..." };
      }
      // unavailable / error -> fall back to the local executor.
      return { executor: "local_cpu", disabledReason: null };
    }
    return { executor: "local_cpu", disabledReason: null };
  }

  return { executor: null, disabledReason: "No executor is usable for this pipeline." };
}
