import type { ScoreRow, SessionRole } from "../types";

// NEWS2 clinical-risk bands and responses (RCP 2017, Chart 4), applied to the server's score.
// Display only: the score and the ranking stay the server's.
export type Band = "high" | "medium" | "low-medium" | "low";

export function band(row: Pick<ScoreRow, "score" | "escalation_reasons">): Band {
  if (row.score >= 7) return "high";
  if (row.score >= 5) return "medium";
  if (row.escalation_reasons.some((r) => r.text.startsWith("Single parameter at 3"))) return "low-medium";
  return "low";
}

const RESPONSE: Record<"nurse" | "physician", Record<Band, string>> = {
  nurse: {
    high: "Call the medical team now (registrar or above)",
    medium: "Inform the medical team now",
    "low-medium": "Inform the medical team for review",
    low: "Nurse assessment; set monitoring frequency",
  },
  physician: {
    high: "Emergency assessment; consider critical care",
    medium: "Urgent assessment",
    "low-medium": "Review; decide whether to escalate",
    low: "No medical review triggered",
  },
};

export function response(b: Band, role: SessionRole): string {
  return RESPONSE[role === "md" ? "physician" : "nurse"][b];
}

/** Shown only on a flagged low-band row: a drift reason never earns a NEWS2 band or response. */
export function driftResponse(role: SessionRole, score: number): string {
  const step = role === "md" ? "no medical review triggered" : score === 0 ? "routine monitoring" : "nurse assessment";
  return `Not a NEWS2 trigger. NEWS2 response: ${step}`;
}

// RCP 2017 Chart 3, minimum observation frequency per band. "low" splits: score 0 is 12-hourly,
// 1-4 is 4-6-hourly (the stricter 4 h is used). High risk is continuous monitoring.
const OBS_INTERVAL_MIN: Record<Exclude<Band, "high">, number> = { medium: 60, "low-medium": 60, low: 240 };

/**
 * When the next full set of observations is due, in epoch ms, or "continuous" for a high-risk
 * patient. The clock starts at the oldest nurse-entered NEWS2 input: BP and consciousness, and
 * heart rate, respiratory rate, SpO2 or temperature when a nurse typed it. The patch streams
 * the rest. A missing input is never observed, so the set is due now (I1).
 */
export function obsDue(row: Pick<ScoreRow, "score" | "escalation_reasons" | "vitals">): number | "continuous" {
  const b = band(row);
  if (b === "high") return "continuous";
  const minutes = b === "low" && row.score === 0 ? 720 : OBS_INTERVAL_MIN[b];
  const { heart_rate, respiratory_rate, spo2, temperature } = row.vitals;
  const typed = [heart_rate, respiratory_rate, spo2, temperature].filter(
    (m) => m?.data.present && m.provenance.source === "nurse",
  );
  const last = Math.min(
    ...[row.vitals.blood_pressure, row.vitals.consciousness, ...typed].map((m) =>
      m!.data.present ? Date.parse(m!.provenance.observed_at) : -Infinity,
    ),
  );
  return last + minutes * 60_000;
}

/**
 * High-risk patients the clinician has not acknowledged at their current score. An
 * acknowledgement holds until the score rises past the one acknowledged.
 */
export function unacknowledged<R extends Pick<ScoreRow, "id" | "score" | "escalation_reasons">>(
  rows: readonly R[],
  acked: ReadonlyMap<string, number>,
): R[] {
  return rows.filter((r) => band(r) === "high" && r.score > (acked.get(r.id) ?? -1));
}
