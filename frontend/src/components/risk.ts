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
