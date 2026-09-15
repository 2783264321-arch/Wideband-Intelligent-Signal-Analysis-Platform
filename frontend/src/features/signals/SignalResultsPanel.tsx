import { Button, List, Progress, Typography } from "antd";
import type { DetectionResult } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";

interface SignalResultsPanelProps {
  detections: DetectionResult[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onViewDetails: (id: string) => void;
  onViewAll: () => void;
}

export function SignalResultsPanel({ detections, selectedId, onSelect, onViewDetails, onViewAll }: SignalResultsPanelProps) {
  const { t } = useLocalization();
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <Typography.Title level={5} style={{ margin: 0 }}>{t("spectrum.detectedSignals")}</Typography.Title>
        <Button size="small" onClick={onViewAll}>{t("common.viewAll")}</Button>
      </div>
      <List
        dataSource={detections}
        renderItem={(item) => (
          <List.Item
            onClick={() => onSelect(item.id)}
            style={{ cursor: "pointer", paddingInline: 8, background: item.id === selectedId ? "#f0f5ff" : undefined }}
          >
            <List.Item.Meta
              title={`${item.className} · ${(item.confidence * 100).toFixed(1)}%`}
              description={
                <>
                  <Progress percent={Math.round(item.confidence * 100)} size="small" showInfo={false} />
                  <Button type="link" size="small" style={{ padding: 0 }} onClick={(event) => { event.stopPropagation(); onViewDetails(item.id); }}>
                    {t("common.viewDetails")}
                  </Button>
                </>
              }
            />
          </List.Item>
        )}
      />
    </div>
  );
}
