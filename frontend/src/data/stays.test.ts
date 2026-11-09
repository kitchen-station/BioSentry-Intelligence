import { describe, expect, it } from "vitest";
import { getStays, setActiveClinician } from "./fixtures";

describe("the demo's stay history", () => {
  it("is scoped to the clinician and always synthetic", async () => {
    setActiveClinician("rn-manalo");
    const everyone = await getStays();
    setActiveClinician("rn-reyes");
    const mine = await getStays();
    expect(mine.length).toBeGreaterThan(0);
    expect(mine.length).toBeLessThan(everyone.length);
    expect(everyone.every((s) => s.synthetic)).toBe(true);
    setActiveClinician(null);
    expect(await getStays()).toEqual([]);
  });

  it("has finished stays in the past, then the ward with arrivals unknown", async () => {
    setActiveClinician("rn-manalo");
    const all = await getStays();
    const past = all.filter((s) => s.left_at !== null);
    expect(past.length).toBeGreaterThan(0);
    expect(past.every((s) => Date.parse(s.left_at!) <= Date.now() && s.outcome !== null)).toBe(true);
    const onWard = all.filter((s) => s.left_at === null);
    expect(onWard.length).toBeGreaterThan(0);
    expect(onWard.every((s) => s.admitted_at === null)).toBe(true);
    expect(past.some((s) => s.admitted_at === null)).toBe(true);
    setActiveClinician(null);
  });
});
