import { useEffect, useRef } from "react";
import { trend } from "hw-ui";
import { formatClockDay } from "./format";

interface Reading {
  time: string;
  value: number | null; // null is a missing reading: the line breaks, nothing is bridged
  source: string; // "nurse" or "device"
}

const SOURCE: Record<string, string> = { nurse: "nurse", device: "device" };
const source = (s: string) => SOURCE[s] ?? s;

/** Blood pressure in mmHg: systolic and diastolic lines over the timestamped readings, each labelled with its source. */
export function PressureChart({ systolic, diastolic }: { systolic: readonly Reading[]; diastolic: readonly Reading[] }) {
  const root = useRef<HTMLDivElement>(null);
  const spec = JSON.stringify({ systolic, diastolic });
  const draw = [...systolic, ...diastolic].some((r) => r.value !== null) && systolic.length >= 2;
  useEffect(() => {
    if (!root.current || !draw) return;
    const p = JSON.parse(spec) as { systolic: Reading[]; diastolic: Reading[] };
    const lastMs = Date.parse(p.systolic[p.systolic.length - 1].time);
    const last = p.systolic[p.systolic.length - 1];
    const dia = p.diastolic.find((r) => r.time === last.time)?.value ?? null;
    const g = trend(root.current, {
      title: last.value === null ? "Blood pressure: latest reading missing" : `Blood pressure ${last.value}/${dia ?? "--"} mmHg, by ${source(last.source)}`,
      subtitle: "Cuff and nurse readings at the times taken. A missing reading is a gap, not joined.",
      x: p.systolic.map((r) => `${Number.isFinite(Date.parse(r.time)) ? formatClockDay(Date.parse(r.time), lastMs) : r.time} · ${source(r.source)}`),
      xLabel: "Time · source",
      series: [
        { name: "Systolic", values: p.systolic.map((r) => r.value) },
        { name: "Diastolic", values: p.systolic.map((r) => p.diastolic.find((d) => d.time === r.time)?.value ?? null), dashed: true },
      ],
      format: (v) => `${v} mmHg`,
      range: [40, 200],
    });
    return () => g.destroy();
  }, [spec, draw]);
  return draw ? <div ref={root} /> : null;
}
