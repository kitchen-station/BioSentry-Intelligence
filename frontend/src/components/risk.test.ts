import { describe, expect, it } from "vitest";
import { band, driftResponse, obsDue, response, unacknowledged } from "./risk";

const r = (score: number, ...texts: string[]) => ({
  score,
  escalation_reasons: texts.map((text) => ({ text, rule: "tier_a_threshold" as const, pathway: null })),
});

describe("NEWS2 band", () => {
  it("follows the RCP thresholds", () => {
    expect(band(r(9))).toBe("high");
    expect(band(r(7))).toBe("high");
    expect(band(r(6))).toBe("medium");
    expect(band(r(5))).toBe("medium");
    expect(band(r(3, "Single parameter at 3: systolic BP"))).toBe("low-medium");
    expect(band(r(4))).toBe("low");
    expect(band(r(0))).toBe("low");
  });

  it("words the response for the signed-in role", () => {
    expect(response("high", "rn")).toMatch(/medical team/);
    expect(response("high", "charge_rn")).toBe(response("high", "rn"));
    expect(response("high", "md")).toMatch(/Emergency assessment/);
  });
});

describe("observations due", () => {
  const at = (iso: string) => ({ data: { present: true, value: 0 }, provenance: { observed_at: iso } });
  const row = (score: number, bpAt: string, avpuAt: string, ...texts: string[]) =>
    ({ ...r(score, ...texts), vitals: { blood_pressure: at(bpAt), consciousness: at(avpuAt) } }) as unknown as Parameters<typeof obsDue>[0];
  const t0 = "2026-09-03T06:00:00Z";
  const plus = (min: number) => Date.parse(t0) + min * 60_000;

  it("follows the RCP minimum frequency, from the oldest nurse-entered input", () => {
    expect(obsDue(row(7, t0, t0))).toBe("continuous");
    expect(obsDue(row(5, t0, "2026-09-03T06:30:00Z"))).toBe(plus(60));
    expect(obsDue(row(3, t0, t0, "Single parameter at 3: systolic BP"))).toBe(plus(60));
    expect(obsDue(row(2, t0, t0))).toBe(plus(240));
    expect(obsDue(row(0, t0, t0))).toBe(plus(720));
  });

  it("starts the clock at a nurse-typed patch value too", () => {
    const typed = { data: { present: true as const, value: 80 }, provenance: { source: "nurse", observed_at: "2026-09-03T08:00:00Z" } };
    const patch = { data: { present: true as const, value: 80 }, provenance: { source: "device", observed_at: "2026-09-03T01:00:00Z" } };
    const r2 = {
      ...r(2),
      vitals: {
        heart_rate: typed, respiratory_rate: typed, spo2: typed, temperature: typed,
        blood_pressure: at("2026-09-03T11:00:00Z"), consciousness: at("2026-09-03T11:00:00Z"),
      },
    } as unknown as Parameters<typeof obsDue>[0];
    expect(obsDue(r2)).toBe(Date.parse("2026-09-03T12:00:00Z"));
    const r3 = { ...r2, vitals: { ...r2.vitals, heart_rate: patch, respiratory_rate: patch, spo2: patch, temperature: patch } } as unknown as typeof r2;
    expect(obsDue(r3)).toBe(Date.parse("2026-09-03T15:00:00Z"));
  });

  it("is due now when a nurse-entered input was never observed", () => {
    const missing = { data: { present: false, reason: "not recorded" }, provenance: { observed_at: t0 } };
    const r0 = { ...r(2), vitals: { blood_pressure: missing, consciousness: at(t0) } } as unknown as Parameters<typeof obsDue>[0];
    expect(obsDue(r0)).toBe(-Infinity);
  });
});

describe("unacknowledged high-risk alerts", () => {
  const row = (id: string, score: number) => ({ id, ...r(score) });

  it("alerts high risk only, until acknowledged, and again when the score rises", () => {
    const rows = [row("a", 9), row("b", 7), row("c", 5)];
    expect(unacknowledged(rows, new Map()).map((x) => x.id)).toEqual(["a", "b"]);
    expect(unacknowledged(rows, new Map([["a", 9]])).map((x) => x.id)).toEqual(["b"]);
    expect(unacknowledged([row("a", 10)], new Map([["a", 9]])).map((x) => x.id)).toEqual(["a"]);
    expect(unacknowledged([row("a", 8)], new Map([["a", 9]]))).toEqual([]);
  });
});

describe("an own-baseline drift reason", () => {
  const drift = { text: "Drifting from own baseline: heart rate, 3 h (synthetic, not validated)", rule: "tier_b_finding" as const, pathway: "circulatory" };
  const flagged = (id: string, score: number) => ({ id, score, escalation_reasons: [drift] });

  it("gives the same band as without it", () => {
    for (const score of [0, 4, 5, 7]) expect(band(flagged("a", score))).toBe(band(r(score)));
  });

  it("does not put a flagged row in the acknowledge strip", () => {
    expect(unacknowledged([flagged("a", 4), flagged("b", 6), flagged("c", 8)], new Map()).map((x) => x.id)).toEqual(["c"]);
  });

  it("words the role line without a NEWS2 response", () => {
    expect(driftResponse("rn", 2)).toBe("Not a NEWS2 trigger. NEWS2 response: nurse assessment");
    expect(driftResponse("charge_rn", 2)).toBe(driftResponse("rn", 2));
    expect(driftResponse("rn", 0)).toBe("Not a NEWS2 trigger. NEWS2 response: routine monitoring");
    expect(driftResponse("charge_rn", 0)).toBe(driftResponse("rn", 0));
    expect(driftResponse("md", 0)).toBe("Not a NEWS2 trigger. NEWS2 response: no medical review triggered");
    expect(driftResponse("md", 3)).toBe(driftResponse("md", 0));
  });
});
