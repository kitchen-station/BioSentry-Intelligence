// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchRow, Session, WardResearch as Answer } from "../types";
import { WardResearch } from "./WardResearch";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return { getWardResearch: vi.fn(), hasLabQuestions: true, ApiError: MockApiError };
});
vi.mock("../api", () => api);

const signOut = vi.fn();
const session: Session = { clinician_id: "md-santos", ward: "W4", role: "md" };
const LABEL = "Each patient against their own baseline. Research view: synthetic, not validated. NEWS2 alone orders the list.";

type Vital = ResearchRow["baseline"][number];
const computed = (vital: string, latest: number, median: number, delta: number, significant: boolean, unit: string): Vital => ({
  vital, status: "computed", baseline_median: median, latest, latest_at: "2026-11-14T09:00:00Z", latest_age_s: 60,
  baseline_from: "2026-11-14T01:00:00Z", anchored_at: "admission", delta, z: 3, band_low: 60, band_high: 80, run_bins: 2,
  since: null, significant, direction: "rising", pathway: null, unit,
} as Vital);
const models = (prediction: unknown = { state: "no_flag", reason: null, bin_start: null, bin_end: null, provenance: [] }) => ({
  prediction,
  alert_policy: { state: "not_computed", reason: "baseline forming", bin_start: null, bin_end: null, provenance: [] },
});
const row = (over: Partial<ResearchRow>): ResearchRow => ({
  bed: "4A", assigned: true, id: "p-001", name: "Santos, Maria", rank: 1, score: 7, level: "high",
  baseline: [
    computed("heart_rate", 92, 70, 22, true, "bpm"),
    { vital: "respiratory_rate", status: "forming", bins_valid: 10, bins_needed: 24 },
    { vital: "spo2", status: "unavailable", reason: "no_history" },
  ],
  models: models({ state: "flag", reason: null, bin_start: null, bin_end: null, provenance: [] }),
  alert: { status: "counted", bins: 13, minutes: 195, ongoing: true },
  ...over,
} as ResearchRow);
const ANSWER: Answer = {
  ward: "W4", now: "2026-11-14T09:30:00Z", synthetic: true, label: LABEL,
  ranked: [
    row({}),
    row({
      bed: "12", assigned: false, id: null, name: null, rank: 2, score: 5, level: "medium",
      baseline: [
        { vital: "heart_rate", status: "flag_only", significant: true, direction: "rising" },
        { vital: "respiratory_rate", status: "flag_only", significant: false, direction: null },
      ] as ResearchRow["baseline"], models: models() as ResearchRow["models"],
      alert: { status: "counted", bins: 3, minutes: 45, ongoing: false },
    }),
    row({ bed: "5C", id: "p-003", name: "Villanueva, Jose", rank: 3, score: 2, level: "low", alert: { status: "counted", bins: 0, minutes: 0, ongoing: false } }),
    row({
      bed: "6D", id: "p-004", name: "Cruz, Ana", rank: 4, score: 3, level: "low_medium",
      models: { prediction: { state: "not_sure", reason: null, bin_start: null, bin_end: null, provenance: [] }, alert_policy: { state: "weird", reason: null, bin_start: null, bin_end: null, provenance: [] } } as unknown as ResearchRow["models"],
      alert: { status: "unavailable", reason: "no_bins" },
    }),
    row({ bed: "7E", id: "p-005", name: "Lim, Francis", rank: 5, score: 0, level: "low", models: models({ state: "not_computed", reason: "something new", bin_start: null, bin_end: null, provenance: [] }) as ResearchRow["models"], alert: { status: "counted", bins: 4, minutes: 60, ongoing: false } }),
  ],
  abstained: [
    { bed: "9", assigned: false, id: null, name: null, reason: "Heart rate missing", instruction: "Measure it" },
    { bed: "8F", assigned: true, id: "p-009", name: "Tan, Lucinda", reason: "Blood pressure missing", instruction: "Record it" },
  ],
};

const page = (who: Session = session) =>
  render(
    <MemoryRouter initialEntries={["/ward/research"]}>
      <Routes>
        <Route path="/ward/research" element={<WardResearch session={who} onSignOut={signOut} />} />
        <Route path="/patient/:id" element={<p>patient page</p>} />
        <Route path="/lab" element={<p>lab page</p>} />
        <Route path="/ask" element={<p>ask page</p>} />
      </Routes>
    </MemoryRouter>,
  );
const loaded = async () => {
  page();
  await screen.findByRole("table");
};

beforeEach(() => {
  api.getWardResearch.mockResolvedValue(ANSWER);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
  api.hasLabQuestions = true;
});

