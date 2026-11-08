import { describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { ScoreRow } from "../types";
import { news2Points } from "./news2";

const ranked = (synthetic as unknown as { ranked: ScoreRow[] }).ranked;

describe("per-vital NEWS2 points", () => {
  it("sum to the server's NEWS2 total for every ranked fixture patient", () => {
    for (const row of ranked) {
      const points: number[] = Object.values(news2Points(row.vitals));
      expect(points.reduce((a, b) => a + b, 0), row.id).toBe(row.score);
    }
  });

  it("flags the single parameter the server named", () => {
    const named = ranked.filter((r) =>
      r.escalation_reasons.some((e) => e.text === "Single parameter at 3: systolic BP"),
    );
    expect(named.length).toBeGreaterThan(0);
    for (const row of named) expect(news2Points(row.vitals).blood_pressure, row.id).toBe(3);
  });
});
