import { describe, expect, it } from "vitest";
import { enteredByNurse, patchInUse, formatAge, formatBloodPressure, formatClock, formatClockDay, formatOxygen, formatSimClock, formatSpo2Scale, simStatus } from "./format";
import type { SimStatus } from "./format";
import type { Vitals } from "../types";

// "Sep" or "Sept", whichever the runtime's en-GB gives: the clock uses the same call as the simulator.
const SEP = new Date(Date.UTC(2026, 8, 3)).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

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

  it("says the date and time in the simulator's format, and the pace", () => {
    expect(at(0)).toBe(`3 ${SEP}, 15:00 \u00b7 10x`);
    expect(at(109_200)).toBe(`4 ${SEP}, 21:20 \u00b7 10x`);
    expect(at(540 * 60 - 1, { speed: 60 })).toBe(`3 ${SEP}, 23:59 \u00b7 60x`);
    expect(at(540 * 60, { speed: 1 })).toBe(`4 ${SEP}, 00:00 \u00b7 1x`);
  });

  it("says Paused at speed 0, and Run ended whatever the speed", () => {
    expect(at(109_200, { speed: 0 })).toBe(`4 ${SEP}, 21:20 \u00b7 Paused`);
    expect(at(109_200, { ended: true })).toBe(`4 ${SEP}, 21:20 \u00b7 Run ended`);
    expect(at(109_200, { speed: 0, ended: true })).toBe(`4 ${SEP}, 21:20 \u00b7 Run ended`);
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

describe("patchInUse", () => {
  const m = (source: "device" | "nurse", present = true) =>
    ({ data: { present }, provenance: { source } }) as unknown as NonNullable<Vitals["heart_rate"]>;
  const v = (hr: Vitals["heart_rate"], temperature: Vitals["temperature"] = m("nurse")) =>
    ({ heart_rate: hr, respiratory_rate: m("nurse"), spo2: m("nurse"), temperature }) as unknown as Vitals;

  it("is true with one device value among the four", () => {
    expect(patchInUse(v(m("nurse"), m("device")))).toBe(true);
  });

  it("is false when all four are nurse-typed, absent or null", () => {
    expect(patchInUse(v(m("nurse")))).toBe(false);
    expect(patchInUse(v(m("device", false)))).toBe(false);
  });

  it("is false when one value is absent and the others are nurse-typed", () => {
    expect(patchInUse(v(m("nurse", false)))).toBe(false);
  });

  it("is true on a mix of one device value and nurse values", () => {
    expect(patchInUse(v(m("nurse"), m("device")))).toBe(true);
    expect(patchInUse(v(m("device")))).toBe(true);
  });
});

describe("formatClockDay", () => {
  const now = new Date(2026, 10, 14, 12, 0).getTime();
  it("gives the clock for today and the date with the clock for another day", () => {
    const today = new Date(2026, 10, 14, 9, 5).getTime();
    const before = new Date(2026, 10, 13, 22, 15).getTime();
    expect(formatClockDay(today, now)).toBe(formatClock(today));
    expect(formatClockDay(before, now)).toBe(`${new Date(before).toLocaleDateString([], { day: "numeric", month: "short" })}, ${formatClock(before)}`);
  });
});
