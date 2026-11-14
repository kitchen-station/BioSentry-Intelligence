import type { BloodPressure, Spo2Scale, Vitals } from "../types";

/**
 * "88/52": the display text of a structured blood pressure. Display only: the string is
 * never parsed back into numbers (D10).
 */
export function formatBloodPressure(bp: BloodPressure): string {
  return `${bp.systolic}/${bp.diastolic}`;
}

/** The NEWS2 air-or-oxygen input, in words rather than a bare true/false. */
export function formatOxygen(onOxygen: boolean): string {
  return onOxygen ? "On oxygen" : "Room air";
}

/** The recorded NEWS2 SpO2 scoring scale (scale 2 is a clinician's decision). */
export function formatSpo2Scale(scale: Spo2Scale): string {
  return `Scale ${scale}`;
}

// Device data older than this is stale: the row's vitals may no longer be current.
export const STALE_AFTER_S = 300;

export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

/** "07:40": wall-clock hour and minute, for due times and last-update stamps. */
export function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * "Entered by a nurse at 07:40", with the day when it is not today: blood pressure and
 * consciousness do not expire, so an old entry must not read as today's.
 */
export function enteredByNurse(ms: number, now: number): string {
  const day = new Date(ms).toDateString() === new Date(now).toDateString()
    ? ""
    : `, ${new Date(ms).toLocaleDateString([], { day: "numeric", month: "short" })}`;
  return `Entered by a nurse at ${formatClock(ms)}${day}`;
}

/**
 * "Entered by a nurse at 07:40" when a patch value (heart rate, respiratory rate, SpO2 or
 * temperature) was typed by a nurse. It names the earliest time, the one that expires first.
 */
export function nurseEntryText(vitals: Vitals, now: number): string | null {
  const times = [vitals.heart_rate, vitals.respiratory_rate, vitals.spo2, vitals.temperature]
    .filter((m) => m !== null && m.data.present && m.provenance.source === "nurse")
    .map((m) => Date.parse(m!.provenance.observed_at));
  return times.length > 0 ? enteredByNurse(Math.min(...times), now) : null;
}

/** True when at least one patch measurement is a device value: otherwise no shown value uses the patch signal. */
export function patchInUse(vitals: Vitals): boolean {
  return [vitals.heart_rate, vitals.respiratory_rate, vitals.spo2, vitals.temperature].some(
    (m) => m !== null && m.data.present && m.provenance.source === "device",
  );
}

/** What the ward simulator publishes in the live view's `shared`, for the status line. */
export interface SimStatus {
  readonly clockS: number;
  readonly speed: 0 | 1 | 10 | 60;
  readonly ended: boolean;
  readonly counts: { readonly received: number; readonly rejected: number; readonly abstained: number };
}

const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const record = (v: unknown): Record<string, unknown> =>
  typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {};

/**
 * The simulator's status from the live view's `shared`, which the server stores and never reads:
 * anything that is not the published shape is null, and the page shows no status line.
 */
export function simStatus(shared: unknown): SimStatus | null {
  const { clockS, speed, ended, counts } = record(shared);
  const { received, rejected, abstained } = record(counts);
  if (!isCount(clockS) || typeof ended !== "boolean") return null;
  if (speed !== 0 && speed !== 1 && speed !== 10 && speed !== 60) return null;
  if (!isCount(received) || !isCount(rejected) || !isCount(abstained)) return null;
  return { clockS, speed, ended, counts: { received, rejected, abstained } };
}

/**
 * "4 Sep, 21:20 · 10x": the simulated time in the simulator's own format, then the pace.
 * shortcut: the simulator's start (15:00, 3 Sep 2026, Manila: its panels.tsx clockText/dateText) is copied here; upgrade when the simulator publishes its start in shared.
 */
export function formatSimClock(sim: SimStatus): string {
  const at = new Date(Date.UTC(2026, 8, 3, 15, Math.floor(sim.clockS / 60)));
  const date = at.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const hh = String(at.getUTCHours()).padStart(2, "0");
  const mm = String(at.getUTCMinutes()).padStart(2, "0");
  const pace = sim.ended ? "Run ended" : sim.speed === 0 ? "Paused" : `${sim.speed}x`;
  return `${date}, ${hh}:${mm} \u00b7 ${pace}`;
}

/** "22:15" for today, "13 Nov, 22:15" for another day (the same day check and locale as enteredByNurse). A `now` of 0 always gives the date. */
export function formatClockDay(ms: number, now: number): string {
  if (new Date(ms).toDateString() === new Date(now).toDateString()) return formatClock(ms);
  return `${new Date(ms).toLocaleDateString([], { day: "numeric", month: "short" })}, ${formatClock(ms)}`;
}
