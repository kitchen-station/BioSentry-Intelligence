// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryEvent, HistoryRun, PatientHistory } from "../types";
import { formatClock, formatClockDay } from "./format";
import { StayPanel, place } from "./StayPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return { getPatientHistory: vi.fn(), ApiError: MockApiError };
});
vi.mock("../api", () => api);

const LABEL = "Rebuilt from stored 15-minute bins. It can differ from the score shown at the time.";
const signOut = vi.fn();
const settle = () => act(async () => {});

// Two clock days before "today" keep every time on another day, so every line carries its date.
const T = (hhmm: string, d = "2026-01-05") => `${d}T${hhmm}:00Z`;
const clk = (iso: string) => formatClockDay(Date.parse(iso));
const run = (over: Partial<HistoryRun>): HistoryRun => ({
  bins: 2, start: T("08:00"), end: T("09:00"), ongoing: false, peak_score: 9, peak_level: "high", peak_at: T("08:30"),
  reasons: ["Total 9 (≥7): high"], provenance: { computed_at: T("10:00"), rule: "news2_as_of_bins", source: "derived", synthetic: true, tier: "A" },
  state: "alert", ...over,
});
const ev = (over: Partial<HistoryEvent>): HistoryEvent => ({
  at: T("08:30"), by: "rn-manalo", kind: "acknowledged", note_id: null, role: null, score: 9, source: "clinician_action", ...over,
});
const hist = (over: Partial<PatientHistory> = {}): PatientHistory => ({
  bins: 8, events: [], events_cut: false, grid_from: T("08:00"), grid_to: T("12:00"), label: LABEL, patient_id: "p-001",
  runs: [], runs_cut: false, synthetic: true, unavailable: null, ...over,
});
const odd = (over: Record<string, unknown>) => ({ ...hist(), ...over }) as unknown as PatientHistory;

const open = async () => {
  render(<StayPanel patientId="p-001" onSignOut={signOut} />);
  const d = document.querySelector("details")!;
  d.open = true;
  fireEvent(d, new Event("toggle"));
  await settle();
};
const lines = () => screen.getAllByRole("listitem").map((l) => l.textContent);

beforeEach(() => {
  api.getPatientHistory.mockResolvedValue(hist());
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("when it reads", () => {
  it("is closed with no call, reads once on first open, not on close and reopen, once more on Refresh, never on a timer", async () => {
    vi.useFakeTimers();
    render(<StayPanel patientId="p-001" onSignOut={signOut} />);
    const d = document.querySelector("details")!;
    expect(d.open).toBe(false);
    expect(screen.getByText("Stay").tagName).toBe("SUMMARY");
    expect(api.getPatientHistory).not.toHaveBeenCalled();
    for (const o of [true, false, true]) {
      d.open = o;
      fireEvent(d, new Event("toggle"));
      await settle();
    }
    expect(api.getPatientHistory).toHaveBeenCalledTimes(1);
    expect(api.getPatientHistory).toHaveBeenCalledWith("p-001");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await settle();
    expect(api.getPatientHistory).toHaveBeenCalledTimes(2);
    const at = formatClock(Date.now());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });
    expect(api.getPatientHistory).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).toContain(`Read at ${at}.`);
  });

  it("says it is loading, and a second Refresh while it loads asks nothing", async () => {
    let done: (h: PatientHistory) => void = () => {};
    api.getPatientHistory.mockReturnValue(new Promise((r) => (done = r)));
    await open();
    expect(screen.getByRole("status").textContent).toBe("Loading the stay…");
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => done(hist()));
    expect(screen.queryByText("Loading the stay…")).toBeNull();
    expect(api.getPatientHistory).toHaveBeenCalledTimes(1);
  });
});

