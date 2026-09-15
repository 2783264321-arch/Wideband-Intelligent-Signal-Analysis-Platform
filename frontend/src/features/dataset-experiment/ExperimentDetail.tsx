import { Alert, Spin, Tabs } from "antd";
import { useEffect, useState } from "react";
import { getDatasetExperiment, PlatformApiError } from "../../api/client";
import type { DatasetExperiment } from "../../api/types";
import { ExperimentProgressHeader } from "./ExperimentProgressHeader";
import { ExperimentItemTable } from "./ExperimentItemTable";

const TERMINAL_STATUSES = new Set(["completed", "completed_with_failures", "failed"]);
const POLL_INTERVAL_MS = 1000;

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export function ExperimentDetail({ experimentId }: { experimentId: string }) {
  const [experiment, setExperiment] = useState<DatasetExperiment | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const clear = () => { if (timer !== undefined) { window.clearTimeout(timer); timer = undefined; } };

    const tick = async () => {
      try {
        const next = await getDatasetExperiment(experimentId);
        if (!active) return;
        setExperiment(next);
        if (TERMINAL_STATUSES.has(next.status)) { clear(); return; }
        schedule();
      } catch (reason) {
        if (!active) return;
        setError(toErrorText(reason));
        schedule();
      }
    };
    const schedule = () => { clear(); timer = window.setTimeout(() => { void tick(); }, POLL_INTERVAL_MS); };

    void tick();
    return () => { active = false; clear(); };
  }, [experimentId]);

  if (error !== null) {
    return <Alert type="error" showIcon message="Unable to load experiment" description={error} />;
  }
  if (experiment === null) {
    return <Spin tip="Loading experiment..." />;
  }

  return (
    <>
      <ExperimentProgressHeader experiment={experiment} />
      <Tabs
        items={[
          { key: "items", label: "Items", children: <ExperimentItemTable experimentId={experiment.id} /> },
          { key: "attempts", label: "Attempts", children: <div data-testid="experiment-attempts-tab" /> },
          { key: "evaluation", label: "Evaluation", children: <div data-testid="experiment-evaluation-tab" /> },
        ]}
      />
    </>
  );
}