describe("ward research: the table", () => {
  it("has the heading, the label from the answer, the read time and a captioned table in a focusable region", async () => {
    await loaded();
    expect(screen.getByRole("heading", { level: 1, name: "Ward research" })).toBeTruthy();
    expect(screen.getByText(LABEL)).toBeTruthy();
    expect(screen.getByText(/Read at/)).toBeTruthy();
    const region = screen.getByRole("region", { name: "Ward research table" });
    expect(region.getAttribute("tabindex")).toBe("0");
    const table = within(region).getByRole("table");
    expect(within(table).getByText("Every patient of the ward, in the NEWS2 order")).toBeTruthy();
    expect(within(table).getAllByRole("columnheader").map((h) => h.getAttribute("scope"))).toEqual(Array(8).fill("col"));
    expect(within(table).getAllByRole("rowheader").length).toBe(5);
    expect(screen.getByText("Simulated patients until a hospital is connected.")).toBeTruthy();
  });

  it("shows each patient's own row as a link and a patient who is not the reader's by bed alone", async () => {
    await loaded();
    const link = screen.getByRole("link", { name: "Santos, Maria" });
    expect(link.getAttribute("href")).toBe("/patient/p-001");
    const other = screen.getByText("Bed 12 (not your patient)");
    expect(other.closest("a")).toBeNull();
    const rows = screen.getAllByRole("row");
    expect(rows.length).toBe(6);
    expect(within(rows[2]).queryByRole("link")).toBeNull();
    expect(screen.getAllByRole("link").filter((l) => l.getAttribute("href")?.startsWith("/patient/")).length).toBe(5);
    expect(document.body.textContent).not.toMatch(/p-0\d\d/); // no id is shown as text, for anyone
  });

  it("shows the score with the level in words, so colour is not the only sign", async () => {
    await loaded();
    const cells = screen.getAllByRole("row").map((r) => within(r).queryAllByRole("cell")[1]?.textContent);
    expect(cells.slice(1)).toEqual(["7, High", "5, Medium", "2, Low", "3, Low-medium (one parameter at 3)", "0, Low"]);
  });

  it("shows each vital against its own baseline, with the display rounding of the baseline panel", async () => {
    await loaded();
    const first = screen.getAllByRole("row")[1];
    const cells = within(first).getAllByRole("cell").map((c) => c.textContent);
    expect(cells[2]).toBe("92 vs 70 bpm (+22) · 1 min ago · Flagged");
    expect(cells[3]).toBe("forming (10 of 24 15-minute sets)");
    expect(cells[4]).toBe("No history yet"); // the reason, in the words of the patient page
    const second = within(screen.getAllByRole("row")[2]).getAllByRole("cell").map((c) => c.textContent);
    expect(second[2]).toBe("Flagged");
    expect(second[3]).toBe("Not flagged");
    expect(second[4]).toBe("No own baseline for this patient now."); // no SpO2 object at all
  });

  it("shows a falling value with its sign and no Flagged mark when it is not significant", async () => {
    api.getWardResearch.mockResolvedValue({ ...ANSWER, ranked: [row({ baseline: [computed("heart_rate", 60, 70, -10, false, "bpm")] })] });
    await loaded();
    expect(screen.getByText("60 vs 70 bpm (-10) · 1 min ago")).toBeTruthy();
  });

  it("shows no number and no vs line for a patient who is not the reader's, and an age for the reader's own", async () => {
    await loaded();
    const rows = screen.getAllByRole("row");
    expect(within(rows[2]).queryByText(/ vs /)).toBeNull();
    expect(within(rows[2]).getAllByRole("cell").slice(2, 5).map((c) => c.textContent).join(" ")).not.toMatch(/\d/);
    expect(within(rows[1]).getByText(/vs 70 bpm \(\+22\) · 1 min ago/)).toBeTruthy();
  });

  it("gives the age from the server, so a different browser clock changes nothing", async () => {
    api.getWardResearch.mockResolvedValue({
      ...ANSWER,
      ranked: [row({ baseline: [{ ...computed("heart_rate", 92, 70, 22, false, "bpm"), latest_age_s: 2400 } as Vital] })],
    });
    vi.spyOn(Date, "now").mockReturnValue(0);
    await loaded();
    expect(screen.getByText("92 vs 70 bpm (+22) · 40 min ago")).toBeTruthy();
  });

  it("says why an own baseline is unavailable, with a plain line for a reason it does not know", async () => {
    api.getWardResearch.mockResolvedValue({
      ...ANSWER,
      ranked: [row({
        baseline: [
          { vital: "heart_rate", status: "unavailable", reason: "patch_not_live" },
          { vital: "respiratory_rate", status: "unavailable", reason: "latest_bin_missing" },
          { vital: "spo2", status: "unavailable", reason: "something_new" },
        ] as ResearchRow["baseline"],
      })],
    });
    await loaded();
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(2, 5)).toEqual(["Not counted: no live patch reading", "Latest 15-minute set missing", "No own baseline for this patient now."]);
  });

  it("says each model's state in the words of the patient page", async () => {
    await loaded();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("Prediction model: Flagged")).toBeTruthy();
    expect(within(rows[1]).getByText("Alert policy: No result: the patient's own baseline is still forming")).toBeTruthy();
    expect(within(rows[2]).getByText("Prediction model: No flag")).toBeTruthy();
    expect(within(rows[4]).getByText("Prediction model: Not sure")).toBeTruthy();
    expect(within(rows[4]).getByText("Alert policy: getting ready")).toBeTruthy();
    expect(within(rows[5]).getByText("Prediction model: No result for this patient now.")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Not computed|bins/);
  });

  it("says the time at an alert level in hours and minutes, none, and cannot be rebuilt", async () => {
    await loaded();
    const last = screen.getAllByRole("row").map((r) => within(r).queryAllByRole("cell").at(-1)?.textContent);
    expect(last.slice(1)).toEqual(["3 h 15 min, now", "45 min", "none", "The stay history is not available for this patient now.", "1 h"]);
    expect(document.body.textContent).not.toMatch(/rebuilt/);
  });
});

