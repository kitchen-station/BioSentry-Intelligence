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
