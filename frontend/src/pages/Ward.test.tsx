// @vitest-environment jsdom
/**
 * The Ward page: acknowledgements go to the API (and a failed save is shown, not swallowed).
 */

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import type { Session, WardResponse } from "../types";
import { Ward } from "./Ward";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getWard: vi.fn(),
  getAcknowledgements: vi.fn(),
  acknowledge: vi.fn(),
  getLiveWard: vi.fn(),
  build: { usesServer: false, canRecord: false },
}));
vi.mock("../api", () => ({
  ...api,
  get canRecordVitals() {
    return api.build.canRecord;
  },
  recordVitals: vi.fn(),
  get usesServer() {
    return api.build.usesServer;
  },
}));

// The committed ward, p-001 first: a NEWS2 9 patient, so an alert is open.
const rows = synthetic.ranked as unknown as WardResponse["ranked"];
const ward = (): WardResponse => ({
  ward: "W4",
  clinician: "rn-manalo",
  shift: "07:00",
  receiving_data: true,
  sort_tier: "A",
  tier_status: rows[0].tier_status,
  ranked: rows.slice(0, 1),
  abstained: [],
});
const session: Session = { clinician_id: "rn-manalo", ward: "W4", role: "charge_rn" };
const admin: Session = { clinician_id: "admin-santos", ward: "W4", role: "admin" };
const page = (who: Session = session) =>
  render(
    <MemoryRouter initialEntries={["/ward"]}>
      <Ward session={who} onSignOut={() => undefined} />
    </MemoryRouter>,
  );