describe("ward research: not ranked, links and reads", () => {
  it("lists the patients that are not ranked with the reason and the instruction, and names only the reader's", async () => {
    await loaded();
    const section = screen.getByRole("region", { hidden: false, name: "Ward research table" }).parentElement!;
    const list = within(section).getByRole("heading", { level: 2, name: "Not ranked" }).parentElement!;
    const items = within(list).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Bed 9 (not your patient): Heart rate missing. Measure it", "Tan, Lucinda: Blood pressure missing. Record it"]);
    expect(within(list).getByRole("link", { name: "Tan, Lucinda" }).getAttribute("href")).toBe("/patient/p-009");
  });

  it("says so in one sentence when the ward has no patients, instead of an empty table", async () => {
    api.getWardResearch.mockResolvedValue({ ...ANSWER, ranked: [], abstained: [] });
    page();
    await screen.findByText("No patients on this ward.");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
  });

  it("shows the Not ranked list alone when every patient is unranked", async () => {
    api.getWardResearch.mockResolvedValue({ ...ANSWER, ranked: [] });
    page();
    await screen.findByRole("heading", { name: "Not ranked" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText("No patients on this ward.")).toBeNull();
  });

  it("has no Not ranked section when every patient is ranked", async () => {
    api.getWardResearch.mockResolvedValue({ ...ANSWER, abstained: [] });
    await loaded();
    expect(screen.queryByRole("heading", { name: "Not ranked" })).toBeNull();
  });

  it("links to the simulated runs and to the research agent", async () => {
    await loaded();
    expect(screen.getByRole("link", { name: "Simulated compare runs" }).getAttribute("href")).toBe("/lab");
    expect(screen.getByRole("link", { name: "Ask the research agent" }).getAttribute("href")).toBe("/ask");
  });

  it("has no link to the research agent when the build cannot ask", async () => {
    api.hasLabQuestions = false;
    await loaded();
    expect(screen.queryByRole("link", { name: "Ask the research agent" })).toBeNull();
    expect(screen.getByRole("link", { name: "Simulated compare runs" })).toBeTruthy();
  });

  it("reads once on open, once for each Refresh and never on a timer", async () => {
    const interval = vi.spyOn(globalThis, "setInterval");
    await loaded();
    expect(api.getWardResearch).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(api.getWardResearch).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(api.getWardResearch).toHaveBeenCalledTimes(3));
    expect(interval.mock.calls.filter(([, ms]) => (ms ?? 0) >= 1000)).toEqual([]); // the page sets no poll
  });

  it("does not start a second read while one is open", async () => {
    let finish: (a: Answer) => void = () => {};
    api.getWardResearch.mockReturnValue(new Promise<Answer>((r) => { finish = r; }));
    page();
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.getWardResearch).toHaveBeenCalledTimes(1);
    await act(async () => finish(ANSWER));
    await screen.findByRole("table");
  });

  it("renders every stored string as text, never as markup", async () => {
    api.getWardResearch.mockResolvedValue({
      ...ANSWER, ranked: [row({ name: "<b>Bold</b>" })], abstained: [{ bed: "<i>1</i>", assigned: false, id: null, name: null, reason: "<u>r</u>", instruction: "<s>i</s>" }],
    });
    await loaded();
    expect(document.querySelector("b, i, u, s")).toBeNull();
    expect(screen.getByRole("link", { name: "<b>Bold</b>" })).toBeTruthy();
  });
});

describe("ward research: errors", () => {
  it.each([
    [401, "Your session has ended. Sign in again."],
    [403, "Not available for this account."],
    [404, "The ward was not found."],
    [500, "The table could not be read. Try Refresh."],
  ])("says what a %i means, shows no table and signs out only for 401", async (status, words) => {
    api.getWardResearch.mockRejectedValue(new api.ApiError(status));
    page();
    expect((await screen.findByRole("alert")).textContent).toBe(words);
    expect(screen.queryByRole("table")).toBeNull();
    expect(signOut).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
  });

  it("says the table could not be read for a failure that is not an API error, and a Refresh can recover", async () => {
    api.getWardResearch.mockRejectedValueOnce(new Error("offline"));
    page();
    expect((await screen.findByRole("alert")).textContent).toBe("The table could not be read. Try Refresh.");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("table");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
