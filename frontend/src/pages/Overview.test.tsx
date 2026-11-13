// @vitest-environment jsdom
/**
 * The admin overview: it shows what the server sent as plain text, reads again only when Refresh
 * is pressed (never on a timer, since every view is an audit row), and says so when refused or
 * when the build has no server.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { WardOverview } from "../types";
import { Overview } from "./Overview";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ hasOverview: true, getOverview: vi.fn() }));
vi.mock("../api", async () => ({
  ApiError: (await vi.importActual<typeof import("../api/client")>("../api/client")).ApiError,
  get hasOverview() {
    return api.hasOverview;
  },
  getOverview: api.getOverview,
}));

const session = { clinician_id: "admin-ruiz", ward: "W4", role: "admin" } as const;
const AS_OF = "As of 2026-11-13 08:30 UTC";

const overview = (over: Partial<WardOverview> = {}): WardOverview => ({
  ward: "W4",
  as_of: "2026-11-13T08:30:00Z",
  receiving_data: true,
  synthetic: true,
  shared: { clockS: 87_000, speed: 10, ended: false, counts: { received: 5, rejected: 0, abstained: 1 } },
  patients: [
    { id: "p-001", bed: "W4-01", state: "ranked", score: 7, reason: null, care: ["md-santos", "rn-manalo", "rn-reyes"], synthetic: true },
    { id: "p-002", bed: "W4-02", state: "not_ranked", score: null, reason: "heart rate missing", care: ["rn-manalo"], synthetic: false },
  ],
  clinicians: [
    { id: "md-santos", role: "md", patients: 1 },
    { id: "rn-manalo", role: "charge_rn", patients: 2 },
    { id: "rn-reyes", role: "rn", patients: 1 },
  ],
  activity: [
    { seq: 9, at: "2026-11-13T08:00:00Z", clinician_id: "rn-reyes", action: "add_note", resource: "p-001", outcome: "ok" },
    { seq: 7, at: "2026-11-13T07:00:00Z", clinician_id: "rn-reyes", action: "view_patient", resource: "-", outcome: "not_found" },
    { seq: 8, at: "2026-11-13T07:30:00Z", clinician_id: "md-santos", action: "acknowledge", resource: "p-001", outcome: "ok" },
  ],
  ...over,
});

const show = () =>
  render(
    <MemoryRouter>
      <Overview session={session} onSignOut={() => {}} />
    </MemoryRouter>,
  );

beforeEach(() => {
  api.hasOverview = true;
  api.getOverview.mockReset().mockResolvedValue(overview());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Overview", () => {
  it("shows the standing lines, the time and the simulator", async () => {
    show();
    expect(screen.getByRole("heading", { name: "Ward overview" })).toBeTruthy();
    expect(screen.getByText("Read-only. Every view is recorded in the audit log.")).toBeTruthy();
    expect(screen.getByText("Simulated patients until a hospital is connected.")).toBeTruthy();
    expect(await screen.findByText(AS_OF)).toBeTruthy();
    expect(screen.getByText("Simulator: day 2 00:10, 10x")).toBeTruthy();
    expect(screen.queryByText("Not receiving data")).toBeNull();
  });

  it("lists patients with state as text, nurse and physician, and marks synthetic ones", async () => {
    show();
    await screen.findByText(AS_OF);
    const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row");
    const first = rows[1].textContent;
    expect(first).toContain("W4-01");
    expect(first).toContain("Synthetic");
    expect(first).toContain("NEWS2 7");
    expect(first).toContain("rn-reyes");
    expect(first).toContain("md-santos");
    expect(first).not.toContain("rn-manalo"); // the charge nurse is left out of the row
    expect(rows[2].textContent).toContain("NOT RANKED: heart rate missing");
    expect(rows[2].textContent).not.toContain("Synthetic");
    expect(rows[2].textContent).toContain("None");
    expect(screen.getByText("The charge nurse is assigned to every patient.")).toBeTruthy();
  });

  it("omits the charge nurse line when there is none, and the simulator line when it has no shape", async () => {
    api.getOverview.mockResolvedValue(
      overview({ shared: null, receiving_data: false, patients: [], clinicians: [], activity: [] }),
    );
    show();
    expect(await screen.findByText("Not receiving data")).toBeTruthy();
    expect(screen.queryByText(/charge nurse is assigned/)).toBeNull();
    expect(screen.queryByText(/Simulator:/)).toBeNull();
    expect(screen.getByText("No activity in the last 24 hours.")).toBeTruthy();
  });

  it("lists clinicians and groups activity by clinician", async () => {
    show();
    expect(await screen.findByRole("heading", { name: "rn-reyes", level: 3 })).toBeTruthy();
    expect(screen.getByText("2026-11-13 08:00 UTC add_note p-001 ok")).toBeTruthy();
    expect(screen.getByText("2026-11-13 07:00 UTC view_patient - not_found")).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(2);
    expect(screen.getByText("charge_rn")).toBeTruthy();
  });

  it("reads again once per Refresh click, disabled while loading, and never on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    show();
    await screen.findByText(AS_OF);
    expect(api.getOverview).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(api.getOverview).toHaveBeenCalledTimes(1);
    let answer!: (o: WardOverview) => void;
    api.getOverview.mockReturnValueOnce(new Promise<WardOverview>((r) => (answer = r)));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(true);
    answer(overview({ as_of: "2026-11-13T09:00:00Z" }));
    expect(await screen.findByText("As of 2026-11-13 09:00 UTC")).toBeTruthy();
    expect(api.getOverview).toHaveBeenCalledTimes(2);
  });

  it("says a 403 is not available and shows no data", async () => {
    api.getOverview.mockRejectedValue(new ApiError(403, "Forbidden", {}));
    show();
    expect((await screen.findByRole("alert")).textContent).toBe("Not available for this account.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says any other failure plainly and keeps what it had", async () => {
    show();
    await screen.findByText(AS_OF);
    api.getOverview.mockRejectedValue(new TypeError("Failed to fetch"));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
    expect(screen.getAllByRole("table")).toHaveLength(2);
  });

  it("needs the API: the demo says so and asks for nothing", () => {
    api.hasOverview = false;
    show();
    expect(screen.getByText("The overview needs the live API. This demo has no server.")).toBeTruthy();
    expect(api.getOverview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  });
});
