import { describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { ScoreRow } from "../types";
import { addsPoints, chartLimits, news2Points, zeroBand } from "./news2";

const ranked = (synthetic as unknown as { ranked: ScoreRow[] }).ranked;

describe("per-vital NEWS2 points", () => {
  it("sum to the server's NEWS2 total for every ranked fixture patient", () => {
    for (const row of ranked) {
      const points: number[] = Object.values(news2Points(row.vitals));
      expect(points.reduce((a, b) => a + b, 0), row.id).toBe(row.score);
    }
  });

  it("draws each chart's warning lines where the point tables start to score", () => {
    for (const [vital, scale, onO2] of [
      ["heart_rate", 1, false], ["respiratory_rate", 1, false], ["spo2", 1, false], ["spo2", 2, false], ["spo2", 2, true],
    ] as const) {
      const [lo, hi] = zeroBand(vital, scale, onO2);
      const adds = addsPoints(vital, scale, onO2);
      for (let v = 0; v <= 250; v++) {
        expect(adds(v), `${vital} scale ${scale}${onO2 ? " on O2" : ""} at ${v}`).toBe((lo !== null && v < lo) || (hi !== null && v > hi));
      }
      expect(addsPoints(vital, scale, onO2)).toBe(adds); // the same function each time
    }
  });

  it("names each line with the points the value adds just past it", () => {
    expect(chartLimits("heart_rate")).toEqual([{ at: 90, label: "+1 point above 90" }, { at: 51, label: "+1 point below 51" }]);
    expect(chartLimits("respiratory_rate")).toEqual([{ at: 20, label: "+2 points above 20" }, { at: 12, label: "+1 point below 12" }]);
    expect(chartLimits("spo2")).toEqual([{ at: 96, label: "+1 point below 96" }]);
    expect(chartLimits("spo2", 2)).toEqual([{ at: 88, label: "+1 point below 88" }]);
    expect(chartLimits("spo2", 2, true)).toEqual([{ at: 92, label: "+1 point above 92" }, { at: 88, label: "+1 point below 88" }]);
  });

  it("flags the single parameter the server named", () => {
    const named = ranked.filter((r) =>
      r.escalation_reasons.some((e) => e.text === "Single parameter at 3: systolic BP"),
    );
    expect(named.length).toBeGreaterThan(0);
    for (const row of named) expect(news2Points(row.vitals).blood_pressure, row.id).toBe(3);
  });
});