describe("the list", () => {
  it("words every run state and every event kind, newest first, events nested in the run that holds them", async () => {
    api.getPatientHistory.mockResolvedValue(
      hist({
        runs: [
          run({ start: T("08:00"), end: T("09:00"), reasons: ["Total 9 (≥7): high", "Respiration 3"] }),
          run({ state: "alert", start: T("09:15"), end: T("09:30"), peak_level: "medium", peak_score: 6, reasons: ["Total 6"] }),
          run({ state: "alert", start: T("09:45"), end: T("10:00"), peak_level: "single_3", peak_score: 4, reasons: [] }),
          run({ state: "not_ranked", start: T("10:15"), end: T("10:30"), peak_score: null, peak_level: null, peak_at: null, reasons: ["Blood pressure missing"] }),
          run({ state: "not_rebuilt", start: T("10:45"), end: T("11:00"), peak_score: null, peak_level: null, peak_at: null, reasons: ["A value the bin needs is not stored"] }),
          run({ state: "no_reading", start: T("11:15"), end: T("11:30"), peak_score: null, peak_level: null, peak_at: null, reasons: ["No usable heart rate reading"] }),
          run({ state: "not_ranked", start: T("11:45"), end: T("12:00"), ongoing: true, peak_score: null, peak_level: null, peak_at: null, reasons: [] }),
        ],
        events: [
          ev({ kind: "admitted", at: T("07:00"), by: null, score: null }),
          ev({ kind: "acknowledged", at: T("08:30"), by: "rn-manalo", score: 9 }),
          ev({ kind: "acknowledged", at: T("08:40"), by: "md-santos", score: null }),
          ev({ kind: "vitals_recorded", at: T("08:50"), by: "rn-reyes" }),
          ev({ kind: "note_added", at: T("12:30"), by: "md-santos", role: "md", note_id: "n1" }),
          ev({ kind: "note_added", at: T("12:31"), role: "rn" }),
          ev({ kind: "note_added", at: T("12:32"), role: "charge_rn" }),
          ev({ kind: "note_added", at: T("12:33"), role: null }),
          ev({ kind: "bp_charted", at: T("12:34") }),
          ev({ kind: "vitals_recorded", at: T("12:35"), by: null }),
          ev({ kind: "acknowledged", at: T("12:36"), by: null, score: null }),
        ],
      }),
    );
    await open();
    const c = clk;
    expect(lines()).toEqual([
      `Acknowledged at ${c(T("12:36"))}`,
      `Vitals recorded at ${c(T("12:35"))}`,
      `Blood pressure charted at ${c(T("12:34"))}`,
      `Note added at ${c(T("12:33"))}`,
      `Note added at ${c(T("12:32"))} (Charge RN)`,
      `Note added at ${c(T("12:31"))} (RN)`,
      `Note added at ${c(T("12:30"))} (MD)`,
      `${c(T("11:45"))} to now · Not ranked`,
      `${c(T("11:15"))} to ${c(T("11:30"))} · No heart rate reading`,
      `${c(T("10:45"))} to ${c(T("11:00"))} · Not rebuilt · the stored values do not give the score of that time`,
      `${c(T("10:15"))} to ${c(T("10:30"))} · Not ranked · Blood pressure missing`,
      `${c(T("09:45"))} to ${c(T("10:00"))} · Alert · NEWS2 up to 4 (one value at 3)`,
      `${c(T("09:15"))} to ${c(T("09:30"))} · Alert · NEWS2 up to 6 (medium) · Total 6`,
      `${c(T("08:00"))} to ${c(T("09:00"))} · Alert · NEWS2 up to 9 (high) · Total 9 (≥7): high · Respiration 3` +
        `Vitals recorded by rn-reyes at ${c(T("08:50"))}` +
        `Acknowledged by md-santos at ${c(T("08:40"))}` +
        `Acknowledged by rn-manalo at ${c(T("08:30"))} (NEWS2 9)`,
      `Vitals recorded by rn-reyes at ${c(T("08:50"))}`,
      `Acknowledged by md-santos at ${c(T("08:40"))}`,
      `Acknowledged by rn-manalo at ${c(T("08:30"))} (NEWS2 9)`,
      `Admitted ${c(T("07:00"))}`,
    ]);
    const nested = within(screen.getAllByRole("list")[0]).getAllByRole("list");
    expect(nested).toHaveLength(1);
    expect(within(nested[0]).getAllByRole("listitem")).toHaveLength(3);
  });

  it("an ongoing alert ends at now, and a time today carries no date", async () => {
    const now = Date.now();
    const at = new Date(now - 60_000).toISOString();
    api.getPatientHistory.mockResolvedValue(hist({ grid_from: at, grid_to: new Date(now).toISOString(), runs: [run({ start: at, end: new Date(now).toISOString(), ongoing: true })] }));
    await open();
    expect(lines()[0]).toBe(`${formatClock(Date.parse(at))} to now · Alert · NEWS2 up to 9 (high) · Total 9 (≥7): high`);
  });

  it("says no alert when there is no run, and still lists the events", async () => {
    api.getPatientHistory.mockResolvedValue(hist({ events: [ev({ kind: "bp_charted", at: T("09:00") })] }));
    await open();
    expect(screen.getByText("No alert in this stay.")).toBeTruthy();
    expect(lines()).toEqual([`Blood pressure charted at ${clk(T("09:00"))}`]);
  });

  it.each([
    ["no_bins", "No 15-minute set is stored for this stay yet."],
    ["past_cap", "This stay is longer than 7 days. Its history cannot be shown yet."],
    ["not_aligned", "The stored sets of this stay do not line up. Its history cannot be shown."],
  ] as const)("says %s in one line and nothing else", async (unavailable, text) => {
    api.getPatientHistory.mockResolvedValue(hist({ unavailable, grid_from: null, grid_to: null }));
    await open();
    expect(screen.getByText(text)).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText("No alert in this stay.")).toBeNull();
    expect(document.querySelector("svg")).toBeNull();
  });

  it("an unknown unavailable word is shown as not shown", async () => {
    api.getPatientHistory.mockResolvedValue(odd({ unavailable: "later" }));
    await open();
    expect(screen.getByText("The stay could not be shown.")).toBeTruthy();
  });

  it("says older entries are not shown, and ends with the label and the synthetic line", async () => {
    api.getPatientHistory.mockResolvedValue(hist({ runs_cut: true }));
    await open();
    expect(screen.getByText("Older entries are not shown.")).toBeTruthy();
    expect(screen.getByText(`${LABEL} Synthetic, not validated.`)).toBeTruthy();
    cleanup();
    api.getPatientHistory.mockResolvedValue(hist({ events_cut: true }));
    await open();
    expect(screen.getByText("Older entries are not shown.")).toBeTruthy();
  });
});

