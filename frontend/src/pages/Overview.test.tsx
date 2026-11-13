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
import type { Session, WardOverview } from "../types";
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
  sim: { clockS: 87_000, speed: 10, ended: false, counts: { received: 5, rejected: 0, abstained: 1 } },
  patients: [
    { bed: "W4-01", state: "ranked", score: 7, reason: null, care: ["md-santos", "rn-manalo", "rn-reyes"], synthetic: true },
    { bed: "W4-02", state: "not_ranked", score: null, reason: "heart rate missing", care: ["rn-manalo"], synthetic: false },
  ],
  clinicians: [
    { id: "md-santos", role: "md", patients: 1 },
    { id: "rn-manalo", role: "charge_rn", patients: 2 },
    { id: "rn-reyes", role: "rn", patients: 1 },
  ],
  activity: [
    { clinician_id: "rn-reyes", action: "view_patient", outcome: "ok", count: 12 },
    { clinician_id: "rn-reyes", action: "view_patient", outcome: "not_found", count: 1 },
    { clinician_id: "md-santos", action: "acknowledge", outcome: "ok", count: 3 },
  ],
  ...over,
});

const show = (who: Session = session, onSignOut: () => void = () => {}) =>
  render(
    <MemoryRouter>
      <Overview session={who} onSignOut={onSignOut} />
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
    expect(first).not.toContain("rn-manalo"); // the charge nurse is on every patient: said once below
    expect(first).not.toContain("p-001");
    expect(rows[2].textContent).toContain("NOT RANKED: heart rate missing");
    expect(rows[2].textContent).not.toContain("Synthetic");
    expect(rows[2].textContent).toContain("None");
    expect(screen.getByText("The charge nurse is assigned to every patient.")).toBeTruthy();
  });

  it("omits the charge nurse line when there is none, and the simulator line when it has no shape", async () => {
    api.getOverview.mockResolvedValue(
      overview({ sim: null, receiving_data: false, patients: [], clinicians: [], activity: [] }),
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
    expect(screen.getByRole("heading", { name: "Activity in the last 24 hours" })).toBeTruthy();
    expect(screen.getByText(/Activity is shown for oversight, not performance review\. Counts only/)).toBeTruthy();
    expect(screen.getByText("view_patient ok: 12")).toBeTruthy();
    expect(screen.getByText("view_patient not_found: 1")).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(2);
    expect(screen.getByText("Charge Nurse")).toBeTruthy();
    expect(screen.queryByText("charge_rn")).toBeNull();
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

  it("says any other failure plainly and keeps nothing stale", async () => {
    show();
    await screen.findByText(AS_OF);
    api.getOverview.mockRejectedValue(new TypeError("Failed to fetch"));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("clears the old data when a refresh is refused with 403", async () => {
    show();
    await screen.findByText(AS_OF);
    api.getOverview.mockRejectedValue(new ApiError(403, "Forbidden", {}));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Not available for this account.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("clears the old data and signs out when a refresh finds the session ended", async () => {
    const out = vi.fn();
    show(session, out);
    await screen.findByText(AS_OF);
    expect(out).not.toHaveBeenCalled();
    api.getOverview.mockRejectedValue(new ApiError(401, "Unauthorized", {}));
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(screen.queryByRole("table")).toBeNull();
    expect(out).toHaveBeenCalledOnce();
  });

  it.each([{}, "not an overview", { patients: [], clinicians: [], activity: [], sim: null }])("shows no tables for a malformed body %j", async (body) => {
    api.getOverview.mockResolvedValue(body);
    show();
    expect((await screen.findByRole("alert")).textContent).toBe("The overview could not be loaded. Try Refresh.");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names the charge nurse in the row unless the charge nurse is on every patient", async () => {
    api.getOverview.mockResolvedValue(
      overview({ patients: [{ bed: "W4-01", state: "ranked", score: 1, reason: null, care: ["rn-manalo"], synthetic: false },
        { bed: "W4-02", state: "ranked", score: 1, reason: null, care: ["rn-reyes"], synthetic: false }] }),
    );
    show();
    await screen.findByText(AS_OF);
    const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row");
    expect(rows[1].textContent).toContain("rn-manalo");
    expect(screen.queryByText(/charge nurse is assigned/)).toBeNull();
  });

  it("shows a care id that is not a listed clinician, and None for an unassigned patient", async () => {
    api.getOverview.mockResolvedValue(
      overview({ patients: [{ bed: "W4-01", state: "ranked", score: 1, reason: null, care: ["rn-ghost"], synthetic: false },
        { bed: "W4-02", state: "ranked", score: 1, reason: null, care: [], synthetic: false }] }),
    );
    show();
    await screen.findByText(AS_OF);
    const rows = within(screen.getAllByRole("table")[0]).getAllByRole("row");
    expect(rows[1].textContent).toContain("rn-ghost");
    expect(rows[2].textContent).toContain("None");
  });

  it("tells a non-admin it is not available, sends no request and keeps the menu", () => {
    show({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    expect(screen.getByText("Not available for this account.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /rn-reyes/ })).toBeTruthy();
    expect(screen.queryByText(/Every view is recorded/)).toBeNull(); // nothing was viewed, so nothing was recorded
    expect(api.getOverview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  });

  it("labels its scrolling tables, marks header columns and sets the title", async () => {
    show();
    await screen.findByText(AS_OF);
    expect(document.title).toBe("Ward overview \u00b7 BioSentry");
    const regions = screen.getAllByRole("region").filter((r) => r.hasAttribute("tabindex"));
    expect(regions.map((r) => r.getAttribute("aria-labelledby"))).toEqual(["patients", "clinicians"]);
    expect(regions.every((r) => r.getAttribute("tabindex") === "0")).toBe(true);
    expect(screen.getAllByRole("columnheader").every((h) => h.getAttribute("scope") === "col")).toBe(true);
    cleanup();
    expect(document.title).toBe("BioSentry");
  });

  it("needs the API: the demo says so and asks for nothing", () => {
    api.hasOverview = false;
    show();
    expect(screen.getByText("The overview needs the live API. This demo has no server.")).toBeTruthy();
    expect(api.getOverview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  });
});
