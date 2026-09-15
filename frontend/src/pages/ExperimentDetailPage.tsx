import { useParams } from "react-router-dom";

export function ExperimentDetailPage() {
  const { experimentId = "" } = useParams();
  return (
    <div data-testid="experiment-detail-page">
      <h2>Experiment {experimentId}</h2>
    </div>
  );
}
