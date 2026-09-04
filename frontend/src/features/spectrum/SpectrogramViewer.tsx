import { Button, Space } from "antd";
import { useRef, useState } from "react";
import type { CSSProperties, MouseEvent, WheelEvent } from "react";
import type { DetectionResult, SpectrogramMeta } from "../../api/types";
import { frequencyToPercentFromTop, timeToPercent } from "./coordinates";

interface SpectrogramViewerProps {
  meta: SpectrogramMeta;
  detections: DetectionResult[];
  selectedDetectionId?: string;
  onSelectDetection?: (id: string) => void;
}

const frameStyle: CSSProperties = {
  position: "relative",
  width: "100%",
  aspectRatio: "16 / 8",
  overflow: "hidden",
  borderRadius: 8,
  border: "1px solid #d9d9d9",
  background: "#0b0f19",
  cursor: "crosshair",
};

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);

export function SpectrogramViewer({
  meta,
  detections,
  selectedDetectionId,
  onSelectDetection,
}: SpectrogramViewerProps) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragOrigin, setDragOrigin] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [cursor, setCursor] = useState<{ timeS: number; frequencyHz: number } | null>(null);
  const frameRef = useRef<HTMLDivElement>(null);

  const resetView = () => {
    setZoom(1);
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

  const onWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.2 : 1 / 1.2;
    setZoom((current) => clamp(current * factor, 1, 8));
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
        onWheel={onWheel}
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
              alt={`${meta.representation.toUpperCase()} spectrogram`}
              draggable={false}
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "fill", userSelect: "none" }}
            />
          ) : null}
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-label="Detection overlays"
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
          >
            {detections.map((detection) => {
              const x = timeToPercent(detection.tStartS, meta.tStartS, meta.tEndS);
              const x2 = timeToPercent(detection.tEndS, meta.tStartS, meta.tEndS);
              const y = frequencyToPercentFromTop(detection.fHighHz, meta.fLowHz, meta.fHighHz);
              const y2 = frequencyToPercentFromTop(detection.fLowHz, meta.fLowHz, meta.fHighHz);
              const selected = detection.id === selectedDetectionId;
              return (
                <g
                  key={detection.id}
                  aria-label={`Select ${detection.id}`}
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
                    x={x}
                    y={y}
                    width={Math.max(x2 - x, 0.4)}
                    height={Math.max(y2 - y, 0.8)}
                    fill="transparent"
                    stroke={selected ? "#ffffff" : "#ffd666"}
                    strokeWidth={selected ? 0.8 : 0.45}
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              );
            })}
          </svg>
        </div>
      </div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginTop: 8 }}>
        <span>{meta.tStartS.toFixed(6)} s</span>
        <span data-testid="cursor-readout">
          {cursor ? `${cursor.timeS.toFixed(6)} s · ${(cursor.frequencyHz / 1e6).toFixed(3)} MHz` : "Move pointer to inspect time / frequency"}
        </span>
        <Space size="small">
          <span data-testid="zoom-readout">{zoom.toFixed(2)}×</span>
          <Button size="small" onClick={resetView}>Reset View</Button>
        </Space>
      </Space>
    </div>
  );
}
