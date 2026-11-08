import { describe, expect, it } from "vitest";
import { band, response } from "./risk";

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
