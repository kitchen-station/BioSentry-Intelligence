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

/** One row per bed used in the window, oldest stay first. An unknown arrival starts where the bed was last free. */
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
  }).filter((row) => row.bars.length > 0);
}

/** Date marks along the timeline, every two days, oldest first. */
export function dayTicks(now: number): { at: number; left: number }[] {
  const span = FLOW_DAYS * DAY_MS;
  return Array.from({ length: FLOW_DAYS / 2 }, (_, i) => ({
    at: now - span + i * 2 * DAY_MS,
    left: ((i * 2) / FLOW_DAYS) * 100,
  }));
}

/** Whoever moved (arrived or left) since `since`, newest first. A stay with no known move is left out. */
export function recentMoves(stays: PatientStay[], since: number): PatientStay[] {
  const latest = (s: PatientStay) => ms(s.left_at ?? s.admitted_at ?? "1970-01-01T00:00:00Z");
  return stays.filter((s) => latest(s) > since).sort((a, b) => latest(b) - latest(a));
}

/**
 * Patients on the ward at this time of day on each of the last FLOW_DAYS days, oldest first, for the days the stays
 * can count: none while a patient whose arrival is not recorded was (or may have been) on the ward, and none before
 * the first recorded arrival. So the series is the latest run of countable days, and may be empty.
 */
export function occupancy(stays: PatientStay[], now: number): { at: number; count: number }[] {
  const unknownUntil = Math.max(...stays.map((s) => (s.admitted_at !== null ? -Infinity : s.left_at ? ms(s.left_at) : Infinity)));
  const firstIn = Math.min(...stays.flatMap((s) => (s.admitted_at ? [ms(s.admitted_at)] : [])));
  return Array.from({ length: FLOW_DAYS }, (_, i) => now - (FLOW_DAYS - 1 - i) * DAY_MS)
    .filter((at) => at >= unknownUntil && at >= firstIn)
    .map((at) => ({
      at,
      count: stays.filter((s) => s.admitted_at !== null && ms(s.admitted_at) <= at && (s.left_at === null || ms(s.left_at) > at)).length,
    }));
}
