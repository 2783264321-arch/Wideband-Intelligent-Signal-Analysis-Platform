import { Button, List, Pagination, Progress, Segmented, Typography, theme } from "antd";
import { useMemo, useState } from "react";
import type { DetectionResult } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import type { MessageKey } from "../../localization/types";

interface SignalResultsPanelProps {
  detections: DetectionResult[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onViewDetails: (id: string) => void;
  onViewAll: () => void;
}

const PAGE_SIZE = 20;

type SortKey = "confidence" | "frequency" | "time";

const SORT_LABEL_KEY: Record<SortKey, MessageKey> = {
  confidence: "spectrum.sortConfidence",
  frequency: "spectrum.sortFrequency",
  time: "spectrum.sortTime",
};

/**
 * Detection list for the spectrum workspace, sorted by a single user-chosen
 * physical key (confidence / center frequency / start time) and paged so a
 * pipeline that outputs thousands of boxes cannot blow up the layout.
 *排序决定展示顺序：默认按置信度从高到低，其余两个是同等地位的物理轴。
 */
export function SignalResultsPanel({ detections, selectedId, onSelect, onViewDetails, onViewAll }: SignalResultsPanelProps) {
  const { t } = useLocalization();
  const { token } = theme.useToken();
  const [sortKey, setSortKey] = useState<SortKey>("confidence");
  const [page, setPage] = useState(1);

  const sorted = useMemo(() => {
    const rows = [...detections];
    if (sortKey === "confidence") {
      rows.sort((a, b) => b.confidence - a.confidence);
    } else if (sortKey === "frequency") {
      rows.sort((a, b) => (a.fLowHz + a.fHighHz) / 2 - (b.fLowHz + b.fHighHz) / 2);
    } else {
      rows.sort((a, b) => a.tStartS - b.tStartS);
    }
    return rows;
  }, [detections, sortKey]);

  const pageCount = Math.max(1, Math.ceil(detections.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const paged = detections.length > PAGE_SIZE
    ? sorted.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)
    : sorted;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
        <Typography.Title level={5} style={{ margin: 0 }}>
          {t("spectrum.detectedSignals")}
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontSize: 12, fontWeight: 400 }}>
            {detections.length}
          </Typography.Text>
        </Typography.Title>
        <Button size="small" onClick={onViewAll}>{t("common.viewAll")}</Button>
      </div>
      <Segmented
        size="small"
        data-testid="detection-sort"
        value={sortKey}
        onChange={(next) => { setSortKey(next as SortKey); setPage(1); }}
        options={(Object.keys(SORT_LABEL_KEY) as SortKey[]).map((key) => ({
          value: key,
          label: t(SORT_LABEL_KEY[key]),
        }))}
        style={{ marginBottom: 8 }}
      />
      <List
        dataSource={paged}
        pagination={false}
        renderItem={(item) => {
          const selected = item.id === selectedId;
          return (
            <List.Item
              onClick={() => onSelect(item.id)}
              style={{
                cursor: "pointer",
                paddingInline: 8,
                background: selected ? token.colorErrorBg : undefined,
                borderInlineStart: selected ? `3px solid ${token.colorError}` : "3px solid transparent",
              }}
              data-testid={`detection-item-${item.id}`}
              data-selected={selected ? "true" : "false"}
            >
              <List.Item.Meta
                title={`${item.className} · ${(item.confidence * 100).toFixed(1)}%`}
                description={
                  <>
                    <Typography.Text type="secondary" style={{ fontSize: 12, display: "block" }}>
                      {(item.fLowHz / 1e6).toFixed(3)}–{(item.fHighHz / 1e6).toFixed(3)} MHz · {item.tStartS.toFixed(4)}–{item.tEndS.toFixed(4)} s
                    </Typography.Text>
                    <Progress percent={Math.round(item.confidence * 100)} size="small" showInfo={false} />
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0 }}
                      onClick={(event) => { event.stopPropagation(); onViewDetails(item.id); }}
                    >
                      {t("common.viewDetails")}
                    </Button>
                  </>
                }
              />
            </List.Item>
          );
        }}
      />
      {detections.length > PAGE_SIZE ? (
        <Pagination
          size="small"
          current={currentPage}
          pageSize={PAGE_SIZE}
          total={detections.length}
          onChange={setPage}
          style={{ marginTop: 8, textAlign: "center" }}
        />
      ) : null}
    </div>
  );
}
