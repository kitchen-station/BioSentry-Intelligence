import type { Measurement, Vitals } from "../types";

// NEWS2 points per parameter (RCP 2017, Chart 1), for display only: they show WHICH vitals drive
// the server's total. news2.test.ts checks they sum to that total for every fixture patient.
// A missing value scores nothing here (I1): it is shown as missing, never as 0 points.

export type Points = 0 | 1 | 2 | 3;
export type VitalKey =
  | "respiratory_rate" | "spo2" | "supplemental_o2" | "temperature"
  | "blood_pressure" | "heart_rate" | "consciousness";

const halfUp = (v: number, dp = 0) => Math.floor(v * 10 ** dp + 0.5) / 10 ** dp;

function value<T>(m: Measurement<T>): T | null {
  return m.data.present ? (m.data.value as T) : null;
}

const rr = (v: number): Points => (v <= 8 ? 3 : v <= 11 ? 1 : v <= 20 ? 0 : v <= 24 ? 2 : 3);
const hr = (v: number): Points =>
  v <= 40 ? 3 : v <= 50 ? 1 : v <= 90 ? 0 : v <= 110 ? 1 : v <= 130 ? 2 : 3;
const sbp = (v: number): Points => (v <= 90 ? 3 : v <= 100 ? 2 : v <= 110 ? 1 : v <= 219 ? 0 : 3);
const temp = (v: number): Points => (v <= 35 ? 3 : v <= 36 ? 1 : v <= 38 ? 0 : v <= 39 ? 1 : 2);
const spo2Scale1 = (v: number): Points => (v <= 91 ? 3 : v <= 93 ? 2 : v <= 95 ? 1 : 0);
function spo2Scale2(v: number, onO2: boolean): Points {
  if (v <= 83) return 3;
  if (v <= 85) return 2;
  if (v <= 87) return 1;
  if (v <= 92 || !onO2) return 0;
  return v <= 94 ? 1 : v <= 96 ? 2 : 3;
}

/** The three vitals the patient page charts. */
export type Charted = "heart_rate" | "respiratory_rate" | "spo2";

/**
 * Where a charted vital stops scoring 0, from the tables above: [lowest 0-point value, highest]
 * (null where that side never scores). SpO2 depends on the scale, and on scale 2 on oxygen.
 * news2.test.ts checks these edges against the point functions.
 */
export function zeroBand(vital: Charted, scale: 1 | 2 = 1, onO2 = false): [number | null, number | null] {
  if (vital === "heart_rate") return [51, 90];
  if (vital === "respiratory_rate") return [12, 20];
  return scale === 2 ? [88, onO2 ? 92 : null] : [96, null];
}

const POINTS = {
  heart_rate: (v: number) => hr(halfUp(v)),
  respiratory_rate: (v: number) => rr(halfUp(v)),
  spo2: (v: number) => spo2Scale1(halfUp(v)),
  spo2Air2: (v: number) => spo2Scale2(halfUp(v), false),
  spo2O22: (v: number) => spo2Scale2(halfUp(v), true),
};
type Rule = keyof typeof POINTS;
const rule = (vital: Charted, scale: 1 | 2, onO2: boolean): Rule =>
  vital !== "spo2" || scale === 1 ? vital : onO2 ? "spo2O22" : "spo2Air2";
// One function per case, made once: a chart that receives the same rule is not rebuilt.
const ADDS = Object.fromEntries(
  Object.entries(POINTS).map(([k, points]) => [k, (v: number) => points(v) > 0]),
) as Record<Rule, (v: number) => boolean>;

/** Whether a value of a charted vital adds NEWS2 points: the stretch of a trend drawn in the highlight. */
export function addsPoints(vital: Charted, scale: 1 | 2 = 1, onO2 = false): (v: number) => boolean {
  return ADDS[rule(vital, scale, onO2)];
}

/** A chart's dashed lines: where the value starts to add points, named with the points it adds just past them. */
export function chartLimits(vital: Charted, scale: 1 | 2 = 1, onO2 = false): { at: number; label: string }[] {
  const [lo, hi] = zeroBand(vital, scale, onO2);
  const points = POINTS[rule(vital, scale, onO2)];
  const name = (n: number) => `+${n} ${n === 1 ? "point" : "points"}`;
  return [
    ...(hi !== null ? [{ at: hi, label: `${name(points(hi + 1))} above ${hi}` }] : []),
    ...(lo !== null ? [{ at: lo, label: `${name(points(lo - 1))} below ${lo}` }] : []),
  ];
}

export function news2Points(v: Vitals): Partial<Record<VitalKey, Points>> {
  const out: Partial<Record<VitalKey, Points>> = {};
  const r = value(v.respiratory_rate);
  if (r !== null) out.respiratory_rate = rr(halfUp(r));
  const h = value(v.heart_rate);
  if (h !== null) out.heart_rate = hr(halfUp(h));
  const t = value(v.temperature);
  if (t !== null) out.temperature = temp(halfUp(t, 1));
  const bp = value(v.blood_pressure);
  if (bp !== null) out.blood_pressure = sbp(halfUp(bp.systolic));
  const c = value(v.consciousness);
  if (c !== null) out.consciousness = c === "A" ? 0 : 3;
  const o2 = value(v.supplemental_o2);
  if (o2 !== null) out.supplemental_o2 = o2 ? 2 : 0;
  const s = value(v.spo2);
  const scale = value(v.spo2_scale);
  if (s !== null && scale !== null) {
    out.spo2 = scale === 2 ? spo2Scale2(halfUp(s), o2 === true) : spo2Scale1(halfUp(s));
  }
  return out;
}
