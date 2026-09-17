import { theme } from "antd";
import type { CSSProperties } from "react";

export interface LinePoint {
  x: number;
  y: number;
}

export interface LineSeries {
  label: string;
  color: string;
  points: LinePoint[];
}

export interface SignalLinePlotProps {
  series: LineSeries[];
  height?: number;
  xLabel?: string;
  yLabel?: string;
  formatX?: (value: number) => string;
  formatY?: (value: number) => string;
  ariaLabel?: string;
}

const VIEW_WIDTH = 1000;
const PAD_LEFT = 66;
const PAD_RIGHT = 16;
const PAD_TOP = 14;
const PAD_BOTTOM = 38;
const TICKS = 5;

function extent(values: number[]): [number, number] {
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) return [min - 0.5, max + 0.5];
  return [min, max];
}

function defaultFormat(value: number): string {
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e9 || magnitude < 1e-3) return value.toExponential(2);
  return value.toFixed(3);
}

/**
 * Small dependency-free SVG line plot for signal representations.
 * Autoscaling, simple axes/grid, one or two series. Parent owns empty/error UI.
 */
export function SignalLinePlot({
  series,
  height = 360,
  xLabel,
  yLabel,
  formatX = defaultFormat,
  formatY = defaultFormat,
  ariaLabel,
}: SignalLinePlotProps) {
  const { token } = theme.useToken();
  const plotWidth = VIEW_WIDTH - PAD_LEFT - PAD_RIGHT;
  const plotHeight = height - PAD_TOP - PAD_BOTTOM;

  const allPoints = series.flatMap((item) => item.points);
  if (allPoints.length === 0) return null;

  const [xMin, xMax] = extent(allPoints.map((point) => point.x));
  const [yMin, yMax] = extent(allPoints.map((point) => point.y));
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;

  const toX = (value: number) => PAD_LEFT + ((value - xMin) / xSpan) * plotWidth;
  const toY = (value: number) => PAD_TOP + (1 - (value - yMin) / ySpan) * plotHeight;

  const xTicks = Array.from({ length: TICKS }, (_v, index) => xMin + (xSpan * index) / (TICKS - 1));
  const yTicks = Array.from({ length: TICKS }, (_v, index) => yMin + (ySpan * index) / (TICKS - 1));

  const axisStyle: CSSProperties = { fill: token.colorTextSecondary, fontSize: 12 };

  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
      preserveAspectRatio="none"
      style={{ width: "100%", height, display: "block" }}
    >
      {xTicks.map((tick) => {
        const x = toX(tick);
        return (
          <g key={`x-${tick}`}>
            <line x1={x} y1={PAD_TOP} x2={x} y2={height - PAD_BOTTOM} stroke={token.colorBorderSecondary} strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <text x={x} y={height - PAD_BOTTOM + 18} textAnchor="middle" style={axisStyle}>
              {formatX(tick)}
            </text>
          </g>
        );
      })}
      {yTicks.map((tick) => {
        const y = toY(tick);
        return (
          <g key={`y-${tick}`}>
            <line x1={PAD_LEFT} y1={y} x2={VIEW_WIDTH - PAD_RIGHT} y2={y} stroke={token.colorBorderSecondary} strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <text x={PAD_LEFT - 8} y={y + 4} textAnchor="end" style={axisStyle}>
              {formatY(tick)}
            </text>
          </g>
        );
      })}
      <line x1={PAD_LEFT} y1={PAD_TOP} x2={PAD_LEFT} y2={height - PAD_BOTTOM} stroke={token.colorTextTertiary} strokeWidth={1} vectorEffect="non-scaling-stroke" />
      <line x1={PAD_LEFT} y1={height - PAD_BOTTOM} x2={VIEW_WIDTH - PAD_RIGHT} y2={height - PAD_BOTTOM} stroke={token.colorTextTertiary} strokeWidth={1} vectorEffect="non-scaling-stroke" />

      {series.map((item) => (
        <polyline
          key={item.label}
          data-testid={`line-series-${item.label}`}
          points={item.points.map((point) => `${toX(point.x)},${toY(point.y)}`).join(" ")}
          fill="none"
          stroke={item.color}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      ))}

      {xLabel ? (
        <text x={PAD_LEFT + plotWidth / 2} y={height - 4} textAnchor="middle" style={axisStyle}>
          {xLabel}
        </text>
      ) : null}
      {yLabel ? (
        <text x={14} y={PAD_TOP + plotHeight / 2} textAnchor="middle" transform={`rotate(-90 14 ${PAD_TOP + plotHeight / 2})`} style={axisStyle}>
          {yLabel}
        </text>
      ) : null}
    </svg>
  );
}
