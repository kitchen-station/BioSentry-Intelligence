// @vitest-environment jsdom
/**
 * The account menu: the dropdown names the role and ward, and signing out saves what waits in an
 * Undo window, calls back and returns to sign-in. The page links are the top bar's (AppShell.test).
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { AccountMenu } from "./SessionMenu";
import { WardFeedContext, type WardFeed } from "./wardFeed";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

const menu = (session: Session, path = "/somewhere", feed: WardFeed | null = null) => {
  const onSignOut = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <WardFeedContext.Provider value={feed}>
        <AccountMenu session={session} onSignOut={onSignOut} />
      </WardFeedContext.Provider>
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return onSignOut;
};
const open = (id: string) => userEvent.click(screen.getByRole("button", { name: new RegExp(id) }));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("AccountMenu", () => {
  it("toggles open and closed from the trigger", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    expect(screen.queryByText("Ward W4")).toBeNull();
    await open("rn-reyes");
    expect(screen.getByText("Staff Nurse")).toBeTruthy();
    expect(screen.getByText("Ward W4")).toBeTruthy();
    await open("rn-reyes");
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it("holds no link in the open menu, to the lab or anywhere, for every role", async () => {
    for (const role of ["rn", "charge_rn", "md", "admin"] as const) {
      menu({ clinician_id: "x-1", ward: "W4", role });
      await open("x-1");
      expect(screen.getByText("Ward W4")).toBeTruthy();
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.queryByText(/Simulation lab|Research numbers|Pipeline|Station|Overview/)).toBeNull();
      cleanup();
    }
  });

  it.each([["rn", "Staff Nurse"], ["charge_rn", "Charge Nurse"], ["md", "Attending Physician"], ["admin", "Administrator"]] as const)(
    "names the role %s as %s in the dropdown",
    async (role, label) => {
      menu({ clinician_id: "x-1", ward: "W4", role });
      await open("x-1");
      expect(screen.getByText(label)).toBeTruthy();
    },
  );

  it("closes on a press outside it and on Escape, which gives focus back to the trigger", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    expect(screen.getByRole("button", { name: /rn-reyes/ }).getAttribute("aria-expanded")).toBe("true");
    await userEvent.click(screen.getByText("Ward W4")); // inside the panel: it stays
    expect(screen.getByText("Ward W4")).toBeTruthy();
    await userEvent.click(document.body);
    expect(screen.queryByText("Ward W4")).toBeNull();
    await open("rn-reyes");
    await userEvent.keyboard("{Shift}"); // any other key leaves it open
    expect(screen.getByText("Ward W4")).toBeTruthy();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("Ward W4")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /rn-reyes/ }));
  });

  it("shows an unknown role as written", async () => {
    menu({ clinician_id: "x-2", ward: "W4", role: "visitor" } as unknown as Session);
    await open("x-2");
    expect(screen.getByText("visitor")).toBeTruthy();
  });

  it("signs out: calls back, closes, and returns to sign-in", async () => {
    const onSignOut = menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledOnce();
    expect(screen.getByText("at /")).toBeTruthy();
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it("saves the acknowledgements still in their Undo window first, while the session can sign them", async () => {
    let saved!: () => void;
    const flush = vi.fn(() => new Promise<void>((res) => (saved = res)));
    const onSignOut = menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" }, "/ward", { flush } as unknown as WardFeed);
    await open("rn-reyes");
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(flush).toHaveBeenCalledOnce();
    expect(onSignOut).not.toHaveBeenCalled();
    await act(async () => saved());
    expect(onSignOut).toHaveBeenCalledWith(true);
    expect(screen.getByText("at /")).toBeTruthy();
  });

  it("signs out after 3 seconds when a save never answers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const flush = vi.fn(() => new Promise<void>(() => {}));
    const onSignOut = menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" }, "/ward", { flush } as unknown as WardFeed);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole("button", { name: /rn-reyes/ }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(onSignOut).toHaveBeenCalledWith(true);
  });

  beforeEach(() => vi.stubEnv("VITE_API_URL", "http://api.test"));

  const NOTICE = "A ward administrator can see which beds you are assigned, counts of your recorded actions from the last 24 hours, and every patient's page, including the notes you write.";

  it.each(["rn", "md", "charge_rn"] as const)("tells a %s on an API build that an administrator sees counts", async (role) => {
    menu({ clinician_id: "x-1", ward: "W4", role });
    await open("x-1");
    expect(screen.getByText(NOTICE)).toBeTruthy();
  });

  it("does not show the notice to an admin or on the fixture build", async () => {
    menu({ clinician_id: "admin-ruiz", ward: "W4", role: "admin" });
    await open("admin-ruiz");
    expect(screen.queryByText(NOTICE)).toBeNull();
    cleanup();
    vi.stubEnv("VITE_API_URL", "");
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    expect(screen.queryByText(NOTICE)).toBeNull();
  });
});
