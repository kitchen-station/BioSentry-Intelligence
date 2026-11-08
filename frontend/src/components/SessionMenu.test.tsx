// @vitest-environment jsdom
/**
 * The session menu: it opens and closes, names the role and ward, offers the station only to a
 * physician or a charge nurse, and signing out calls back and returns to sign-in.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { SessionMenu } from "./SessionMenu";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

const menu = (session: Session) => {
  const onSignOut = vi.fn();
  render(
    <MemoryRouter initialEntries={["/somewhere"]}>
      <SessionMenu session={session} onSignOut={onSignOut} />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return onSignOut;
};
const open = (id: string) => userEvent.click(screen.getByRole("button", { name: new RegExp(id) }));

afterEach(cleanup);

describe("SessionMenu", () => {
  it("toggles open and closed from the trigger", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    expect(screen.queryByText("Ward W4")).toBeNull();
    await open("rn-reyes");
    expect(screen.getByText("Staff Nurse")).toBeTruthy();
    expect(screen.getByText("Ward W4")).toBeTruthy();
    await open("rn-reyes");
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it("closes when the backdrop is clicked", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    const backdrop = screen.getByText("Ward W4").closest("div")!.parentElement!
      .previousElementSibling as HTMLElement;
    await userEvent.click(backdrop);
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it("offers a staff nurse the ward only, and following it closes the menu", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    expect(screen.queryByRole("link", { name: "Station" })).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Ward Monitor" }));
    expect(screen.getByText("at /ward")).toBeTruthy();
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it.each([
    ["md", "Attending Physician"],
    ["charge_rn", "Charge Nurse"],
  ] as const)("offers %s the station", async (role, label) => {
    menu({ clinician_id: "x-1", ward: "W4", role });
    await open("x-1");
    expect(screen.getByText(label)).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "Station" }));
    expect(screen.getByText("at /station")).toBeTruthy();
    expect(screen.queryByText("Ward W4")).toBeNull();
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
});