beforeEach(() => {
  api.getWard.mockReset().mockResolvedValue(ward());
  api.getAcknowledgements.mockReset().mockResolvedValue([]);
  api.acknowledge.mockReset().mockResolvedValue(undefined);
  api.getLiveWard.mockReset().mockResolvedValue(null);
  api.build.usesServer = false;
  api.build.canRecord = false;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const expectLabEntry = () => {
  expect(screen.queryByRole("link", { name: "Simulation lab" })).toBeNull(); // in the menu, not the bar
  expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
};

describe("the simulated note", () => {
  it("says the patients are simulated, once, with the current Ward link in the bar", async () => {
    page();
    expect(await screen.findAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Ward" }).getAttribute("aria-current")).toBe("page");
  });
});

describe("acknowledging an alert", () => {
  // Timed out at the default 5s on a slow Cloudflare build.
  it("saves through the API and closes the alert once it is saved", async () => {
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(api.acknowledge).toHaveBeenCalledWith("p-001", 9);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull());
  }, 15000);

  it("shows a failed save and leaves the alert open", async () => {
    const user = userEvent.setup();
    api.acknowledge.mockRejectedValue(new Error("down"));
    page();
    await user.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText(/Could not save the acknowledgement/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeTruthy();
  });

  it("does not raise an alert the server says was acknowledged at this score", async () => {
    api.getAcknowledgements.mockResolvedValue([{ patient_id: "p-001", score: 9 }]);
    page();
    await screen.findByText(/Priority queue/);
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
  });
});

describe("when the server fails", () => {
  it("says the ward server cannot be reached when the first load fails", async () => {
    api.getWard.mockRejectedValue(new Error("down"));
    page();
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The ward server cannot be reached. Retrying every 15 seconds.",
    );
    expectLabEntry();
  });

  it("shows the lab entry and the quiet line while the ward loads", () => {
    api.getWard.mockReturnValue(new Promise(() => {}));
    page();
    expect(screen.getByText("Loading ward…")).toBeTruthy();
    expectLabEntry();
  });

  it("keeps every alert open, says so, and lets the notice be dismissed", async () => {
    const user = userEvent.setup();
    api.getAcknowledgements.mockRejectedValue(new Error("down"));
    page();
    expect(await screen.findByText(/Could not load your acknowledgements/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Could not load your acknowledgements/)).toBeNull();
  });
});

// What the simulator publishes in `shared`: day 2, 06:20 on the simulated clock.
const live = (shared: unknown) => ({ shared, patients: [], can_control: false });
const published = (over: object = {}) =>
  live({ clockS: 109_200, speed: 10, ended: false, counts: { received: 120, rejected: 3, abstained: 5 }, ...over });

describe("the simulator status line", () => {
  beforeEach(() => {
    api.build.usesServer = true; // only the API has a simulator
  });

  it("says the simulated time, the speed and the counts", async () => {
    api.getLiveWard.mockResolvedValue(published());
    page();
    expect(
      await screen.findByText("Simulator: day 2 06:20, 10x · 120 received · 3 rejected · 5 abstained"),
    ).toBeTruthy();
  });

  it("says paused at speed 0 and ended when the run is over", async () => {
    api.getLiveWard.mockResolvedValue(published({ speed: 0 }));
    page();
    expect(await screen.findByText(/^Simulator: day 2 06:20, paused/)).toBeTruthy();
    cleanup();
    api.getLiveWard.mockResolvedValue(published({ ended: true }));
    page();
    expect(await screen.findByText(/^Simulator: day 2 06:20, ended/)).toBeTruthy();
  });

  it.each([
    ["there is no simulator", null],
    ["nothing is published yet", live(null)],
    ["what is published is not the simulator's status", live({ clockS: "soon" })],
  ])("shows no line when %s", async (_, view) => {
    api.getLiveWard.mockResolvedValue(view);
    page();
    await screen.findByText(/Priority queue/);
    await waitFor(() => expect(api.getLiveWard).toHaveBeenCalled());
    expect(screen.queryByText(/Simulator:/)).toBeNull();
  });

  it("follows the simulator with the ward, in the same round, and drops the line when it cannot be read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.getLiveWard
      .mockResolvedValueOnce(published())
      .mockResolvedValueOnce(published({ clockS: 109_260 }))
      .mockRejectedValue(new Error("down"));
    page();
    await screen.findByText(/^Simulator: day 2 06:20/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await screen.findByText(/^Simulator: day 2 06:21/);
    expect(api.getLiveWard).toHaveBeenCalledTimes(2);
    expect(api.getWard).toHaveBeenCalledTimes(2); // one round: the rows and the status together
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await waitFor(() => expect(screen.queryByText(/Simulator:/)).toBeNull());
  });

  it("is not asked for on the fixture: no call, now or later", async () => {
    api.build.usesServer = false;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    page();
    await screen.findByText(/Priority queue/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByText(/Simulator:/)).toBeNull();
  });
});

describe("how often the ward is asked for", () => {
  it.each([
    ["the fixture", 15_000, false],
    ["the API", 5_000, true],
  ])("%s: every %i ms, and the offline notice says so", async (_, ms, usesServer) => {
    api.build.usesServer = usesServer;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    page();
    await screen.findByText(/Priority queue/);
    api.getWard.mockClear().mockRejectedValue(new Error("down"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms - 1_000);
    });
    expect(api.getWard).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(api.getWard).toHaveBeenCalledTimes(1);
    const offline = await screen.findByText(new RegExp(`Retrying every ${ms / 1000} seconds\\.`));
    const note = screen.getByText("Simulated patients until a hospital is connected.");
    expect(offline.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("an administrator's ward page (read only)", () => {
  const unranked = synthetic.abstained as unknown as WardResponse["abstained"];
  beforeEach(() => {
    api.build.usesServer = true;
    api.build.canRecord = true;
    api.getWard.mockResolvedValue({ ...ward(), abstained: unranked.slice(0, 1) });
  });

  it("shows the list and the read-only line, with no write control, alert or acknowledgement call", async () => {
    page(admin);
    expect(await screen.findByText(/Priority queue/)).toBeTruthy();
    expect(screen.getByText(/Read-only\. Views are recorded in the audit log\./)).toBeTruthy();
    expect(screen.getByText(/Not ranked \(1\)/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Record/ })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.title).toBe("BioSentry");
    expect(api.getAcknowledgements).not.toHaveBeenCalled();
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByText(/Simulator:/)).toBeNull();
  });

  it("asks once, never on a timer, and again only on Refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page(admin);
    await screen.findByText(/Priority queue/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(api.getWard).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.getWard).toHaveBeenCalledTimes(2);
    expect(api.getLiveWard).not.toHaveBeenCalled();
  });

  it("tells the admin to use Refresh when the server cannot be reached, and when it was", async () => {
    api.getWard.mockReset().mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    page(admin);
    expect(await screen.findByText("The ward server cannot be reached. Use Refresh to try again.")).toBeTruthy();
    api.getWard.mockResolvedValue(ward());
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(/Priority queue/);
    api.getWard.mockRejectedValue(new Error("down"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Use Refresh to try again\./)).toBeTruthy();
  });

  it("leaves a clinician's page as it was: Record is offered", async () => {
    page();
    await screen.findByText(/Not ranked \(1\)/);
    expect(screen.getAllByRole("button", { name: "Record vitals" }).length).toBeGreaterThan(0);
  });
});
