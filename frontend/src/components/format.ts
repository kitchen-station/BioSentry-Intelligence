import type { BloodPressure, Spo2Scale } from "../types";

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
