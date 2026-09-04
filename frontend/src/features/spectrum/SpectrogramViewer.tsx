import type { CSSProperties } from "react";
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
  background:
    "linear-gradient(180deg, #101827 0%, #162c3d 25%, #2c5364 45%, #9a6b43 64%, #331a24 85%, #0b0f19 100%)",
};

export function SpectrogramViewer({
  meta,
  detections,
  selectedDetectionId,
  onSelectDetection,
}: SpectrogramViewerProps) {
  return (
    <div>
      <div style={frameStyle} data-testid="spectrogram-viewer">
        {meta.imageUrl ? (
          <img
            src={meta.imageUrl}
            alt={`${meta.representation.toUpperCase()} spectrogram`}
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "fill" }}
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
                onClick={() => onSelectDetection?.(detection.id)}
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
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 12, color: "#666" }}>
        <span>{meta.tStartS.toFixed(3)} s</span>
        <span>{(meta.fLowHz / 1e9).toFixed(4)}–{(meta.fHighHz / 1e9).toFixed(4)} GHz</span>
        <span>{meta.tEndS.toFixed(3)} s</span>
      </div>
    </div>
  );
}
