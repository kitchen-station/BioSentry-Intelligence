// @vitest-environment jsdom
/**
 * The Ward page: acknowledgements go to the API (and a failed save is shown, not swallowed),
 * and replayed real data carries its attribution.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
}));
vi.mock("../api", () => ({ ...api, canRecordVitals: false, recordVitals: vi.fn() }));

// The committed ward, p-001 first: a NEWS2 9 patient, so an alert is open.
const rows = synthetic.ranked as unknown as WardResponse["ranked"];
const ward = (synthetic_: boolean): WardResponse => ({
  ward: "W4",
  clinician: "rn-manalo",
  shift: "07:00",
  receiving_data: true,
  sort_tier: "A",
  tier_status: rows[0].tier_status,
  ranked: rows.slice(0, 1).map((r) => ({ ...r, synthetic: synthetic_ })),
  abstained: [],
});
const session: Session = { clinician_id: "rn-manalo", ward: "W4", role: "charge_rn" };
const page = () =>
  render(
    <MemoryRouter>
      <Ward session={session} onSignOut={() => undefined} />
    </MemoryRouter>,
  );

beforeEach(() => {
  api.getWard.mockReset().mockResolvedValue(ward(true));
  api.getAcknowledgements.mockReset().mockResolvedValue([]);
  api.acknowledge.mockReset().mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("acknowledging an alert", () => {
  it("saves through the API and closes the alert once it is saved", async () => {
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole("button", { name: "Acknowledge" }));
    expect(api.acknowledge).toHaveBeenCalledWith("p-001", 9);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull());
  });

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

describe("the attribution banner", () => {
  const text = "Real de-identified ICU data replayed. eICU-CRD Demo 2.0.1, ODbL.";

  it("shows when any row is not synthetic", async () => {
    api.getWard.mockResolvedValue(ward(false));
    page();
    expect(await screen.findByText(text)).toBeTruthy();
  });

  it("is absent for synthetic data", async () => {
    page();
    await screen.findByText(/Priority queue/);
    expect(screen.queryByText(text)).toBeNull();
  });
});
