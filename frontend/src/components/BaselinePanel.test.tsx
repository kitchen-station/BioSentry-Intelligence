// @vitest-environment jsdom
/**
 * The own-baseline panel: each state in plain words, a stale reading that cannot pass as
 * current, the fixed research label before any flag, the stored value on hover, and a response of
 * the wrong shape that becomes a plain line rather than a blank page.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BaselineComputed, BaselineResponse } from "../types";
import { BaselinePanel } from "./BaselinePanel";
import type { BaselineState } from "./useBaseline";
import { formatClock } from "./format";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LABEL = "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger.";
const T0 = Date.parse("2026-11-12T10:00:00Z");
const at = (minutesAgo: number) => new Date(T0 - minutesAgo * 60_000).toISOString();
const day = (iso: string) => new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
const when = (iso: string) => `${day(iso)} ${formatClock(Date.parse(iso))}`;

const computed = (over: Partial<BaselineComputed> = {}): BaselineComputed => ({
  vital: "heart_rate",
  status: "computed",
  baseline_median: 70,
  latest: 90,
  latest_at: at(30),
  latest_age_s: 60,
  baseline_from: at(600),
  anchored_at: "admission",
  delta: 20,
  z: 4.1,
  run_bins: 4,
  since: at(60),
  significant: true,
  direction: "rising",
  pathway: "circulatory",
  unit: "bpm",
  ...over,
});
const quiet = (over: Partial<BaselineComputed> = {}) =>
  computed({ latest: 72.3, delta: 2.3, z: 0.4, run_bins: 0, since: null, significant: false, direction: "rising", ...over });

const response = (vitals: unknown[], over: Record<string, unknown> = {}) =>
  ({
    patient_id: "p-001",
    synthetic: true,
    admission_known: true,
    label: LABEL,
    params: { window_bins: 24, k: 2, p: 3 },
    vitals,
    ...over,
  }) as unknown as BaselineResponse;

const show = (baseline: BaselineState | unknown) => render(<BaselinePanel baseline={baseline as BaselineState} />);
const items = () => screen.getAllByRole("listitem");
const text = (i = 0) => items()[i].textContent!;

afterEach(cleanup);

describe("BaselinePanel", () => {
  it("is a labelled region with a heading, the research label first, then a list with list semantics", () => {
    show(response([computed(), { vital: "respiratory_rate", status: "forming", bins_valid: 10, bins_needed: 24 }]));
    const region = screen.getByRole("region", { name: "Change from own baseline" });
    expect(within(region).getByRole("heading", { name: "Change from own baseline" })).toBeTruthy();
    expect(items()).toHaveLength(4); // the two vitals that came back, and a line for each of the other two
    expect(screen.getByRole("list").getAttribute("role")).toBe("list");
    const label = screen.getByText(LABEL);
    const list = screen.getByRole("list");
    expect(label.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("prints the fixed label whatever the server sends", () => {
    show(response([computed()], { label: "" }));
    expect(screen.getByText(LABEL)).toBeTruthy();
    cleanup();
    show(response([computed()], { label: "Something else" }));
    expect(screen.getByText(LABEL)).toBeTruthy();
    expect(screen.queryByText("Something else")).toBeNull();
  });

  it("says a flagged change in words, with units, the run in bins and hours, and when it began", () => {
    show(response([computed()]));
    expect(text()).toContain(
      `Heart rate 90 bpm · +20 bpm from own baseline 70 bpm · Flagged rising for 4 bins (1 h), since ${when(at(60))}`,
    );
    expect(items()[0].getAttribute("data-significant")).toBe("true");
    expect(text()).toContain("z 4.1 · circulatory");
  });

  it.each([
    [1, "1 bin (15 min)"],
    [3, "3 bins (45 min)"],
    [6, "6 bins (1 h 30 min)"],
  ])("words a run of %i bins as %s", (run_bins, expected) => {
    show(response([computed({ run_bins, since: null, direction: null })]));
    expect(text()).toContain(`Flagged for ${expected}`);
    expect(text()).not.toContain(", since ");
  });

  it("says not flagged, never 'range', for a small move", () => {
    show(response([quiet({ pathway: null })]));
    expect(text()).toContain("Heart rate 72.3 bpm · +2.3 bpm from own baseline 70 bpm · not flagged");
    expect(text()).not.toContain("(");
    expect(text()).not.toContain("range");
    expect(text()).not.toContain("circulatory");
    expect(items()[0].getAttribute("data-significant")).toBeNull();
  });

  it("says a big move the other way is not flagged, and what would be (heart rate 35 against 75)", () => {
    show(response([quiet({ latest: 35, delta: -40, z: -6, baseline_median: 75, direction: "falling" })]));
    expect(text()).toContain(
      "Heart rate 35 bpm · -40 bpm from own baseline 75 bpm · not flagged (moved the other way; only a sustained rise is flagged)",
    );
    expect(text()).not.toContain("range");
  });

  it("names a fall as what is flagged when the latest moved up and the bad direction is down (SpO2)", () => {
    show(response([quiet({ vital: "spo2", unit: "%", latest: 99, delta: 4, z: 3, baseline_median: 95, direction: "rising" })]));
    expect(text()).toContain("not flagged (moved the other way; only a sustained fall is flagged)");
  });

  it("says a short run is not yet flagged and how far it has got, using the served k and p", () => {
    show(response([quiet({ run_bins: 2, z: 3.4, delta: 12 })], { params: { window_bins: 24, k: 2, p: 5 } }));
    expect(text()).toContain("not flagged (only 2 of 5 bins so far)");
  });

  it("does not call a move other-way when it is below k, using the served k", () => {
    show(response([quiet({ z: -2.5, delta: -5 })], { params: { window_bins: 24, k: 3, p: 3 } }));
    expect(text()).toContain("not flagged");
    expect(text()).not.toContain("other way");
  });

  it("shows no plus sign on a zero or negative delta", () => {
    show(response([quiet({ delta: 0, z: 0 }), quiet({ delta: -2.1, z: -0.5 })]));
    expect(text(0)).toContain(" · 0 bpm from own baseline");
    expect(text(1)).toContain(" · -2.1 bpm from");
  });

  it("says a baseline is still forming, with how many bins it has", () => {
    show(response([{ vital: "respiratory_rate", status: "forming", bins_valid: 10, bins_needed: 24 }]));
    expect(text()).toBe("Respiratory rate · baseline forming, 10 of 24 bins");
  });

  it("says why a vital has nothing, and does not guess when the reason is new", () => {
    show(response([
      { vital: "temperature", status: "unavailable", reason: "no_history" },
      { vital: "spo2", status: "unavailable", reason: "latest_bin_missing" },
      { vital: "heart_rate", status: "unavailable", reason: "patch_not_live" },
      { vital: "spo2", status: "unavailable", reason: "sensor_off" },
    ]));
    expect(items().slice(0, 4).map((li) => li.textContent)).toEqual([
      "Temperature · no history yet",
      "SpO2 · latest reading missing",
      "Heart rate · not counted: no live patch reading",
      "Unrecognised finding",
    ]);
  });

  it.each([
    ["fresh", 0, false],
    ["a reading in its own bin", 300, false],
    ["one bin late (exactly the threshold)", 1200, false],
    ["past one missed bin", 1201, true],
    ["very old", 7 * 24 * 3600, true],
  ])("age from the server: %s (%i s) is stale: %s", (_label, age, stale) => {
    show(response([computed({ latest_age_s: age })]));
    expect(text().includes("Stale · ")).toBe(stale);
    expect(items()[0].querySelector("[data-stale]") !== null).toBe(stale);
    expect(text()).toContain(`15-min median ${when(at(30))} to ${formatClock(T0 - 15 * 60_000)}`);
  });

  it("words the age with the existing formatter", () => {
    show(response([computed({ latest_age_s: 540 })]));
    expect(text()).toContain(`15-min median ${when(at(30))} to ${formatClock(T0 - 15 * 60_000)}, ended 9 min ago`);
  });

  it("shows the date with every time, so a time is never read as today", () => {
    show(response([computed({ since: "2026-11-09T18:30:00Z", baseline_from: "2026-11-08T08:00:00Z" })]));
    expect(text()).toContain(`since ${when("2026-11-09T18:30:00Z")}`);
    expect(screen.getByText(/^Baseline: first/).textContent).toContain(`from ${when("2026-11-08T08:00:00Z")}`);
  });

  it("rounds only what it shows: latest to one decimal, the served value on hover", () => {
    show(response([computed({ latest: 140.32326888221948, significant: false, run_bins: 0, z: 0.1 })]));
    const value = screen.getByTitle("140.32326888221948");
    expect(value.textContent).toBe("140.3 bpm");
    expect(text()).not.toContain("140.32326");
  });

  it("shows temperature to one decimal and exact stored 72.123456789 as the title", () => {
    show(response([computed({ vital: "temperature", unit: "°C", latest: 37, baseline_median: 36.8, delta: 0.2, significant: false, run_bins: 0, z: 0.1 })]));
    expect(text()).toContain("Temperature 37.0 °C");
    cleanup();
    show(response([computed({ latest: 72.123456789 })]));
    expect(screen.getByTitle("72.123456789").textContent).toBe("72.1 bpm");
  });

  const anchors = (vitals: BaselineComputed[], admission_known: boolean) => {
    show(response(vitals, { admission_known }));
    return screen.queryAllByText(/^Baseline: first/).map((e) => e.textContent);
  };

  it("anchors on admission only when the server says so, and says the other two cases apart", () => {
    const from = when(at(600));
    expect(anchors([computed()], true)).toEqual([`Baseline: first 24 valid bins, since admission, from ${from}`]);
    cleanup();
    expect(anchors([computed({ anchored_at: "first_kept_bin" })], true)).toEqual([
      `Baseline: first 24 valid bins, from the earliest readings kept (not from admission), from ${from}`,
    ]);
    cleanup();
    expect(anchors([computed({ anchored_at: "first_kept_bin" })], false)).toEqual([
      `Baseline: first 24 valid bins, from the first stored reading (admission time not known), from ${from}`,
    ]);
    cleanup();
    expect(anchors([computed({ anchored_at: "elsewhere" as never })], true)[0]).not.toContain("since admission");
  });

  it("shows a shared anchor once under the list, and a differing one per vital", () => {
    const both = [computed(), computed({ vital: "spo2", unit: "%" })];
    expect(anchors(both, true)).toHaveLength(1);
    expect(screen.getByRole("list").contains(screen.getByText(/^Baseline: first/))).toBe(false);
    cleanup();
    const differ = [computed(), computed({ vital: "spo2", unit: "%", anchored_at: "first_kept_bin" })];
    expect(anchors(differ, true)).toHaveLength(2);
    expect(text(0)).toContain("since admission");
    expect(text(1)).toContain("not from admission");
    cleanup();
    expect(anchors([computed(), computed({ vital: "spo2", unit: "%", baseline_from: at(500) })], true)).toHaveLength(2);
  });

  it("says fixture mode has no finding, and the frontend computes none", () => {
    show(null);
    expect(screen.getByText("Own-baseline findings need the live API.")).toBeTruthy();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("shows one quiet line when the finding could not be loaded", () => {
    show("failed");
    expect(screen.getByText("Baseline could not be loaded.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says it is loading", () => {
    show(undefined);
    expect(screen.getByText("Loading baseline…")).toBeTruthy();
  });
});

describe("BaselinePanel with a response of the wrong shape", () => {
  it.each([
    ["no vitals", { vitals: undefined }],
    ["vitals that is not a list", { vitals: "x" }],
    ["no params", { params: undefined }],
    ["params of the wrong type", { params: { window_bins: "24", k: 2, p: 3 } }],
    ["a body that is not an object", "oops"],
  ])("%s: the error line, no throw", (_name, over) => {
    show(typeof over === "string" ? over : response([computed()], over));
    expect(screen.getByText("Baseline could not be loaded.")).toBeTruthy();
  });

  it.each([
    ["an unknown vital", { vital: "glucose", status: "forming", bins_valid: 1, bins_needed: 24 }],
    ["an inherited name as the vital", { vital: "constructor", status: "forming", bins_valid: 1, bins_needed: 24 }],
    ["an unknown status", { vital: "spo2", status: "learning" }],
    ["a forming entry without counts", { vital: "spo2", status: "forming" }],
    ["an unavailable entry without a reason", { vital: "spo2", status: "unavailable" }],
    ["a computed entry with a number missing", { ...computed(), delta: undefined }],
    ["a computed entry with an unparseable latest time", { ...computed(), latest_at: "soon" }],
    ["a computed entry with an unparseable baseline time", { ...computed(), baseline_from: 5 }],
    ["a computed entry with an unparseable since", { ...computed(), since: "later" }],
    ["a computed entry without a flag", { ...computed(), significant: "yes" }],
    ["a computed entry without a unit", { ...computed(), unit: undefined }],
    ["an entry that is not an object", null],
    ["an entry that is a string", "heart_rate"],
  ])("%s: one plain line for it, the others still shown", (_name, entry) => {
    show(response([entry, { vital: "temperature", status: "unavailable", reason: "no_history" }]));
    expect(text(0)).toBe("Unrecognised finding");
    expect(text(1)).toBe("Temperature · no history yet");
  });
});

describe("BaselinePanel: the flagged direction comes from the vital", () => {
  const FLAGS = { heart_rate: "rise", respiratory_rate: "rise", temperature: "rise", spo2: "fall" } as const;
  const UNITS = { heart_rate: "bpm", respiratory_rate: "/min", temperature: "°C", spo2: "%" } as const;

  it.each(
    (Object.keys(FLAGS) as (keyof typeof FLAGS)[]).flatMap((vital) => [
      [vital, "up", 3],
      [vital, "down", -3],
    ] as const),
  )("%s moved %s with z %i and no run", (vital, way, z) => {
    show(response([quiet({ vital, unit: UNITS[vital], z, delta: z, run_bins: 0 })]));
    const flaggedWay = (FLAGS[vital] === "rise") === (way === "up");
    expect(text()).toContain(
      flaggedWay
        ? "not flagged (only 0 of 3 bins so far)"
        : `not flagged (moved the other way; only a sustained ${FLAGS[vital]} is flagged)`,
    );
  });

  it("a heart rate that rose, with z served at exactly k and a run of 0, is not called the other way", () => {
    show(response([quiet({ z: 2, delta: 8.3, latest: 78.3 })]));
    expect(text()).toContain("not flagged (only 0 of 3 bins so far)");
    expect(text()).not.toContain("other way");
  });

  it("an SpO2 that fell with a run of 0 says only 0 of 3, and a rise says the other way (fall flagged)", () => {
    show(response([quiet({ vital: "spo2", unit: "%", latest: 92, baseline_median: 96, delta: -4, z: -3, run_bins: 0 })]));
    expect(text()).toContain("not flagged (only 0 of 3 bins so far)");
    cleanup();
    show(response([quiet({ vital: "spo2", unit: "%", latest: 99, baseline_median: 96, delta: 3, z: 2.5 })]));
    expect(text()).toContain("SpO2 99 % · +3 % from own baseline 96 % · not flagged (moved the other way; only a sustained fall is flagged)");
  });

  it("says nothing more for a move of zero", () => {
    show(response([quiet({ delta: 0, z: 3 })]));
    expect(text()).not.toContain("(");
  });
});

describe("BaselinePanel: what is missing and what is malformed", () => {
  it("an empty list is the error line, not a heading over nothing", () => {
    show(response([]));
    expect(screen.getByText("Baseline could not be loaded.")).toBeTruthy();
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("says a vital the server left out returned no finding", () => {
    show(response([computed()]));
    expect(items().slice(1).map((li) => li.textContent)).toEqual([
      "Respiratory rate · no finding returned",
      "SpO2 · no finding returned",
      "Temperature · no finding returned",
    ]);
  });

  it("treats a wrong-typed direction or pathway as absent, never '[object Object]' or 'undefined'", () => {
    show(response([{ ...computed(), direction: { a: 1 }, pathway: undefined }]));
    expect(text()).toContain("Flagged for 4 bins");
    expect(text()).not.toContain("object");
    expect(text()).not.toContain("undefined");
    expect(text()).toContain("z 4.1");
  });

  it("calls a negative age or run unrecognised", () => {
    show(response([computed({ latest_age_s: -50 }), computed({ run_bins: -1 })]));
    expect(text(0)).toBe("Unrecognised finding");
    expect(text(1)).toBe("Unrecognised finding");
  });
});

describe("BaselinePanel: display rounding of latest", () => {
  it.each([
    ["heart_rate", 72.95, "73"],
    ["heart_rate", 72.85, "72.9"],
    ["heart_rate", 72.05, "72.1"],
    ["heart_rate", 72, "72"],
    ["temperature", 36.25, "36.3"],
    ["temperature", 36.05, "36.1"],
    ["temperature", 37, "37.0"],
    ["heart_rate", 0.00000001, "0"],
    ["heart_rate", -72.85, "-72.9"],
  ])("%s %s shows as %s, the served value in the title", (vital, latest, expected) => {
    show(response([computed({ vital: vital as never, latest, unit: "u", significant: false, run_bins: 0, z: 0 })]));
    const value = screen.getByTitle(String(latest));
    expect(value.textContent).toBe(`${expected} u`);
  });
});
