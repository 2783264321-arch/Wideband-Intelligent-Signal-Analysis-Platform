import { Button, Space, theme } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, MouseEvent } from "react";
import type { DetectionResult, GroundTruthResult, SpectrogramMeta } from "../../api/types";
import { frequencyToPercentFromTop, timeToPercent } from "./coordinates";
import { useLocalization } from "../../localization/useLocalization";
import {
  FIT_ZOOM,
  VIEWER_VIEWPORT_HEIGHT,
  zoomStep,
} from "./viewerGeometry";

interface SpectrogramViewerProps {
  meta: SpectrogramMeta;
  detections: DetectionResult[];
  groundTruth?: GroundTruthResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
  /** Pure representation hides the GT/Prediction/Selected overlay legend. */
  showOverlayLegend?: boolean;
  /**
   * Whether the orange prediction boxes are drawn. The SELECTED-detection
   * highlight (red) is intentionally independent of this toggle: selecting a row
   * in the results list must always be visible on the spectrogram, even when the
   * user has turned the general detection overlay off.
   */
  showDetections?: boolean;
}

// Size concepts (do not conflate):
//   model input size        owned by the detection model; never applied here
//   spectrogram raster size the intrinsic image dimensions (naturalWidth/Height)
//   browser display size    the CSS box of this frame
//
// The display viewport is deliberately raster-independent: height is a
// responsive analysis workspace size, never naturalWidth/naturalHeight.
const baseFrameStyle: CSSProperties = {
  position: "relative",
  width: "100%",
  height: VIEWER_VIEWPORT_HEIGHT,
  overflow: "hidden",
  borderRadius: 8,
  background: "#0b0f19",
  cursor: "crosshair",
};

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);

/**
 * Density guards. A wideband scene can legitimately carry hundreds of signals
 * (and a permissive detector thousands of boxes): drawing every one of them, with
 * per-box numbering, produces an unreadable mush. Above these limits the overlay
 * keeps the strongest detections and drops GT numbering, and the legend says so.
 */
const DENSE_DETECTION_LIMIT = 300;
const DENSE_DETECTION_KEEP = 200;
const MAX_GROUND_TRUTH_NUMBERS = 30;
/** Extra fraction of the visible span kept when culling to the viewport. */
const VIEWPORT_MARGIN = 0.05;

