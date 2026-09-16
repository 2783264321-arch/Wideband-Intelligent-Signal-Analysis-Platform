import { Button, Space, theme } from "antd";
import { useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";
import type { DetectionResult, GroundTruthResult, SpectrogramMeta } from "../../api/types";
import { frequencyToPercentFromTop, timeToPercent } from "./coordinates";
import { useLocalization } from "../../localization/useLocalization";
import {
  FIT_ZOOM,
  viewerAspectRatio,
  zoomStep,
} from "./viewerGeometry";

interface SpectrogramViewerProps {
  meta: SpectrogramMeta;
  detections: DetectionResult[];
  groundTruth?: GroundTruthResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
}

// Size concepts (do not conflate):
//   model input size        owned by the detection model; never applied here
//   spectrogram raster size the intrinsic image dimensions (naturalWidth/Height)
//   browser display size    the CSS box of this frame
const baseFrameStyle: CSSProperties = {
  position: "relative",
  width: "100%",
  overflow: "hidden",
  borderRadius: 8,
  background: "#0b0f19",
  cursor: "crosshair",
};

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);

function boxGeometry(box: { tStartS: number; tEndS: number; fLowHz: number; fHighHz: number }, meta: SpectrogramMeta) {
  const x = timeToPercent(box.tStartS, meta.tStartS, meta.tEndS);
  const x2 = timeToPercent(box.tEndS, meta.tStartS, meta.tEndS);
  const y = frequencyToPercentFromTop(box.fHighHz, meta.fLowHz, meta.fHighHz);
  const y2 = frequencyToPercentFromTop(box.fLowHz, meta.fLowHz, meta.fHighHz);
  return { x, y, width: Math.max(x2 - x, 0.4), height: Math.max(y2 - y, 0.8) };
}

