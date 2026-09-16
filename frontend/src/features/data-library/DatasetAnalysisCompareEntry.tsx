import { Button, Checkbox, Space } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLocalization } from "../../localization/useLocalization";
import type { DatasetAnalysisHistoryItem } from "../../api/types";

export interface DatasetAnalysisCompareEntryProps {
  datasetProjectionId: string;
  items: DatasetAnalysisHistoryItem[];
}

/**
 * Selects exactly two dataset evaluations for comparison. Only real evaluation
 * IDs participate; an imported batch that has no DatasetEvaluation yet remains
 * visible history but is never treated as a comparison identity.
 */
export function DatasetAnalysisCompareEntry({ items }: DatasetAnalysisCompareEntryProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<string[]>([]);

  const evaluations = items.filter(
    (item) => item.kind === "evaluation" && item.status === "completed",
  );

  const toggle = (evaluationId: string, checked: boolean) => {
    setSelected((current) => {
      if (checked) {
        if (current.includes(evaluationId) || current.length >= 2) return current;
        return [...current, evaluationId];
      }
      return current.filter((value) => value !== evaluationId);
    });
  };

  if (evaluations.length < 2) return null;

  return (
    <Space direction="vertical" data-testid="dataset-analysis-compare-entry">
      {evaluations.map((item) => (
        <Checkbox
          key={item.resourceId}
          aria-label={item.resourceId}
          checked={selected.includes(item.resourceId)}
          onChange={(event) => toggle(item.resourceId, event.target.checked)}
        >
          {item.name}
        </Checkbox>
      ))}
      <Button
        type="primary"
        disabled={selected.length !== 2}
        onClick={() => navigate(`/experiments?tab=compare&a=${selected[0]}&b=${selected[1]}`)}
      >
        {t("common.compare")}
      </Button>
    </Space>
  );
}
