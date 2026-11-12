// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PatientStay } from "../types";

const api = vi.hoisted(() => ({
  getStays: vi.fn(),
  build: { usesServer: false },
}));
vi.mock("../api", () => ({
  getStays: api.getStays,
  get usesServer() {
    return api.build.usesServer;
  },
}));

import { FlowSection } from "./Flow";
import { bedRows, dayTicks, flowStats, formatStay, recentMoves } from "./flowMath";

const NOW = Date.parse("2026-03-15T12:00:00Z");
const HOUR = 3_600_000;
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

  it("lists the newest moves and skips stays with none known", () => {
    expect(recentMoves(STAYS, 2).map((s) => s.id)).toEqual(["b", "d"]);
  });
});

describe("Flow page", () => {
  const show = () =>
    render(
      <MemoryRouter>
        <FlowSection />
      </MemoryRouter>,
    );
  beforeEach(() => void vi.useFakeTimers({ now: NOW, toFake: ["Date"] }));
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
    expect(screen.getByText(/Synthetic data/)).toBeTruthy();
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