export function SpectrogramViewer({
  meta,
  detections,
  groundTruth = [],
  selectedDetectionId,
  onSelectDetection,
}: SpectrogramViewerProps) {
  const { t } = useLocalization();
  const { token } = theme.useToken();
  const [zoom, setZoom] = useState(FIT_ZOOM);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragOrigin, setDragOrigin] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [cursor, setCursor] = useState<{ timeS: number; frequencyHz: number } | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);

  const frameStyle: CSSProperties = {
    ...baseFrameStyle,
    aspectRatio: String(viewerAspectRatio(naturalSize)),
    border: `1px solid ${token.colorBorderSecondary}`,
  };

  const resetView = () => {
    setZoom(FIT_ZOOM);
    setPan({ x: 0, y: 0 });
  };

  const updateCursor = (event: MouseEvent<HTMLDivElement>) => {
    const frame = frameRef.current;
    if (!frame) return;
    const rect = frame.getBoundingClientRect();
    const contentX = (event.clientX - rect.left - pan.x) / zoom;
    const contentY = (event.clientY - rect.top - pan.y) / zoom;
    const xFraction = clamp(contentX / rect.width, 0, 1);
    const yFraction = clamp(contentY / rect.height, 0, 1);
    setCursor({
      timeS: meta.tStartS + xFraction * (meta.tEndS - meta.tStartS),
      frequencyHz: meta.fHighHz - yFraction * (meta.fHighHz - meta.fLowHz),
    });
  };

  const onMouseMove = (event: MouseEvent<HTMLDivElement>) => {
    if (dragOrigin) {
      setPan({
        x: dragOrigin.panX + event.clientX - dragOrigin.x,
        y: dragOrigin.panY + event.clientY - dragOrigin.y,
      });
    }
    updateCursor(event);
  };

  return (
    <div>
      <div
        ref={frameRef}
        style={frameStyle}
        data-testid="spectrogram-viewer"
        onMouseDown={(event) => setDragOrigin({ x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y })}
        onMouseMove={onMouseMove}
        onMouseUp={() => setDragOrigin(null)}
        onMouseLeave={() => {
          setDragOrigin(null);
          setCursor(null);
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            transformOrigin: "0 0",
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
          {meta.imageUrl ? (
            <img
              src={meta.imageUrl}
              alt={t("spectrum.spectrogramAlt", { representation: meta.representation.toUpperCase() })}
              draggable={false}
              onLoad={(event) =>
                setNaturalSize({
                  width: event.currentTarget.naturalWidth,
                  height: event.currentTarget.naturalHeight,
                })
              }
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "fill", userSelect: "none" }}
            />
          ) : null}
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-label={t("spectrum.detectionOverlays")}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
          >
            {groundTruth.map((item) => {
              const geometry = boxGeometry(item, meta);
              return (
                <rect
                  key={`gt-${item.id}`}
                  data-testid={`overlay-gt-${item.id}`}
                  data-overlay="ground-truth"
                  {...geometry}
                  fill="transparent"
                  stroke={token.colorSuccess}
                  strokeWidth={0.45}
                  strokeDasharray="1.4 1"
                  vectorEffect="non-scaling-stroke"
                  aria-label={t("spectrum.groundTruthBox", { id: item.id })}
                />
              );
            })}
            {detections.map((detection) => {
              const geometry = boxGeometry(detection, meta);
              const selected = detection.id === selectedDetectionId;
              return (
                <g
                  key={detection.id}
                  aria-label={t("spectrum.selectDetection", { id: detection.id })}
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectDetection?.(detection.id);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") onSelectDetection?.(detection.id);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <rect
                    data-testid={`overlay-det-${detection.id}`}
                    data-overlay="prediction"
                    data-selected={selected ? "true" : "false"}
                    {...geometry}
                    fill="transparent"
                    stroke={selected ? token.colorError : token.colorWarning}
                    strokeWidth={selected ? 0.9 : 0.45}
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              );
            })}
          </svg>
        </div>
      </div>
      <div
        data-testid="spectrogram-legend"
        style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 8, color: token.colorTextSecondary, fontSize: 12 }}
      >
        <span>{t("spectrum.legend")}</span>
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill="transparent" stroke={token.colorSuccess} strokeWidth="2" strokeDasharray="3 2" />
          </svg>
          {t("spectrum.legendGroundTruth")}
        </span>
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill="transparent" stroke={token.colorWarning} strokeWidth="2" />
          </svg>
          {t("spectrum.legendPrediction")}
        </span>
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill="transparent" stroke={token.colorError} strokeWidth="3" />
          </svg>
          {t("spectrum.legendSelected")}
        </span>
      </div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginTop: 8 }}>
        <span style={{ color: token.colorTextSecondary }}>{meta.tStartS.toFixed(6)} s</span>
        <span data-testid="cursor-readout" style={{ color: token.colorTextSecondary }}>
          {cursor ? `${cursor.timeS.toFixed(6)} s · ${(cursor.frequencyHz / 1e6).toFixed(3)} MHz` : t("spectrum.cursorHint")}
        </span>
        <Space size="small">
          <Button size="small" aria-label={t("spectrum.zoomOut")} onClick={() => setZoom((current) => zoomStep(current, "out"))}>
            −
          </Button>
          <span
            data-testid="zoom-readout"
            aria-label={t("spectrum.zoomLevel", { percent: Math.round(zoom * 100) })}
          >
            {zoom.toFixed(2)}×
          </span>
          <Button size="small" aria-label={t("spectrum.zoomIn")} onClick={() => setZoom((current) => zoomStep(current, "in"))}>
            +
          </Button>
          <Button
            size="small"
            aria-label={t("spectrum.zoomFit")}
            onClick={() => {
              setZoom(FIT_ZOOM);
              setPan({ x: 0, y: 0 });
            }}
          >
            {t("spectrum.zoomFit")}
          </Button>
          <Button
            size="small"
            onClick={() => {
              resetView();
              setCursor(null);
            }}
          >
            {t("spectrum.resetView")}
          </Button>
        </Space>
      </Space>
    </div>
  );
}
