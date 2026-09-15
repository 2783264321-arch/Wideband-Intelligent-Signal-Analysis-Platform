import { useParams } from "react-router-dom";
import { ExperimentDetail } from "../features/dataset-experiment/ExperimentDetail";

export function ExperimentDetailPage() {
  const { experimentId = "" } = useParams();
  return (
    <div data-testid="experiment-detail-page">
      <ExperimentDetail experimentId={experimentId} />
    </div>
  );
}