describe("the strip", () => {
  it("draws one bar per run in time order with its marker, one tick per event, hidden from readers", async () => {
    const runs = [
      run({ start: T("08:00"), end: T("09:00") }),
      run({ start: T("09:00"), end: T("10:00"), peak_level: "medium", peak_score: 6 }),
      run({ start: T("10:00"), end: T("11:00"), peak_level: "single_3", peak_score: 4 }),
      run({ state: "not_ranked", start: T("11:00"), end: T("11:30"), peak_score: null, peak_level: null, peak_at: null }),
      run({ state: "not_rebuilt", start: T("11:30"), end: T("11:45"), peak_score: null, peak_level: null, peak_at: null }),
      run({ state: "no_reading", start: T("11:45"), end: T("11:45"), peak_score: null, peak_level: null, peak_at: null }),
    ];
    api.getPatientHistory.mockResolvedValue(hist({ runs, events: [ev({ at: T("08:30") }), ev({ at: T("13:00"), kind: "bp_charted" })] }));
    await open();
    const svg = document.querySelector("svg")!;
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    const bars = [...svg.querySelectorAll("rect[data-kind]")];
    expect(bars.map((b) => b.getAttribute("data-kind"))).toEqual(["H", "M", "3", "NR", "?", "dashed"]);
    const from = Date.parse(T("08:00"));
    const to = Date.parse(T("13:00")); // the newest event is later than grid_to
    runs.forEach((r, i) => {
      const { x, w } = place(Date.parse(r.start), Date.parse(r.end), from, to);
      expect(bars[i].getAttribute("x")).toBe(`${x}%`);
      expect(bars[i].getAttribute("width")).toBe(`${w}%`);
    });
    const xs = bars.map((b) => parseFloat(b.getAttribute("x")!));
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(parseFloat(bars[5].getAttribute("width")!)).toBeGreaterThan(0); // start equals end
    const ticks = [...svg.querySelectorAll("rect[data-tick]")];
    expect(ticks.map((t) => t.getAttribute("data-tick"))).toEqual(["acknowledged", "bp_charted"]);
    expect(ticks[0].getAttribute("x")).toBe(`${place(Date.parse(T("08:30")), Date.parse(T("08:30")), from, to).x}%`);
    expect(bars[3].getAttribute("style")).toMatch(/url\(/);
    expect(bars[4].getAttribute("style")).toMatch(/url\(/);
    expect(bars[0].getAttribute("style")).toBeNull();
    // Wide bars carry their letter; the narrow ones do not.
    expect([...svg.querySelectorAll("text")].map((t) => t.textContent)).toEqual(["H", "M", "3", "NR"]);
  });

  it("scales a run to percent of the span and keeps a point run visible", () => {
    expect(place(0, 50, 0, 100)).toEqual({ x: 0, w: 50 });
    expect(place(100, 100, 0, 100).x).toBeCloseTo(99.2);
    expect(place(5, 5, 5, 5).w).toBeGreaterThan(0);
    expect(place(-10, 500, 0, 100)).toEqual({ x: 0, w: 100 });
  });

  it("shows both ends as text, with the date when the span crosses midnight, and now for an ongoing run", async () => {
    api.getPatientHistory.mockResolvedValue(hist({ grid_from: T("22:00"), grid_to: T("01:00", "2026-01-06"), runs: [] }));
    await open();
    expect(screen.getByText(clk(T("22:00")))).toBeTruthy();
    expect(screen.getByText(clk(T("01:00", "2026-01-06")))).toBeTruthy();
    expect(clk(T("22:00"))).toMatch(/,/);
    cleanup();
    api.getPatientHistory.mockResolvedValue(hist({ runs: [run({ ongoing: true, end: T("12:00") })] }));
    await open();
    expect(screen.getByText("now")).toBeTruthy();
  });

  it("dates both ends when the span crosses midnight today", async () => {
    const a = new Date(2026, 0, 5, 23, 0).toISOString();
    api.getPatientHistory.mockResolvedValue(hist({ grid_from: a, grid_to: new Date(2026, 0, 6, 1, 0).toISOString() }));
    await open();
    expect(screen.getAllByText(/Jan/).length).toBe(2);
  });
});

describe("errors", () => {
  const failWith = async (status: number) => {
    api.getPatientHistory.mockRejectedValue(new api.ApiError(status));
    await open();
  };
  it("404: the patient is gone", async () => {
    await failWith(404);
    expect(screen.getByText("This patient is no longer on your ranked list.")).toBeTruthy();
  });
  it("401: the session ends", async () => {
    await failWith(401);
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Your session has ended. Sign in again.")).toBeTruthy();
  });
  it("a network failure: cannot reach the server", async () => {
    api.getPatientHistory.mockRejectedValue(new TypeError("x"));
    await open();
    expect(screen.getByText("Cannot reach the ward server.")).toBeTruthy();
    expect(signOut).not.toHaveBeenCalled();
  });
  it("a failed Refresh keeps the last history under the error line", async () => {
    api.getPatientHistory.mockResolvedValue(hist({ events: [ev({ kind: "admitted", at: T("07:00") })] }));
    await open();
    api.getPatientHistory.mockRejectedValue(new TypeError("x"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await settle();
    expect(screen.getByText("Cannot reach the ward server.")).toBeTruthy();
    expect(lines()).toEqual([`Admitted ${clk(T("07:00"))}`]);
  });
});

describe("an answer of an odd shape", () => {
  it("skips the bad items and keeps the good ones", async () => {
    api.getPatientHistory.mockResolvedValue(
      odd({
        runs: [
          run({ start: "x" }),
          run({ state: "paused" as never }),
          run({ start: T("09:00"), end: T("08:00") }),
          run({ peak_level: null }),
          run({ peak_level: "huge" as never }),
          null,
          run({ state: "not_ranked", reasons: 5 as never, peak_score: null, peak_level: null }),
          run({ start: T("10:00"), end: T("10:15"), reasons: ["ok", 7 as never] }),
        ],
        events: [ev({ kind: "shouted" as never }), ev({ at: "x" }), ev({ kind: "bp_charted", at: T("11:00"), role: "boss" as never, by: 3 as never, score: "9" as never })],
      }),
    );
    await open();
    expect(lines()).toEqual([
      `Blood pressure charted at ${clk(T("11:00"))}`,
      `${clk(T("10:00"))} to ${clk(T("10:15"))} · Alert · NEWS2 up to 9 (high) · ok`,
      `${clk(T("08:00"))} to ${clk(T("09:00"))} · Not ranked`,
    ]);
  });

  it("runs: null with good events shows the events", async () => {
    api.getPatientHistory.mockResolvedValue(odd({ runs: null, grid_from: "x", grid_to: null, events: [ev({ kind: "admitted", at: T("07:00") })] }));
    await open();
    expect(lines()).toEqual([`Admitted ${clk(T("07:00"))}`]);
  });

  it.each([[{ runs: null, events: null }], [{ runs: [{}], events: [{}] }]])("nothing usable says so", async (over) => {
    api.getPatientHistory.mockResolvedValue(odd(over));
    await open();
    expect(screen.getByText("The stay could not be shown.")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.getByText("Synthetic, not validated.", { exact: false })).toBeTruthy();
    cleanup();
  });

  it("an answer that is not an object does not throw", async () => {
    api.getPatientHistory.mockResolvedValue(null);
    await open();
    expect(screen.getByText("The stay could not be shown.")).toBeTruthy();
  });
});

describe("text from the server", () => {
  it("stays text", async () => {
    const bad = "<img src=x onerror=alert(1)>";
    api.getPatientHistory.mockResolvedValue(hist({ runs: [run({ reasons: [bad] })], events: [ev({ by: bad, at: T("08:10") })] }));
    await open();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getAllByRole("listitem")[0].textContent).toContain(bad);
    expect(screen.getByText(`Acknowledged by ${bad} at ${clk(T("08:10"))} (NEWS2 9)`)).toBeTruthy();
  });
});
