// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session } from "../types";
import { LabFrame, SimulatedNote } from "./LabFrame";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
const phone = () =>
  vi.spyOn(window, "matchMedia").mockImplementation((q) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList);

const STRIP = "Research · Synthetic, not validated. Nothing here changes a patient's rank.";
const who = (role: Session["role"]): Session => ({ clinician_id: `${role}-x`, ward: "W4", role });
const frame = (session: Session | null, at = "/lab", title = "Simulation lab") =>
  render(
    <MemoryRouter initialEntries={[at]}>
      <LabFrame session={session} onSignOut={() => {}} title={title}>
        <p>inside</p>
      </LabFrame>
    </MemoryRouter>,
  );
const pages = (name: string) =>
  within(screen.getByRole("navigation", { name })).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")]);

describe("the research space", () => {
  it("carries the strip, in the research space, around the page and the way back to the ward", () => {
    frame(who("admin"));
    const note = screen.getByRole("note");
    expect(note.textContent).toBe(STRIP);
    expect(note.closest("[data-hw-space='research']")).toBe(screen.getByText("inside").closest("[data-hw-space='research']"));
    expect(document.querySelector("b, i, u, s")).toBeNull(); // icons are spans
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Ward" }).getAttribute("href")).toBe("/ward");
  });

  it.each(["", "/api"])("links the lab's pages for an admin (VITE_API_URL=%j) and marks the current one, the front page only on itself", (api) => {
    vi.stubEnv("VITE_API_URL", api);
    frame(who("admin"), "/lab/research");
    expect(pages("Lab pages")).toEqual([
      ["Research numbers", "/lab/research", "page"],
      ["Pipeline", "/lab/pipeline", null],
      ["Simulation lab", "/lab", null],
    ]);
  });

  it.each(["md", "charge_rn"] as const)("links the clinician's research pages for %s with the API, a patient's Ask thread marking Ask", (role) => {
    vi.stubEnv("VITE_API_URL", "/api");
    frame(who(role), "/ask/patient/p-001", "Ask the assistant");
    expect(pages("Research pages")).toEqual([
      ["Ask the assistant", "/ask", "page"],
      ["What-if runs", "/what-if", null],
      ["Ward research", "/ward/research", null],
    ]);
  });

  it("links the lab's pages for every demo clinician in the sample build, and none for a staff nurse", () => {
    frame(who("md"), "/lab");
    expect(pages("Research pages")).toEqual([
      ["Research numbers", "/lab/research", null],
      ["Pipeline", "/lab/pipeline", null],
      ["Simulation lab", "/lab", "page"],
    ]);
    cleanup();
    frame(who("rn"));
    expect(screen.getByRole("note").textContent).toBe(STRIP);
    expect(screen.queryByRole("navigation", { name: /pages/ })).toBeNull();
  });

  it("names the page in the phone's app bar", () => {
    phone();
    frame(who("md"), "/what-if", "What-if runs");
    expect(within(screen.getByRole("banner")).getByText("What-if runs")).toBeTruthy();
  });

  it("before sign-in: the strip and the page, a way back to sign-in, no account, no tabs and no page links", () => {
    frame(null, "/lab/research", "Research numbers");
    expect(screen.getByRole("note").textContent).toBe(STRIP);
    expect(screen.getByText("inside")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull(); // no account menu, no assistant
    expect(screen.getByText("Set in HarmonyOS Sans")).toBeTruthy();
  });
});

describe("SimulatedNote", () => {
  it("says the patients are simulated", () => {
    render(<SimulatedNote />);
    expect(screen.getByRole("note").textContent).toBe("Simulated patients until a hospital is connected.");
  });

  it("adds read-only for an administrator", () => {
    render(<SimulatedNote readOnly />);
    expect(screen.getByText("Simulated patients until a hospital is connected.")).toBeTruthy();
    expect(screen.getByText("Read-only. Every view is recorded in the audit log.").tagName).toBe("STRONG");
  });
});
