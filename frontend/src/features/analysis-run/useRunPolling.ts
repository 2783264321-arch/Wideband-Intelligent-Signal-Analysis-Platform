import { useEffect, useRef } from "react";
import { getAnalysisRun, getDetections } from "../../api/client";
import type { AnalysisRun, DetectionResult } from "../../api/types";

const POLL_INTERVAL_MS = 1000;
const ACTIVE_STATUSES = new Set(["pending", "running"]);

export interface UseRunPollingArgs {
  runId: string | undefined;
  onRun: (run: AnalysisRun) => void;
  onDetections: (detections: DetectionResult[]) => void;
}

/**
 * Polls one AnalysisRun while it is non-terminal and stops on any terminal status.
 *
 * The lifecycle is bound to the exact `runId` it belongs to: changing `runId` or
 * unmounting cancels the previous interval, and a late response from a previous
 * run is ignored (it can never update the current run's state).
 *
 * - pending / running   -> continue polling
 * - completed           -> stop and fetch detections once
 * - failed / interrupted-> stop (no detections)
 */
export function useRunPolling({ runId, onRun, onDetections }: UseRunPollingArgs): void {
  const onRunRef = useRef(onRun);
  const onDetectionsRef = useRef(onDetections);
  onRunRef.current = onRun;
  onDetectionsRef.current = onDetections;

  useEffect(() => {
    if (runId === undefined) return undefined;
    const boundRunId = runId;
    let active = true;
    let timer: number | undefined;

    const stop = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };

    const tick = async () => {
      try {
        const run = await getAnalysisRun(boundRunId);
        if (!active) return;
        onRunRef.current(run);
        if (!ACTIVE_STATUSES.has(run.status)) {
          stop();
          if (run.status === "completed") {
            const detections = await getDetections(boundRunId);
            if (!active) return;
            onDetectionsRef.current(detections);
          }
        }
      } catch {
        if (!active) return;
        stop();
      }
    };

    timer = window.setInterval(() => { void tick(); }, POLL_INTERVAL_MS);
    return () => {
      active = false;
      stop();
    };
  }, [runId]);
}
