// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PatientStay, Session } from "../types";

const api = vi.hoisted(() => ({
  getStays: vi.fn(),
  getWard: vi.fn(),
  build: { usesServer: false },
}));
vi.mock("../api", () => ({
  getStays: api.getStays,
  getWard: api.getWard,
  get usesServer() {
    return api.build.usesServer;
  },
}));

import { Census } from "./Census";
import { forgetWardNow, syncWardNow } from "../components/wardNow";
import { formatDay } from "../components/format";
import { bedRows, dayTicks, flowStats, formatStay, occupancy, recentMoves } from "./flowMath";

const NOW = Date.parse("2026-03-15T12:00:00Z");
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const day = (daysAgo: number) => formatDay(NOW - daysAgo * DAY); // as the page writes it, in this browser's language
const iso = (hoursAgo: number) => new Date(NOW - hoursAgo * HOUR).toISOString();
const stay = (over: Partial<PatientStay>): PatientStay => ({
  id: "n-1", name: "Cruz, Ana", bed: "4A", admitted_at: null, left_at: null, outcome: null, synthetic: true, ...over,
});

const STAYS = [
  stay({ id: "a", bed: "10B", admitted_at: iso(72), left_at: iso(24 + 12), outcome: "discharged" }),
  stay({ id: "b", bed: "2B", admitted_at: iso(6), left_at: iso(2), outcome: "higher_care" }),
  stay({ id: "c", bed: "2B" }),
  stay({ id: "d", bed: "3A", admitted_at: iso(3) }),
  stay({ id: "old", bed: "3A", admitted_at: iso(900), left_at: iso(800), outcome: "discharged" }),
];

describe("flow arithmetic", () => {
  it("writes durations plainly", () => {
    expect(formatStay(12 * 60_000)).toBe("12m");
    expect(formatStay(185 * 60_000)).toBe("3h 5m");
    expect(formatStay((5 * 1440 + 180) * 60_000)).toBe("5d 3h");
  });

  it("counts the last day and leaves unknown arrivals out of the average", () => {
    const stats = flowStats(STAYS, NOW);
    expect(stats).toMatchObject({ in24h: 2, out24h: 1, onWard: 2 });
    expect(stats.averageMs).toBeCloseTo(((36 + 4 + 100) * HOUR) / 3);
    expect(flowStats([stay({})], NOW).averageMs).toBeNull();
  });

  it("sorts beds as numbers, drops stays before the window, and starts an unknown arrival where the bed was free", () => {
    const rows = bedRows(STAYS, NOW);
    expect(rows.map((r) => r.bed)).toEqual(["2B", "3A", "10B"]);
    expect(rows[1].bars.map((b) => b.stay.id)).toEqual(["d"]);
    const [left, right] = rows[0].bars;
    expect(left.stay.id).toBe("b");
    expect(right.stay.id).toBe("c");
    expect(right.left).toBeCloseTo(left.left + left.width);
    expect(right.left + right.width).toBeCloseTo(100);
  });

  it("leaves out a bed nobody was in during the window", () => {
    expect(bedRows([stay({ bed: "9Z", admitted_at: iso(900), left_at: iso(800), outcome: "discharged" })], NOW)).toEqual([]);
  });

  it("starts an unknown arrival at the window edge when the bed has no past", () => {
    const [{ bars }] = bedRows([stay({})], NOW);
    expect(bars[0]).toMatchObject({ left: 0, width: 100 });
  });

  it("marks every other day across the window", () => {
    const ticks = dayTicks(NOW);
    expect(ticks).toHaveLength(7);
    expect(ticks[0]).toEqual({ at: NOW - 14 * 24 * HOUR, left: 0 });
    expect(ticks[1].left).toBeCloseTo(100 / 7);
  });

  it("lists the moves since a time, newest first, and skips stays with none known", () => {
    expect(recentMoves(STAYS, NOW - 14 * DAY).map((s) => s.id)).toEqual(["b", "d", "a"]);
  });

  it("counts the ward at this time of day on each of the last 14 days", () => {
    const days = occupancy(
      [
        stay({ id: "s1", admitted_at: iso(20 * 24) }),
        stay({ id: "s2", admitted_at: iso(240), left_at: iso(120), outcome: "discharged" }), // in at day 10, out at day 5
        stay({ id: "s3", admitted_at: iso(48) }),
      ],
      NOW,
    );
    expect(days.map((d) => d.at)).toEqual(Array.from({ length: 14 }, (_, i) => NOW - (13 - i) * DAY));
    expect(days.map((d) => d.count)).toEqual([1, 1, 1, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2]);
  });

  it("counts no day a patient with no recorded arrival may have been on, nor before the first arrival", () => {
    const open = stay({ id: "s1", admitted_at: iso(20 * 24) });
    expect(occupancy([open, stay({ id: "u" })], NOW)).toEqual([]); // still on the ward, arrival unknown
    const gone = stay({ id: "u", left_at: iso(72), outcome: "discharged" });
    expect(occupancy([open, gone], NOW).map((d) => [d.at, d.count])).toEqual([3, 2, 1, 0].map((k) => [NOW - k * DAY, 1]));
    expect(occupancy([stay({ id: "s", admitted_at: iso(120) })], NOW)).toHaveLength(6);
    expect(occupancy([], NOW)).toEqual([]);
  });
});

