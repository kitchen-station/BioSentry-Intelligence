// @vitest-environment jsdom
/**
 * The session menu: the top bar links only the pages the role can use (Ward, Station, Overview, and
 * Analytics when the build sets a same-site path); the dropdown names the role and ward, and signing out calls back and returns to sign-in.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { SessionMenu } from "./SessionMenu";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

const menu = (session: Session, path = "/somewhere") => {
  const onSignOut = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <SessionMenu session={session} onSignOut={onSignOut} />
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
});

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

  it("holds no link to the lab in the open menu, for every role", async () => {
    for (const role of ["rn", "charge_rn", "md", "admin"] as const) {
      menu({ clinician_id: "x-1", ward: "W4", role });
      await open("x-1");
      expect(screen.getByText("Ward W4")).toBeTruthy();
      expect(document.querySelector('a[href="/lab"]')).toBeNull();
      expect(screen.queryByText(/Simulation lab/)).toBeNull();
      cleanup();
    }
  });

  it("takes a physician from the bar to the What-if page", async () => {
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    await userEvent.click(screen.getByRole("link", { name: "What-if runs" }));
    expect(screen.getByText("at /what-if")).toBeTruthy();
  });

  it("shows Ward in the bar on a lab page and goes to the ward", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" }, "/lab/research");
    await userEvent.click(screen.getByRole("link", { name: "Ward" }));
    expect(screen.getByText("at /ward")).toBeTruthy();
  });

  it.each([
    ["rn", ["Ward"]],
    ["charge_rn", ["Ward", "Ask", "What-if runs", "Station"]],
    ["md", ["Ward", "Ask", "What-if runs", "Station"]],
    ["admin", ["Overview", "Ward"]],
  ] as const)("shows %s only the bar links for routes it can use", (role, names) => {
    vi.stubEnv("VITE_API_URL", ""); // the fixture build: Ward research needs the API
    menu({ clinician_id: "x-1", ward: "W4", role });
    const bar = screen.getByRole("navigation", { name: "Pages" });
    expect(within(bar).getAllByRole("link").map((l) => l.textContent)).toEqual(names);
  });

  it.each([
    ["rn", ["Ward"]],
    ["charge_rn", ["Ward", "Ask", "Station", "Ward research"]],
    ["md", ["Ward", "Ask", "Station", "Ward research"]],
    ["admin", ["Overview", "Ward"]],
  ] as const)("adds Ward research to the bar of %s only with the API and only for a physician or the charge nurse", (role, names) => {
    vi.stubEnv("VITE_API_URL", "/api");
    menu({ clinician_id: "x-1", ward: "W4", role });
    const bar = screen.getByRole("navigation", { name: "Pages" });
    expect(within(bar).getAllByRole("link").map((l) => l.textContent)).toEqual(names);
  });

  it("has no Ward research link in the fixture build, and the link opens the table", async () => {
    vi.stubEnv("VITE_API_URL", "");
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    expect(screen.queryByRole("link", { name: "Ward research" })).toBeNull();
    cleanup();
    vi.stubEnv("VITE_API_URL", "/api");
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    await userEvent.click(screen.getByRole("link", { name: "Ward research" }));
    expect(screen.getByText("at /ward/research")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ward" }).getAttribute("aria-current")).toBeNull();
  });

  it.each([
    ["/ward", "Ward"],
    ["/station", "Station"],
    ["/patient/p1", null],
  ] as const)("marks the current bar link on %s", (path, current) => {
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" }, path);
    const mark = (n: string) => screen.getByRole("link", { name: n }).getAttribute("aria-current");
    for (const n of ["Ward", "Station"]) expect(mark(n)).toBe(n === current ? "page" : null);
  });

  it.each(["md", "charge_rn"] as const)("sends %s to the Ask page from the bar, and marks it on a thread", async (role) => {
    menu({ clinician_id: "x-1", ward: "W4", role }, "/ask/patient/p-001");
    expect(screen.getByRole("link", { name: "Ask" }).getAttribute("aria-current")).toBe("page");
    await userEvent.click(screen.getByRole("link", { name: "Ask" }));
    expect(screen.getByText("at /ask")).toBeTruthy();
  });

  it("marks Overview for an admin on /overview", () => {
    menu({ clinician_id: "admin-ruiz", ward: "W4", role: "admin" }, "/overview");
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
  });

  it.each(["md", "admin"] as const)("adds an Analytics link for %s when the build sets a same-site path", (role) => {
    vi.stubEnv("VITE_ANALYTICS_URL", "/simulator/#analytics");
    menu({ clinician_id: "x-1", ward: "W4", role });
    const link = screen.getByRole("link", { name: "Analytics" });
    expect(link.getAttribute("href")).toBe("/simulator/#analytics");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it.each([
    undefined, "", "//evil.example", "/\\evil.example", "https://x.example/a", "javascript:alert(1)", "simulator/", "/a b",
    "/\t/evil.example", "/\n/evil.example", "/ok\n", " /x", "\uFEFF/x", "/a\\b",
  ])(
    "hides Analytics for the value %j",
    (value) => {
      if (value === undefined) vi.unstubAllEnvs();
      else vi.stubEnv("VITE_ANALYTICS_URL", value);
      menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
      expect(screen.queryByRole("link", { name: "Analytics" })).toBeNull();
    },
  );

  it("shows Analytics for a path with a percent-escape: it stays on this site", () => {
    vi.stubEnv("VITE_ANALYTICS_URL", "/%5Cevil.example");
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    expect(screen.getByRole("link", { name: "Analytics" }).getAttribute("href")).toBe("/%5Cevil.example");
  });

  it.each([["rn", "Staff Nurse"], ["charge_rn", "Charge Nurse"], ["md", "Attending Physician"], ["admin", "Administrator"]] as const)(
    "names the role %s as %s in the dropdown",
    async (role, label) => {
      menu({ clinician_id: "x-1", ward: "W4", role });
      await open("x-1");
      expect(screen.getByText(label)).toBeTruthy();
    },
  );

  it("no longer lists research or the pipeline in the dropdown", async () => {
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    await open("md-santos");
    expect(screen.queryByRole("link", { name: "Research numbers" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Pipeline" })).toBeNull();
  });

  it("closes when the backdrop is clicked", async () => {
    menu({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    await open("rn-reyes");
    const backdrop = screen.getByText("Ward W4").closest("div")!.parentElement!
      .previousElementSibling as HTMLElement;
    await userEvent.click(backdrop);
    expect(screen.queryByText("Ward W4")).toBeNull();
  });

  it("keeps Ward Monitor, Station and Overview out of the dropdown", async () => {
    menu({ clinician_id: "md-santos", ward: "W4", role: "md" });
    await open("md-santos");
    expect(screen.queryByRole("link", { name: "Ward Monitor" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Overview" })).toBeNull();
    expect(screen.getAllByRole("link", { name: "Station" })).toHaveLength(1);
  });

  it.each(["md", "charge_rn"] as const)("sends %s to the station from the bar", async (role) => {
    menu({ clinician_id: "x-1", ward: "W4", role });
    await userEvent.click(screen.getByRole("link", { name: "Station" }));
    expect(screen.getByText("at /station")).toBeTruthy();
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

  it("gives an admin the overview and the ward in the bar, and no station", async () => {
    menu({ clinician_id: "admin-ruiz", ward: "W4", role: "admin" }, "/lab");
    expect(screen.getByRole("link", { name: "Ward" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Station" })).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Overview" }));
    expect(screen.getByText("at /overview")).toBeTruthy();
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
