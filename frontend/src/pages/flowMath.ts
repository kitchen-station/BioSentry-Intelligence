import type { PatientStay } from "../types";

export const FLOW_DAYS = 14;
const DAY_MS = 86_400_000;
const ms = (iso: string) => Date.parse(iso);

/** "5d 3h", "3h 5m" or "12m". */
export function formatStay(milliseconds: number): string {
  const minutes = Math.floor(milliseconds / 60_000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  return hours > 0 ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
}

/** Stays with an unknown arrival are left out of the average: their length is not known. */
export function flowStats(stays: PatientStay[], now: number) {
  const since = now - DAY_MS;
  const finished = stays.filter((s) => s.left_at !== null && s.admitted_at !== null);
  return {
    in24h: stays.filter((s) => s.admitted_at !== null && ms(s.admitted_at) > since).length,
    out24h: stays.filter((s) => s.left_at !== null && ms(s.left_at) > since).length,
    onWard: stays.filter((s) => s.left_at === null).length,
    averageMs: finished.length
      ? finished.reduce((sum, s) => sum + ms(s.left_at!) - ms(s.admitted_at!), 0) / finished.length
      : null,
  };
}

export interface Bar {
  stay: PatientStay;
  left: number; // percent of the window
  width: number;
}

/** One row per bed, oldest stay first. An unknown arrival starts where the bed was last free. */
export function bedRows(stays: PatientStay[], now: number): { bed: string; bars: Bar[] }[] {
  const span = FLOW_DAYS * DAY_MS;
  const from = now - span;
  const beds = [...new Set(stays.map((s) => s.bed))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  return beds.map((bed) => {
    const here = stays.filter((s) => s.bed === bed);
    const knownEnds = here.flatMap((s) => (s.left_at ? [ms(s.left_at)] : []));
    const bars = here.flatMap((stay): Bar[] => {
      const end = stay.left_at ? ms(stay.left_at) : now;
      if (end <= from) return [];
      const free = knownEnds.filter((t) => t <= end && t !== end);
      const start = Math.max(from, stay.admitted_at ? ms(stay.admitted_at) : Math.max(from, ...free));
      return [{ stay, left: ((start - from) / span) * 100, width: ((end - start) / span) * 100 }];
    });
    return { bed, bars: bars.sort((a, b) => a.left - b.left) };
  });
}

/** Date marks along the timeline, every two days, oldest first. */
export function dayTicks(now: number): { at: number; left: number }[] {
  const span = FLOW_DAYS * DAY_MS;
  return Array.from({ length: FLOW_DAYS / 2 }, (_, i) => ({
    at: now - span + i * 2 * DAY_MS,
    left: ((i * 2) / FLOW_DAYS) * 100,
  }));
}

/** Whoever moved most recently, newest first. */
export function recentMoves(stays: PatientStay[], count: number): PatientStay[] {
  const latest = (s: PatientStay) => ms(s.left_at ?? s.admitted_at ?? "1970-01-01T00:00:00Z");
  return stays
    .filter((s) => s.left_at !== null || s.admitted_at !== null)
    .sort((a, b) => latest(b) - latest(a))
    .slice(0, count);
}
