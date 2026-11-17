import { useEffect, useRef } from "react";
import { trend } from "hw-ui";
import { shown } from "./BaselinePanel";
import { formatClockDay } from "./format";

interface DataPoint {
  time: string; // ISO 8601. From the API: the END of the 15-minute bin. The fixture file gives its start.
  value: number | null; // null is a gap: the line breaks, nothing is bridged
}

interface Props {
  data: readonly DataPoint[];
  label: string;
  unit: string;
  warningHigh?: number;
  warningLow?: number;
  /** The patient's own baseline, as the server sent it. */
  band?: { low: number; high: number; flagged: boolean };
}

const text = (v: number, unit: string) => `${shown("", v)} ${unit}`;

// The kit graph draws no threshold line and no band, so both are said in the subtitle.
function subtitle({ unit, warningHigh: hi, warningLow: lo, band }: Props): string | undefined {
  const warn =
    hi !== undefined && lo !== undefined
      ? `Warning above ${hi} or below ${lo} ${unit}`
      : hi !== undefined
        ? `Warning above ${hi} ${unit}`
        : lo !== undefined
          ? `Warning below ${lo} ${unit}`
          : null;
  const own = band && `Own baseline ${band.low} to ${band.high} ${unit}${band.flagged ? " · Flagged" : ""}`;
  return [warn, own].filter(Boolean).join(" · ") || undefined;
}

// The title states the finding: the latest value, past a warning line or not, and where it sits
// against the patient's own baseline.
function title({ data, label, unit, warningHigh: hi, warningLow: lo, band }: Props): string {
  const last = data[data.length - 1].value;
  if (last === null) return `${label}: latest 15-minute set missing`;
  const warn = hi !== undefined && last > hi ? "above the warning line" : lo !== undefined && last < lo ? "below the warning line" : null;
  const own = band && `${last > band.high ? "above" : last < band.low ? "below" : "within"} own baseline`;
  const found = [warn, own].filter(Boolean).join(" and ");
  return `${label} ${text(last, unit)}${found ? `, ${found}` : ""}`;
}

/** One vital over the stay, on the kit's trend graph (readout sentence, arrow keys, "Show as a table"). */
export function TrendChart(props: Props) {
  const root = useRef<HTMLDivElement>(null);
  const { data } = props;
  const drawn = data.length >= 2 && data.some((d) => d.value !== null);
  // Rebuilt only when what it shows changes: a timed read with the same figures leaves it alone.
  const spec = JSON.stringify(props);
  useEffect(() => {
    if (!root.current) return;
    const p = JSON.parse(spec) as Props;
    const lastMs = Date.parse(p.data[p.data.length - 1].time);
    const g = trend(root.current, {
      title: title(p),
      subtitle: subtitle(p),
      x: p.data.map((d) => (Number.isFinite(Date.parse(d.time)) ? formatClockDay(Date.parse(d.time), lastMs) : d.time)),
      xLabel: "Time",
      series: [{ name: p.label, values: p.data.map((d) => d.value) }],
      format: (v) => text(v, p.unit),
    });
    return () => g.destroy();
  }, [spec]);
  return drawn ? <div ref={root} /> : null;
}
