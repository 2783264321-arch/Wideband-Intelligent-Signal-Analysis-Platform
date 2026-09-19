import { useEffect, useRef } from "react";
import { getAnalysisRun, getDetections } from "../../api/client";
import type { AnalysisRun, DetectionResult } from "../../api/types";

const POLL_INTERVAL_MS = 1000;
const ACTIVE_STATUSES = new Set(["pending", "running"]);

export interface UseRunPollingArgs {
  runId: string | undefined;
  onRun: (run: AnalysisRun) => void;
  onDetections: (detections: DetectionResult[]) => void;
  onError: (reason: unknown) => void;
}

/**
 * Polls one AnalysisRun while it is non-terminal, using serialized scheduling:
 * the next poll is only scheduled AFTER the current request settles, so at most
 * one `getAnalysisRun` request is ever in flight for a runId.
 *
 * The lifecycle is bound to the exact `runId` it belongs to: changing `runId` or
 * unmounting cancels the previous lifecycle, and a late response from a previous
 * run can never call the current callbacks.
 *
 * - pending / running    -> schedule the next poll
 * - completed            -> stop run polling, then fetch detections once
 * - failed / interrupted -> stop run polling
 *
 * A transient poll failure is surfaced via `onError` and polling continues while
 * the lifecycle is still active (a run is not stranded by one failed poll). A
 * detections failure after completion is surfaced but never restarts run polling.
 */
export function useRunPolling({ runId, onRun, onDetections, onError }: UseRunPollingArgs): void {
  const onRunRef = useRef(onRun);
  const onDetectionsRef = useRef(onDetections);
  const onErrorRef = useRef(onError);
  onRunRef.current = onRun;
  onDetectionsRef.current = onDetections;
  onErrorRef.current = onError;

  useEffect(() => {
    if (runId === undefined) return undefined;
    const boundRunId = runId;
    let active = true;
    let timer: number | undefined;

    const clear = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    };

    const schedule = () => {
      clear();
      timer = window.setTimeout(() => { void poll(); }, POLL_INTERVAL_MS);
    };

    const poll = async (): Promise<void> => {
      let run: AnalysisRun;
      try {
        run = await getAnalysisRun(boundRunId);
      } catch (reason) {
        if (!active) return;
        onErrorRef.current(reason);
        // Transient failure: keep polling while the lifecycle is still active.
        schedule();
        return;
      }
      if (!active) return;

      if (ACTIVE_STATUSES.has(run.status)) {
        onRunRef.current(run);
        schedule();
        return;
      }

      // Terminal: stop run polling.
      clear();
      if (run.status !== "completed") {
        onRunRef.current(run);
        return;
      }

      // Fetch the results BEFORE publishing the terminal status. Publishing a
      // terminal run makes the owner stop passing `runId`, which tears this
      // lifecycle down (active=false) and would silently discard the results
      // fetched afterwards — the run would show as completed with no detections.
      let detections: DetectionResult[] | null = null;
      try {
        detections = await getDetections(boundRunId);
      } catch (reason) {
        if (active) onErrorRef.current(reason);
      }
      if (!active) return;
      onRunRef.current(run);
      if (detections !== null) onDetectionsRef.current(detections);
    };

    schedule();
    return () => {
      active = false;
      clear();
    };
  }, [runId]);
}
