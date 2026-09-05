import { Card, Tag } from "antd";
import type { DetectionResult, GroundTruthResult, SpectrogramMeta } from "../../api/types";
import { SpectrogramViewer } from "../spectrum/SpectrogramViewer";

interface Props {
  title: string;
  color: string;
  meta: SpectrogramMeta;
  groundTruth: GroundTruthResult[];
  detections: DetectionResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
}

export function RunComparisonPanel({
  title,
  color,
  meta,
  groundTruth,
  detections,
  selectedDetectionId,
  onSelectDetection,
}: Props) {
  return (
    <Card
      size="small"
      title={<span>{title}</span>}
      extra={<Tag color={color}>{detections.length} predictions</Tag>}
    >
      <SpectrogramViewer
        meta={meta}
        detections={detections}
        groundTruth={groundTruth}
        selectedDetectionId={selectedDetectionId}
        onSelectDetection={onSelectDetection}
      />
    </Card>
  );
}