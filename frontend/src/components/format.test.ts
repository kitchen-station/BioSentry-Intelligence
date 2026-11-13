import { describe, expect, it } from "vitest";
import { enteredByNurse, formatAge, formatBloodPressure, formatClock, formatOxygen, formatSimClock, formatSpo2Scale, simStatus } from "./format";
import type { SimStatus } from "./format";

describe("formatAge", () => {
  it("uses seconds, then minutes, then hours", () => {
    expect(formatAge(8)).toBe("8s ago");
    expect(formatAge(1208)).toBe("20 min ago");
    expect(formatAge(7300)).toBe("2 h ago");
  });
});

describe("formatBloodPressure", () => {
  it("writes systolic over diastolic", () => {
    expect(formatBloodPressure({ systolic: 88, diastolic: 52 })).toBe("88/52");
    expect(formatBloodPressure({ systolic: 224, diastolic: 118 })).toBe("224/118");
  });

  it("is never the default object stringification", () => {
    expect(formatBloodPressure({ systolic: 120, diastolic: 80 })).not.toContain("[object");
  });
});

describe("formatOxygen", () => {
  it("says room air or on oxygen, never true/false", () => {
    expect(formatOxygen(false)).toBe("Room air");
    expect(formatOxygen(true)).toBe("On oxygen");
  });
});

describe("formatSpo2Scale", () => {
  it("names the scale", () => {
    expect(formatSpo2Scale(1)).toBe("Scale 1");
    expect(formatSpo2Scale(2)).toBe("Scale 2");
  });
});

const SHARED = { clockS: 109_200, speed: 10, ended: false, counts: { received: 120, rejected: 3, abstained: 5 } };

describe("simStatus", () => {
  it("reads what the simulator publishes and ignores the rest of it", () => {
    expect(simStatus({ ...SHARED, staff: [{ id: "rn-reyes" }] })).toEqual(SHARED);
    expect(simStatus({ ...SHARED, speed: 0, ended: true })).toMatchObject({ speed: 0, ended: true });
  });

  it.each([
    ["nothing", undefined],
    ["null", null],
    ["a string", "day 2"],
    ["an empty object", {}],
    ["a clock that is not a number", { ...SHARED, clockS: "soon" }],
    ["a clock that is negative", { ...SHARED, clockS: -1 }],
    ["a clock that is infinite", { ...SHARED, clockS: Infinity }],
    ["a speed it does not offer", { ...SHARED, speed: 5 }],
    ["no ended flag", { ...SHARED, ended: undefined }],
    ["no counts", { ...SHARED, counts: undefined }],
    ["counts that are not an object", { ...SHARED, counts: 3 }],
    ["a count that is missing", { ...SHARED, counts: { received: 1, rejected: 2 } }],
    ["a count that is not a number", { ...SHARED, counts: { ...SHARED.counts, abstained: "5" } }],
  ])("is null for %s", (_, shared) => {
    expect(simStatus(shared)).toBeNull();
  });
});

describe("formatSimClock", () => {
  const at = (clockS: number, over: Partial<SimStatus> = {}) =>
    formatSimClock({ ...(simStatus(SHARED) as SimStatus), clockS, ...over });

  it("says the day from 1, the time and the speed", () => {
    expect(at(0)).toBe("day 1 00:00, 10x");
    expect(at(109_200)).toBe("day 2 06:20, 10x");
    expect(at(86_400 * 3 - 1, { speed: 60 })).toBe("day 3 23:59, 60x");
  });

  it("says paused at speed 0, and ended whatever the speed", () => {
    expect(at(109_200, { speed: 0 })).toBe("day 2 06:20, paused");
    expect(at(109_200, { ended: true })).toBe("day 2 06:20, ended");
  });
});

describe("enteredByNurse", () => {
  const at = new Date(2026, 8, 3, 7, 40).getTime();

  it("gives the time alone for an entry made today", () => {
    expect(enteredByNurse(at, new Date(2026, 8, 3, 19, 0).getTime())).toBe(`Entered by a nurse at ${formatClock(at)}`);
  });

  it("adds the day for an entry from another day, so it does not read as today's", () => {
    const text = enteredByNurse(at, new Date(2026, 8, 5, 7, 40).getTime());
    expect(text.startsWith(`Entered by a nurse at ${formatClock(at)}, `)).toBe(true);
    expect(text).toMatch(/3/);
  });
});