describe("Census page", () => {
  const session: Session = { clinician_id: "rn-manalo", ward: "W4", role: "charge_rn" };
  const show = () =>
    render(
      <MemoryRouter>
        <Census session={session} onSignOut={() => undefined} />
      </MemoryRouter>,
    );
  // The browser says 14 Nov 2026; the ward, as served, says 15 Mar 2026: every figure follows the ward.
  const BROWSER = Date.parse("2026-11-14T09:00:00Z");
  beforeEach(() => {
    vi.useFakeTimers({ now: BROWSER, toFake: ["Date"] });
    forgetWardNow();
    api.getWard.mockResolvedValue({ now: new Date(NOW).toISOString() });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("shows stays, links only those still on the ward, and says the data is synthetic", async () => {
    api.getStays.mockResolvedValue(STAYS);
    show();
    expect(screen.getByText("Loading...")).toBeTruthy();
    await screen.findByRole("heading", { name: "Recent movement" });
    expect(screen.getAllByRole("link", { name: "Cruz, Ana" })).toHaveLength(1); // d: arrival known, still here
    expect(screen.getAllByRole("img", { name: "Cruz, Ana, Higher care" })).toHaveLength(1);
  });

  it("labels a stay that ended in a transfer or a death", async () => {
    api.getStays.mockResolvedValue([
      stay({ id: "t", bed: "2B", admitted_at: iso(6), left_at: iso(2), outcome: "transferred" }),
      stay({ id: "x", bed: "3A", admitted_at: iso(5), left_at: iso(1), outcome: "died" }),
    ]);
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    expect(screen.getAllByRole("img", { name: "Cruz, Ana, Transferred" })).toHaveLength(1);
    expect(screen.getAllByRole("img", { name: "Cruz, Ana, Died" })).toHaveLength(1);
    // Each ended stay closes with its outcome's mark, and the legend names all five.
    const died = screen.getByRole("img", { name: "Cruz, Ana, Died" });
    expect(died.getAttribute("data-outcome")).toBe("died");
    expect(died.querySelector(".hw-timeline-beds__mark")?.getAttribute("data-outcome")).toBe("died");
    const key = screen.getByRole("list", { name: "Legend" });
    expect(within(key).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "On the ward", "Discharged", "Higher care", "Transferred", "Died",
    ]);
    expect(within(screen.getByRole("region", { name: "Bed timeline" })).getAllByRole("img")).toHaveLength(2);
  });

  it("places each stay by percentage, names the longer ones, and leaves an open stay unmarked", async () => {
    api.getStays.mockResolvedValue(STAYS);
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    const open = within(screen.getByText("3A", { selector: ".hw-timeline-beds__bed" }).parentElement!).getByRole("img", { name: "Cruz, Ana, On the ward" }); // d, in for the last 3 h
    expect(open.hasAttribute("data-outcome")).toBe(false);
    expect(open.querySelector(".hw-timeline-beds__mark")).toBeNull();
    expect(parseFloat(open.style.getPropertyValue("--hw-timeline-beds-left"))).toBeCloseTo(100 - (3 / (14 * 24)) * 100);
    expect(open.textContent).toBe(""); // too short to hold a name
    const long = screen.getByRole("img", { name: "Cruz, Ana, Discharged" }); // a, three days
    expect(long.querySelector(".hw-timeline-beds__name")?.textContent).toBe("Cruz");
    expect(screen.getByText("Now")).toBeTruthy();
  });

  it("filters the movement by outcome, with a count on each, and says when none ended that way", async () => {
    api.getStays.mockResolvedValue(STAYS);
    const user = userEvent.setup();
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    const chips = within(screen.getByRole("group", { name: "Filter by outcome" }));
    expect(chips.getAllByRole("button").map((b) => b.textContent)).toEqual(["All 3", "Discharged 1", "Higher care 1", "Transferred 0", "Died 0"]);
    expect(chips.getByRole("button", { name: "All 3" }).getAttribute("aria-pressed")).toBe("true");
    const rows = () => within(screen.getByRole("region", { name: "Recent movement table" })).getAllByRole("row").slice(1);
    expect(rows()).toHaveLength(3); // the old stay is outside the 14 days

    await user.click(chips.getByRole("button", { name: "Discharged 1" }));
    expect(chips.getByRole("button", { name: "Discharged 1" }).getAttribute("aria-pressed")).toBe("true");
    expect(chips.getByRole("button", { name: "All 3" }).getAttribute("aria-pressed")).toBe("false");
    expect(rows().map((r) => (r as HTMLTableRowElement).cells[2].textContent)).toEqual(["Discharged"]);

    await user.click(chips.getByRole("button", { name: "Died 0" }));
    expect(screen.getByText("No stay ended this way in the last 14 days.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Recent movement table" })).toBeNull();
  });

  it("shows the newest 12 moves and says how many there are", async () => {
    api.getStays.mockResolvedValue(
      Array.from({ length: 13 }, (_, i) => stay({ id: `m${i}`, bed: "5A", admitted_at: iso(100 + i), left_at: iso(i + 1), outcome: "discharged" })),
    );
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    expect(within(screen.getByRole("region", { name: "Recent movement table" })).getAllByRole("row")).toHaveLength(13);
    expect(screen.getByText("The newest 12 of 13.")).toBeTruthy();
  });

  it("draws occupancy by day with its finding as the title, in ink", async () => {
    api.getStays.mockResolvedValue([
      stay({ id: "s1", admitted_at: iso(20 * 24) }),
      stay({ id: "s2", bed: "2B", admitted_at: iso(240), left_at: iso(120), outcome: "discharged" }),
    ]);
    const { container } = show();
    // Two patients from day 10 to day 6 before now: the latest of those days is the finding.
    expect(await screen.findByRole("heading", { name: `The ward was fullest on ${day(6)}, with 2 patients` })).toBeTruthy();
    expect(screen.getByText(`Patients on the ward at this time of day, ${day(13)} to ${day(0)}.`)).toBeTruthy();
    expect(within(screen.getByRole("group", { name: /^The ward was fullest/ })).getAllByRole("button")).toHaveLength(14);
    expect(screen.getByRole("button", { name: `${day(6)}, 2 patients` }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: `${day(0)}, 1 patient` }).getAttribute("aria-pressed")).toBe("true"); // today is read first
    expect(container.querySelector("[data-hw-space]")?.getAttribute("data-hw-space")).toBe("clinical");
  });

  it("says when every day held the same, and which days are left out", async () => {
    api.getStays.mockResolvedValue([
      stay({ id: "s1", admitted_at: iso(20 * 24) }),
      stay({ id: "u", bed: "2B", left_at: iso(72), outcome: "discharged" }),
    ]);
    show();
    expect(await screen.findByRole("heading", { name: "The ward held 1 patient on each of the last 4 days" })).toBeTruthy();
    expect(
      screen.getByText(`Patients on the ward at this time of day, ${day(3)} to ${day(0)}. Earlier days are left out: an arrival before ${day(3)} is not recorded.`),
    ).toBeTruthy();
  });

  it("leaves occupancy out, saying why, while an arrival is not recorded", async () => {
    api.getStays.mockResolvedValue(STAYS); // c is on the ward with no recorded arrival
    show();
    expect(await screen.findByRole("heading", { name: "Occupancy by day" })).toBeTruthy();
    expect(screen.getByText(/A daily count needs the arrival of every patient/)).toBeTruthy();
  });

  it("counts the last day against the ward's time, not the browser's", async () => {
    api.getStays.mockResolvedValue(STAYS);
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    expect(screen.getByText("In, last 24 h").nextSibling?.textContent).toBe("2");
    expect(screen.getByText("Out, last 24 h").nextSibling?.textContent).toBe("1");
  });

  it("reads the ward for its time even when another page set an older one", async () => {
    syncWardNow(new Date(NOW - 3_600_000).toISOString());
    api.getStays.mockResolvedValue(STAYS);
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    expect(api.getWard).toHaveBeenCalled();
  });

  it("reads the ward on every refresh against a server, so the last day moves with the ward", async () => {
    vi.useFakeTimers({ now: BROWSER });
    api.build.usesServer = true;
    syncWardNow(new Date(NOW).toISOString());
    api.getStays.mockResolvedValue(STAYS);
    show();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.getWard).toHaveBeenCalledTimes(1);
    expect(screen.getByText("In, last 24 h").nextSibling?.textContent).toBe("2");
    api.getWard.mockResolvedValue({ now: new Date(NOW + 30 * 24 * 3_600_000).toISOString() }); // the ward moves a month on
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(api.getWard).toHaveBeenCalledTimes(2);
    expect(screen.getByText("In, last 24 h").nextSibling?.textContent).toBe("0");
    api.build.usesServer = false;
  });

  it("says so when the ward's time cannot be read", async () => {
    api.getWard.mockRejectedValue(new Error("down"));
    api.getStays.mockResolvedValue(STAYS);
    show();
    await screen.findByRole("alert");
    expect(screen.queryByText("Recent movement")).toBeNull();
  });

  it("says when nobody has moved yet", async () => {
    api.getStays.mockResolvedValue([stay({})]);
    show();
    await screen.findByText("No arrivals or departures yet.");
    expect(screen.getByText("--")).toBeTruthy();
  });

  it("keeps the last stays when a refresh fails, and says so", async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true });
    api.getStays.mockResolvedValueOnce(STAYS).mockRejectedValueOnce(new Error("down"));
    show();
    await screen.findByText("Recent movement");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText("Recent movement")).toBeTruthy();
  });

  it("shows only the failure when the first load fails", async () => {
    api.getStays.mockRejectedValue(new Error("down"));
    show();
    await screen.findByRole("alert");
    expect(screen.queryByText("Loading...")).toBeNull();
  });

  it.each([
    ["the API", 5_000, true],
    ["the fixture", 15_000, false],
  ])("refreshes against %s every %i ms", async (_, ms, usesServer) => {
    api.build.usesServer = usesServer;
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true });
    api.getStays.mockResolvedValue(STAYS);
    show();
    await screen.findByRole("heading", { name: "Recent movement" });
    api.getStays.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms - 1_000);
    });
    expect(api.getStays).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(api.getStays).toHaveBeenCalledTimes(1);
    api.build.usesServer = false;
  });
});
