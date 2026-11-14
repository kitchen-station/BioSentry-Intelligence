import {
  agoLabel,
  areaPath,
  isolatedPoints,
  labelIndexes,
  linePath,
  present,
  xByIndex,
  xByTime,
  type Pt,
} from "./chartPath";
import { DrawOn, Tick, useDrawFrom } from "./DrawOn";

interface DataPoint {
  time: string; // ISO 8601. From the API: the END of the 15-minute bin. The fixture file gives its start.
  value: number | null; // null is a gap: the line breaks, nothing is bridged
}

interface Props {
  data: readonly DataPoint[];
  label: string;
  unit: string;
  width?: number;
  height?: number;
  responsive?: boolean;
  color?: string;
  warningHigh?: number;
  warningLow?: number;
  /** The patient's own baseline, as the server sent it. The chart only maps it to pixels. */
  band?: { low: number; high: number; flagged: boolean };
}

export function TrendChart({
  data,
  label,
  unit,
  width = 400,
  height = 140,
  responsive = true,
  color = "var(--data)",
  warningHigh,
  warningLow,
  band,
}: Props) {
  const draw = useDrawFrom(data.map((d) => `${d.time}|${d.value}`));
  const values = data.map((d) => d.value);
  const known = present(values);
  if (data.length < 2 || known.length === 0) return null;

  const dataMin = Math.min(...known);
  const dataMax = Math.max(...known);
  const min = Math.min(dataMin, warningLow ?? dataMin) * 0.95;
  const max = Math.max(dataMax, warningHigh ?? dataMax) * 1.05;
  const range = max - min || 1;

  const padL = 40;
  const padR = 24;
  const padT = 8;
  const padB = 24;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  // x by timestamp, so a gap is drawn as a gap; even spacing only if the times are unusable.
  const times = data.map((d) => Date.parse(d.time));
  const xs =
    xByTime(times, padL, plotW) ?? data.map((_, i) => xByIndex(i, data.length, padL, plotW));
  const yOf = (v: number) => padT + plotH - ((v - min) / range) * plotH;
  const points: (Pt | null)[] = data.map((d, i) =>
    d.value === null ? null : { x: xs[i], y: yOf(d.value) },
  );
  // The band is cut to the drawn range; wholly outside it nothing is drawn, the words stay.
  const bandTop = band && yOf(Math.min(band.high, max));
  const bandBottom = band && yOf(Math.max(band.low, min));
  const bandText =
    band && `Own baseline ${band.low} to ${band.high} ${unit}${band.flagged ? " \u00b7 Flagged" : ""}`;
  const latest = points[points.length - 1];
  const latestValue = values[values.length - 1];
  const timesUsable = xByTime(times, padL, plotW) !== null;
  const fromX = draw.from < 0 ? padL : xs[draw.from];

  // Y-axis ticks
  const ticks = 3;
  const yTicks = Array.from({ length: ticks }, (_, i) => {
    const val = min + (range * i) / (ticks - 1);
    const y = padT + plotH - (i / (ticks - 1)) * plotH;
    return { val: Math.round(val), y };
  });

  // X-axis labels (first, middle, last): how long before the latest bin
  const xLabels = labelIndexes(data.length).map((i) => ({
    key: i,
    label: timesUsable ? agoLabel((times[data.length - 1] - times[i]) / 60000) : data[i].time,
    x: xs[i],
  }));

  return (
    <div style={{ position: "relative" }}>
      <div
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: "10px",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--label)",
          marginBottom: "4px",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>
          {label}
          {bandText && <span style={{ textTransform: "none" }}>{` \u00b7 ${bandText}`}</span>}
        </span>
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink)" }}>
          {latestValue === null ? "--" : `${latestValue} ${unit}`}
        </span>
      </div>
      <svg
        width={responsive ? "100%" : width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={bandText ? `${label} trend. ${bandText}` : `${label} trend`}
      >
        {/* Grid lines */}
        {yTicks.map((t) => (
          <line
            key={t.val}
            x1={padL}
            y1={t.y}
            x2={width - padR}
            y2={t.y}
            stroke="var(--border)"
            strokeWidth={0.5}
          />
        ))}

        {/* Warning thresholds */}
        {warningHigh !== undefined && (() => {
          const y = padT + plotH - ((warningHigh - min) / range) * plotH;
          return (
            <>
              <line
                x1={padL} y1={y} x2={width - padR} y2={y}
                stroke="var(--alert)" strokeWidth={1} strokeDasharray="6,4" opacity={0.5}
              />
              <text x={width - padR + 2} y={y + 3} fontSize={8} fill="var(--alert)" fontFamily="var(--font-sans)" opacity={0.7}>
                {warningHigh}
              </text>
            </>
          );
        })()}
        {warningLow !== undefined && (() => {
          const y = padT + plotH - ((warningLow - min) / range) * plotH;
          return (
            <>
              <line
                x1={padL} y1={y} x2={width - padR} y2={y}
                stroke="var(--alert)" strokeWidth={1} strokeDasharray="6,4" opacity={0.5}
              />
              <text x={width - padR + 2} y={y + 3} fontSize={8} fill="var(--alert)" fontFamily="var(--font-sans)" opacity={0.7}>
                {warningLow}
              </text>
            </>
          );
        })()}

        {bandTop !== undefined && bandBottom !== undefined && bandTop < bandBottom && (
          <rect
            data-testid="own-baseline-band"
            x={padL}
            y={bandTop}
            width={plotW}
            height={bandBottom - bandTop}
            fill={color}
            opacity={0.15}
          />
        )}

        <DrawOn fromX={fromX} toX={width} height={height} version={draw.version}>
          <path d={areaPath(points, padT + plotH)} fill={color} opacity={0.06} />
          <path
            d={linePath(points)}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* Readings that stand alone between gaps */}
          {isolatedPoints(points).map((p) => (
            <Tick key={`${p.x},${p.y}`} x={p.x} y={p.y} color={color} />
          ))}
          {/* Live end: only when the latest bin has a reading */}
          {latest !== null && <Tick x={latest.x} y={latest.y} color={color} half={6} />}
        </DrawOn>

        {/* Y labels */}
        {yTicks.map((t) => (
          <text
            key={t.val}
            x={padL - 6}
            y={t.y + 3}
            textAnchor="end"
            fontSize={10}
            fill="var(--label)"
            fontFamily="var(--font-sans)"
          >
            {t.val}
          </text>
        ))}

        {/* X labels */}
        {xLabels.map((l) => (
          <text
            key={l.key}
            x={l.x}
            y={height - 4}
            textAnchor="middle"
            fontSize={9}
            fill="var(--label)"
            fontFamily="var(--font-sans)"
          >
            {l.label}
          </text>
        ))}
      </svg>
    </div>
  );
}
