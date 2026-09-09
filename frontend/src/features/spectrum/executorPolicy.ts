/**
 * Deterministic executor resolution for the Spectrum run button (Task 12F-C
 * Task 4). Pure and unit-testable.
 *
 * Classification:
 *   remoteOnly = supports remote_gpu AND NOT cpuSupported
 *   localOnly  = cpuSupported AND does NOT support remote_gpu
 *   dual       = cpuSupported AND supports remote_gpu
 *
 * remoteOnly: available -> remote_gpu; loading/idle/error/unavailable -> DISABLED.
 * localOnly:  -> local_cpu.
 * dual (recommended remote): loading/idle -> DISABLED; available -> remote_gpu;
 *   unavailable/error -> fall back to local_cpu.
 * dual (recommended local): -> local_cpu.
 * neither usable -> DISABLED.
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
  cpuSupported: boolean;
  executorsSupported: string[];
  recommendedExecutor: string;
}

export function resolveExecutorForPipeline(
  pipeline: ExecutorPolicyPipeline,
  availability: AvailabilityState,
): ExecutorResolution {
  const supportsRemote = pipeline.executorsSupported.includes("remote_gpu");
  const remoteOnly = supportsRemote && !pipeline.cpuSupported;
  const localOnly = pipeline.cpuSupported && !supportsRemote;
  const dual = pipeline.cpuSupported && supportsRemote;

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