interface VisibleWindow {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

function intersects(window: VisibleWindow, box: { x: number; y: number; width: number; height: number }): boolean {
  return (
    box.x + box.width >= window.x0 &&
    box.x <= window.x1 &&
    box.y + box.height >= window.y0 &&
    box.y <= window.y1
  );
}

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
  showOverlayLegend = true,
  showDetections = true,
}: SpectrogramViewerProps) {
  const { t } = useLocalization();
  const { token } = theme.useToken();
  const [zoom, setZoom] = useState(FIT_ZOOM);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragOrigin, setDragOrigin] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [cursor, setCursor] = useState<{ timeS: number; frequencyHz: number } | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  // Measured frame size; a plain ref read would not re-run the culling memos.
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const measure = () => {
      const frame = frameRef.current;
      if (!frame) return;
      setFrameSize((current) =>
        current.width === frame.clientWidth && current.height === frame.clientHeight
          ? current
          : { width: frame.clientWidth, height: frame.clientHeight },
      );
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [zoom, pan.x, pan.y]);

  const frameStyle: CSSProperties = {
    ...baseFrameStyle,
    border: `1px solid ${token.colorBorderSecondary}`,
  };

  const resetView = () => {
    setZoom(FIT_ZOOM);
    setPan({ x: 0, y: 0 });
  };

  /**
   * Percent-space window currently visible in the viewport (or null when the whole
   * image is visible / the frame has no measurable size, e.g. in tests). Used to
   * cull overlays so a dense scene only pays for what is on screen.
   */
  const visibleWindow = useMemo<VisibleWindow | null>(() => {
    if (zoom <= FIT_ZOOM) return null;
    const { width, height } = frameSize;
    if (width <= 0 || height <= 0) return null;
    const x0 = ((-pan.x) / (width * zoom)) * 100;
    const x1 = ((width - pan.x) / (width * zoom)) * 100;
    const y0 = ((-pan.y) / (height * zoom)) * 100;
    const y1 = ((height - pan.y) / (height * zoom)) * 100;
    const spanX = (x1 - x0) * VIEWPORT_MARGIN;
    const spanY = (y1 - y0) * VIEWPORT_MARGIN;
    return { x0: x0 - spanX, x1: x1 + spanX, y0: y0 - spanY, y1: y1 + spanY };
  }, [zoom, pan.x, pan.y, frameSize]);

  const { drawnGroundTruth, groundTruthNumbered } = useMemo(() => {
    const culled = visibleWindow === null
      ? groundTruth
      : groundTruth.filter((item) => intersects(visibleWindow, boxGeometry(item, meta)));
    return { drawnGroundTruth: culled, groundTruthNumbered: culled.length <= MAX_GROUND_TRUTH_NUMBERS };
  }, [groundTruth, meta, visibleWindow]);

  const { drawnDetections, detectionsCapped } = useMemo(() => {
    const culled = visibleWindow === null
      ? detections
      : detections.filter(
          (detection) =>
            detection.id === selectedDetectionId ||
            intersects(visibleWindow, boxGeometry(detection, meta)),
        );
    if (culled.length <= DENSE_DETECTION_LIMIT) {
      return { drawnDetections: culled, detectionsCapped: false };
    }
    // Too many boxes to read anyway: keep the strongest ones (the selected box
    // always survives, so the user's current pick never disappears).
    const strongest = [...culled]
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, DENSE_DETECTION_KEEP);
    if (selectedDetectionId && !strongest.some((item) => item.id === selectedDetectionId)) {
      const selected = culled.find((item) => item.id === selectedDetectionId);
      if (selected) strongest.push(selected);
    }
    return { drawnDetections: strongest, detectionsCapped: true };
  }, [detections, meta, selectedDetectionId, visibleWindow]);

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
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "fill", userSelect: "none" }}
            />
          ) : null}
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-label={t("spectrum.detectionOverlays")}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
          >
            {drawnGroundTruth.map((item) => {
              const geometry = boxGeometry(item, meta);
              return (
                <rect
                  key={`gt-${item.id}`}
                  data-testid={`overlay-gt-${item.id}`}
                  data-overlay="ground-truth"
                  {...geometry}
                  fill="transparent"
                  stroke={token.colorWhite}
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  vectorEffect="non-scaling-stroke"
                  aria-label={t("spectrum.groundTruthBox", { id: item.id })}
                />
              );
            })}
            {drawnDetections.map((detection) => {
              const geometry = boxGeometry(detection, meta);
              const selected = detection.id === selectedDetectionId;
              if (!selected && !showDetections) return null;
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
                  // No focus ring: clicking a box in the plot must look exactly like
                  // selecting it in the results list (red box + tint), not like a
                  // browser-default black/white outline. Selection is the indicator.
                  style={{ cursor: "pointer", outline: "none" }}
                >
                  {selected ? (
                    <rect
                      data-testid={`overlay-det-glow-${detection.id}`}
                      {...geometry}
                      fill={token.colorError}
                      fillOpacity={0.14}
                      stroke="none"
                      vectorEffect="non-scaling-stroke"
                    />
                  ) : null}
                  <rect
                    data-testid={`overlay-det-${detection.id}`}
                    data-overlay="prediction"
                    data-selected={selected ? "true" : "false"}
                    {...geometry}
                    fill={selected ? "rgba(0,0,0,0)" : "transparent"}
                    stroke={selected ? token.colorError : token.colorWarning}
                    strokeWidth={selected ? 3 : 2}
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              );
            })}
          </svg>
          {/*
            Ground-truth index chips. GT order is the backend's stable physical
            order, so the numbers are reproducible; they let a user see how many
            labelled signals a sample actually contains. Rendered as HTML (not SVG
            text) because the overlay SVG is non-uniformly scaled.
          */}
          {drawnGroundTruth.length > 0 && groundTruthNumbered ? (
            <div
              data-testid="ground-truth-index-layer"
              style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
            >
              {drawnGroundTruth.map((item, index) => {
                const geometry = boxGeometry(item, meta);
                return (
                  <span
                    key={`gt-index-${item.id}`}
                    data-testid={`overlay-gt-index-${item.id}`}
                    style={{
                      position: "absolute",
                      left: `${geometry.x}%`,
                      top: `${geometry.y}%`,
                      transform: geometry.y < 4 ? "translateY(0)" : "translateY(-100%)",
                      color: token.colorWhite,
                      background: "rgba(0, 0, 0, 0.6)",
                      border: `1px solid ${token.colorWhite}`,
                      borderRadius: 3,
                      fontSize: 11,
                      lineHeight: "13px",
                      padding: "0 4px",
                      whiteSpace: "nowrap",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {index + 1}
                  </span>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>
      {showOverlayLegend && (groundTruth.length > 0 || detections.length > 0) ? (
      <div
        data-testid="spectrogram-legend"
        style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 8, color: token.colorTextSecondary, fontSize: 12 }}
      >
        <span>{t("spectrum.legend")}</span>
        {groundTruth.length > 0 ? (
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill="transparent" stroke={token.colorWhite} strokeWidth="2" strokeDasharray="4 2" />
          </svg>
          {t("spectrum.legendGroundTruth")}
        </span>
        ) : null}
        {showDetections && detections.length > 0 ? (
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill="transparent" stroke={token.colorWarning} strokeWidth="2" />
          </svg>
          {t("spectrum.legendPrediction")}
        </span>
        ) : null}
        <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <svg width="18" height="8" aria-hidden="true">
            <rect x="1" y="1" width="16" height="6" fill={token.colorError} fillOpacity={0.14} stroke={token.colorError} strokeWidth="3" />
          </svg>
          {t("spectrum.legendSelected")}
        </span>
      </div>
      ) : null}
      {detectionsCapped || (!groundTruthNumbered && drawnGroundTruth.length > 0) ? (
        <div
          data-testid="spectrogram-overlay-notes"
          style={{ marginTop: 6, color: token.colorTextSecondary, fontSize: 12 }}
        >
          {detectionsCapped
            ? t("spectrum.legendDensityNote", {
                shown: String(drawnDetections.length),
                total: String(detections.length),
              })
            : null}
          {detectionsCapped && !groundTruthNumbered && drawnGroundTruth.length > 0 ? " · " : null}
          {!groundTruthNumbered && drawnGroundTruth.length > 0
            ? t("spectrum.legendNumberingHidden")
            : null}
        </div>
      ) : null}
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
