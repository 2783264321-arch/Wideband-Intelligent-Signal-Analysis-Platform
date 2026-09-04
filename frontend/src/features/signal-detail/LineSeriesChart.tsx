interface Series {
  name: string;
  values: number[];
  dashed?: boolean;
}

interface LineSeriesChartProps {
  x: number[];
  series: Series[];
  xFormatter?: (value: number) => string;
  height?: number;
}

function normalize(value: number, min: number, max: number): number {
  if (max === min) return 0.5;
  return (value - min) / (max - min);
}

export function LineSeriesChart({ x, series, xFormatter = (value) => value.toFixed(3), height = 220 }: LineSeriesChartProps) {
  if (!x.length || !series.length) return <div style={{ minHeight: height, display: "grid", placeItems: "center" }}>No data</div>;
  const allY = series.flatMap((item) => item.values);
  const xMin = Math.min(...x);
  const xMax = Math.max(...x);
  const yMin = Math.min(...allY);
  const yMax = Math.max(...allY);

  return (
    <div>
      <svg viewBox="0 0 1000 300" width="100%" height={height} role="img" aria-label="Signal chart" style={{ border: "1px solid #f0f0f0" }}>
        {series.map((item) => {
          const points = item.values
            .slice(0, x.length)
            .map((value, index) => `${normalize(x[index], xMin, xMax) * 980 + 10},${290 - normalize(value, yMin, yMax) * 280}`)
            .join(" ");
          return (
            <polyline
              key={item.name}
              points={points}
              fill="none"
              stroke="currentColor"
              strokeOpacity={item.dashed ? 0.55 : 0.9}
              strokeDasharray={item.dashed ? "8 5" : undefined}
              strokeWidth="1.4"
              vectorEffect="non-scaling-stroke"
            />
          );
        })}
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#666" }}>
        <span>{xFormatter(xMin)}</span>
        <span>{series.map((item) => item.name).join(" / ")}</span>
        <span>{xFormatter(xMax)}</span>
      </div>
    </div>
  );
}
