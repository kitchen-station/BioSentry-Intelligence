import { areaPath, isolatedPoints, linePath, present, xByIndex, type Pt } from "./chartPath";
import { DrawOn, Tick, useDrawFrom } from "./DrawOn";

interface Props {
  // Oldest first, evenly spaced bins. null is a gap: the line breaks, nothing is bridged.
  data: readonly (number | null)[];
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  className?: string;
}

export function Sparkline({
  data,
  width = 80,
  height = 24,
  color = "var(--data)",
  fill = false,
  className,
}: Props) {
  const draw = useDrawFrom(data);
  const values = present(data);
  if (values.length === 0) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 2;

  const points: (Pt | null)[] = data.map((v, i) =>
    v === null
      ? null
      : {
          x: xByIndex(i, data.length, pad, width - pad * 2),
          y: height - pad - ((v - min) / range) * (height - pad * 2),
        },
  );
  const latest = points[points.length - 1];
  const fromX = draw.from < 0 ? 0 : xByIndex(draw.from, data.length, pad, width - pad * 2);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      style={{ display: "block" }}
    >
      <DrawOn fromX={fromX} toX={width} height={height} version={draw.version}>
        {fill && <path d={areaPath(points, height - pad)} fill={color} opacity={0.08} />}
        <path
          d={linePath(points)}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {isolatedPoints(points).map((p) => (
          <Tick key={`${p.x},${p.y}`} x={p.x} y={p.y} color={color} half={3} />
        ))}
        {/* Live end: only when the latest bin has a reading */}
        {latest !== null && <Tick x={latest.x} y={latest.y} color={color} half={3} />}
      </DrawOn>
    </svg>
  );
}
