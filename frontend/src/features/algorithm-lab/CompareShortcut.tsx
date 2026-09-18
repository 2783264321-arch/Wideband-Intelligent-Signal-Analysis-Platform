import { Button, Checkbox, Space, Typography } from "antd";
import { useState } from "react";
import { useLocalization } from "../../localization/useLocalization";
import type { AnalysisRun } from "../../api/types";

export interface CompareShortcutProps {
  recordingId: string;
  hasGroundTruth: boolean;
  runs: AnalysisRun[];
  onCompare: (runAId: string, runBId: string) => void;
}

/**
 * Selects exactly two completed runs for the current recording and triggers a
 * comparison. Comparison is gated on Ground Truth before navigation, so the
 * user never enters a flow that is guaranteed to fail validation.
 */
export function CompareShortcut({ recordingId, hasGroundTruth, runs, onCompare }: CompareShortcutProps) {
  const { t } = useLocalization();
  const [selected, setSelected] = useState<string[]>([]);

  const toggle = (runId: string, checked: boolean) => {
    setSelected((current) => {
      if (checked) {
        if (current.includes(runId) || current.length >= 2) return current;
        return [...current, runId];
      }
      return current.filter((id) => id !== runId);
    });
  };

  const canCompare = hasGroundTruth && selected.length === 2;

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {runs.map((run) => {
        const selectable = run.status === "completed" && run.recordingId === recordingId;
        const when = run.startedAt ?? run.createdAt;
        return (
          <Checkbox
            key={run.id}
            checked={selected.includes(run.id)}
            disabled={!selectable}
            aria-label={run.id}
            onChange={(event) => toggle(run.id, event.target.checked)}
          >
            {`${when ? new Date(when).toLocaleString() : "—"} · ${run.pipelineId}`}
          </Checkbox>
        );
      })}
      {!hasGroundTruth ? (
        <Typography.Text type="warning" data-testid="compare-gt-required">
          {t("algorithmLab.compareRequiresGroundTruth")}
        </Typography.Text>
      ) : null}
      <Button type="primary" disabled={!canCompare} onClick={() => onCompare(selected[0], selected[1])}>
        {t("common.compare")}
      </Button>
    </Space>
  );
}
