import { Alert, Spin, Tabs } from "antd";
import { useEffect, useState } from "react";
import { getDatasetExperiment, PlatformApiError } from "../../api/client";
import type { DatasetExperiment } from "../../api/types";
import { ExperimentProgressHeader } from "./ExperimentProgressHeader";
import { ExperimentItemTable } from "./ExperimentItemTable";
import { ExperimentAttemptsTab } from "./AttemptTimeline";
import { LinkedEvaluationSummary } from "./LinkedEvaluationSummary";
import { useLocalization } from "../../localization/useLocalization";

const TERMINAL_STATUSES = new Set(["completed", "completed_with_failures", "failed"]);
const POLL_INTERVAL_MS = 1000;

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export function ExperimentDetail({ experimentId }: { experimentId: string }) {
  const { t } = useLocalization();
  const [experiment, setExperiment] = useState<DatasetExperiment | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped when a retry is accepted so the polling lifecycle restarts from the
  // backend-authoritative state even though the previous lifecycle had terminated.
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const clear = () => { if (timer !== undefined) { window.clearTimeout(timer); timer = undefined; } };

    const tick = async () => {
      try {
        const next = await getDatasetExperiment(experimentId);
        if (!active) return;
        // A successful poll clears any transient polling error and recovers the view.
        setError(null);
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
  }, [experimentId, refreshToken]);

  if (error !== null) {
    return <Alert type="error" showIcon message={t("experiment.detailLoadError")} description={error} />;
  }
  if (experiment === null) {
    return <Spin tip={t("common.loadingExperiment")} />;
  }

  return (
    <>
      <ExperimentProgressHeader experiment={experiment} />
      <Tabs
        items={[
          { key: "items", label: t("experiment.itemsTab"), children: <ExperimentItemTable experimentId={experiment.id} /> },
          { key: "evaluation", label: t("experiment.evaluationTab"), children: (
            <LinkedEvaluationSummary
              experiment={experiment}
              onRetryAccepted={(updated) => {
                setError(null);
                setExperiment(updated);
                setRefreshToken((token) => token + 1);
              }}
            />
          ) },
          {
            key: "details",
            label: t("experiment.detailsTab"),
            children: <ExperimentAttemptsTab experimentId={experiment.id} />,
          },
        ]}
      />
    </>
  );
}
