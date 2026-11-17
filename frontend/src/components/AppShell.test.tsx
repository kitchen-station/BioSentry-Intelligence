// @vitest-environment jsdom
/**
 * The app shell: one top bar with the mark, the role's tabs, the Research (or Lab) menu with only the
 * routes the role can open in this build, the page's status, the bell and search when a ward feed is
 * there, and the account; on a phone an app bar and bottom tabs, and none for a staff nurse.
 */

import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import type { Session, SessionRole, WardResponse } from "../types";
import { AppShell, UnitSwitcher, researchItems } from "./AppShell";
import { AssistantContext, type Assistant } from "./roles";
import { WardFeedContext, type WardFeed } from "./wardFeed";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  const { pathname, search } = useLocation();
  return <p>at {pathname}{search}</p>;
}

const who = (role: SessionRole): Session => ({ clinician_id: `${role}-1`, ward: "W4", role });
const ward = { ward: "W4", ranked: synthetic.ranked, abstained: synthetic.abstained, now: "2026-09-03T07:00:00Z" } as unknown as WardResponse;
const fakeFeed = (over: Partial<WardFeed> = {}): WardFeed => ({
  data: null, fetchedAt: null, failedSince: null, sim: null, refresh: vi.fn(), pollMs: 5_000, readOnly: false,
  alerts: [], acknowledge: vi.fn(), flush: vi.fn(() => Promise.resolve()), notice: null, setNotice: vi.fn(), ...over,
});

const shell = (
  role: SessionRole = "md", path = "/ward", feed: WardFeed | null = null,
  extra: { title?: string; status?: string; assistant?: Assistant } = {},
) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AssistantContext.Provider value={extra.assistant ?? null}>
        <WardFeedContext.Provider value={feed}>
          <AppShell session={who(role)} onSignOut={vi.fn()} title={extra.title} status={extra.status && <span>{extra.status}</span>}>
            <Routes>
              <Route path="*" element={<Where />} />
            </Routes>
          </AppShell>
        </WardFeedContext.Provider>
      </AssistantContext.Provider>
    </MemoryRouter>,
  );

const main = () => screen.getByRole("navigation", { name: "Main" });
const tabNames = () => within(main()).getAllByRole("link").map((l) => l.textContent);
const research = (name = "Research") => screen.getByRole("button", { name });
const phone = (on: boolean) =>
  vi.spyOn(window, "matchMedia").mockImplementation((q) => ({ matches: on && q.includes("768"), media: q, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList);

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("the desk bar", () => {
  it("has the mark, the page's status, the content in main, the account, and credits the typeface", () => {
    shell("md", "/ward", null, { status: "Updated 09:54" });
    const bar = screen.getByRole("banner");
    expect(within(bar).getByRole("link", { name: "BioSentry, go to the ward list" }).getAttribute("href")).toBe("/ward");
    expect(within(bar).getByText("Updated 09:54")).toBeTruthy();
    expect(within(bar).getByRole("button", { name: /md-1/ })).toBeTruthy();
    expect(within(screen.getByRole("main")).getByText("at /ward")).toBeTruthy();
    expect(screen.getByText("Set in HarmonyOS Sans")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull(); // no drawer, no hamburger
  });

  it.each([
    ["charge_rn", ["Ward", "Census", "Admit"], "Research"],
    ["md", ["Ward", "Census", "Admit"], "Research"],
    ["admin", ["Overview", "Ward"], "Lab"],
  ] as const)("gives %s the tabs %j and the %s menu", (role, names, menu) => {
    shell(role);
    expect(tabNames()).toEqual(names);
    expect(research(menu).getAttribute("aria-expanded")).toBe("false");
  });

  it("gives a staff nurse no tabs and no menu, and no second ward title: the page itself names the ward", () => {
    shell("rn");
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
    expect(within(screen.getByRole("banner")).queryByText("Ward W4")).toBeNull();
    expect(screen.queryByRole("button", { name: "Research" })).toBeNull();
  });

  it.each([
    ["/ward", "Ward"],
    ["/census", "Census"],
    ["/admit", "Admit"],
    ["/station", null],
    ["/patient/p-001", null],
  ])("marks the current tab on %s, on a desk and on a phone", (path, current) => {
    shell("charge_rn", path);
    for (const n of ["Ward", "Census", "Admit"]) {
      expect(within(main()).getByRole("link", { name: n }).getAttribute("aria-current")).toBe(n === current ? "page" : null);
    }
    cleanup();
    phone(true);
    shell("charge_rn", path);
    for (const n of ["Ward", "Census", "Admit"]) {
      expect(within(main()).getByRole("link", { name: n }).getAttribute("aria-current")).toBe(n === current ? "page" : null);
    }
  });

  it("sends Census and Admit to their own pages, and Overview for an admin", async () => {
    shell("md");
    await userEvent.click(within(main()).getByRole("link", { name: "Census" }));
    expect(screen.getByText("at /census")).toBeTruthy();
    await userEvent.click(within(main()).getByRole("link", { name: "Admit" }));
    expect(screen.getByText("at /admit")).toBeTruthy();
    cleanup();
    shell("admin", "/overview");
    expect(within(main()).getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe("page");
  });
});

describe("the Research and Lab menus", () => {
  const items = () => within(document.querySelector<HTMLElement>(".hw-menu")!).getAllByRole("link").map((l) => l.getAttribute("href"));

  it.each([
    ["md", "", ["/lab/research", "/lab/pipeline", "/lab"]],
    ["charge_rn", "", ["/lab/research", "/lab/pipeline", "/lab"]],
    ["md", "/api", ["/ask", "/what-if", "/ward/research"]],
    ["charge_rn", "/api", ["/ask", "/what-if", "/ward/research"]],
  ] as const)("lists for %s with VITE_API_URL=%j only the routes it can open", async (role, api, hrefs) => {
    vi.stubEnv("VITE_API_URL", api);
    shell(role);
    await userEvent.click(research());
    expect(items()).toEqual(hrefs);
  });

  it.each(["", "/api"])("lists the lab for an admin (VITE_API_URL=%j), under the name Lab", async (api) => {
    vi.stubEnv("VITE_API_URL", api);
    shell("admin");
    await userEvent.click(research("Lab"));
    expect(items()).toEqual(["/lab/research", "/lab/pipeline", "/lab"]);
  });

  it("has no items for a staff nurse or an unknown role", () => {
    expect(researchItems("rn")).toEqual([]);
    expect(researchItems("visitor")).toEqual([]);
  });

  it("names each item and says first that nothing here changes a rank", async () => {
    vi.stubEnv("VITE_API_URL", "/api");
    shell("md");
    await userEvent.click(research());
    expect(screen.getByText("Research space. Synthetic, not validated. Nothing here changes a patient’s rank.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /^Ask the assistant/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /^What-if runs Change a rule/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /^Ward research Every patient/ })).toBeTruthy();
  });

  it("closes on Esc, giving focus back, on a press outside, and when an item opens its page", async () => {
    vi.stubEnv("VITE_API_URL", "/api");
    shell("md");
    await userEvent.click(research());
    await userEvent.keyboard("{Escape}");
    expect(research().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(research());
    await userEvent.click(research());
    await userEvent.click(document.body);
    expect(research().getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(research());
    await userEvent.click(screen.getByRole("link", { name: /^What-if runs/ }));
    expect(screen.getByText("at /what-if")).toBeTruthy();
    expect(research().getAttribute("aria-expanded")).toBe("false");
  });
});

describe("the Analytics link", () => {
  it.each(["md", "admin", "rn"] as const)("is in the bar for %s when the build sets a same-site path", (role) => {
    vi.stubEnv("VITE_ANALYTICS_URL", "/simulator/#analytics");
    shell(role);
    const link = within(main()).getByRole("link", { name: "Analytics" });
    expect(link.getAttribute("href")).toBe("/simulator/#analytics");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it.each([
    undefined, "", "//evil.example", "/\\evil.example", "https://x.example/a", "javascript:alert(1)", "simulator/", "/a b",
    "/\t/evil.example", "/\n/evil.example", "/ok\n", " /x", "﻿/x", "/a\\b",
  ])("is hidden for the value %j", (value) => {
    if (value !== undefined) vi.stubEnv("VITE_ANALYTICS_URL", value);
    shell("md");
    expect(screen.queryByRole("link", { name: "Analytics" })).toBeNull();
  });

  it("shows for a path with a percent-escape: it stays on this site", () => {
    vi.stubEnv("VITE_ANALYTICS_URL", "/%5Cevil.example");
    shell("md");
    expect(screen.getByRole("link", { name: "Analytics" }).getAttribute("href")).toBe("/%5Cevil.example");
  });
});

describe("what the bar takes from the ward feed", () => {
  it("has no bell and no search without a feed", () => {
    shell("md");
    expect(screen.queryByRole("button", { name: /unacknowledged alert/ })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows the bell and, once the ward is read, the search", () => {
    shell("md", "/ward", fakeFeed());
    expect(screen.getByRole("button", { name: "0 unacknowledged alerts" })).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    cleanup();
    shell("md", "/ward", fakeFeed({ data: ward }));
    expect(screen.getByRole("combobox", { name: "Find a patient by name or bed" })).toBeTruthy();
  });

  it("has no bell for an administrator's read-only feed, and keeps the search", () => {
    shell("admin", "/ward", fakeFeed({ data: ward, readOnly: true }));
    expect(screen.queryByRole("button", { name: /unacknowledged alert/ })).toBeNull();
    expect(screen.getByRole("combobox")).toBeTruthy();
  });

  it("shows the feed's notice above the page, and Dismiss clears it", async () => {
    const setNotice = vi.fn();
    shell("md", "/ward", fakeFeed({ notice: "Could not save the acknowledgement. The alert stays open: try again.", setNotice }));
    const notice = screen.getByRole("status");
    expect(notice.textContent).toContain("Could not save the acknowledgement");
    await userEvent.click(within(notice).getByRole("button", { name: "Dismiss" }));
    expect(setNotice).toHaveBeenCalledWith(null);
  });
});

describe("on a phone", () => {
  const bottom = () => screen.queryByRole("navigation", { name: "Main" });

  it.each([
    ["charge_rn", ["Ward", "Census", "Admit"], "Research"],
    ["md", ["Ward", "Census", "Admit"], "Research"],
    ["admin", ["Overview", "Ward"], "Lab"],
  ] as const)("gives %s bottom tabs %j and %s, and no hamburger", (role, names, menu) => {
    phone(true);
    shell(role);
    const nav = bottom()!;
    expect(nav.className).toContain("hw-bottomnav");
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual(names);
    expect(within(nav).getByRole("button", { name: menu })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
  });

  it("gives a staff nurse no bottom bar", () => {
    phone(true);
    shell("rn");
    expect(bottom()).toBeNull();
  });

  it("puts the page's title and status in the app bar, and the bell, but no search", () => {
    phone(true);
    shell("md", "/patient/p-001", fakeFeed({ data: ward }), { title: "Santos, Maria", status: "Updated 09:54" });
    const bar = screen.getByRole("banner");
    expect(within(bar).getByText("Santos, Maria")).toBeTruthy();
    expect(within(bar).getByText("Updated 09:54")).toBeTruthy();
    expect(within(bar).getByRole("button", { name: /unacknowledged alerts/ })).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
    cleanup();
    shell("rn");
    expect(within(screen.getByRole("banner")).getByText("Ward W4")).toBeTruthy(); // no title given: the ward
  });

  it("opens the Research menu from the bottom bar", async () => {
    phone(true);
    vi.stubEnv("VITE_API_URL", "/api");
    shell("md");
    await userEvent.click(within(bottom()!).getByRole("button", { name: "Research" }));
    expect(screen.getByRole("link", { name: /^Ask the assistant/ })).toBeTruthy();
  });

  it("follows the width as it changes", () => {
    let change = () => {};
    let narrow = false;
    vi.spyOn(window, "matchMedia").mockImplementation((q) => ({
      get matches() { return narrow; }, media: q,
      addEventListener: (_: string, fn: () => void) => { change = fn; }, removeEventListener() {},
    }) as unknown as MediaQueryList);
    shell("md");
    expect(screen.getByRole("navigation", { name: "Main" }).className).toContain("hw-header__nav");
    narrow = true;
    act(() => change());
    expect(screen.getByRole("navigation", { name: "Main" }).className).toContain("hw-bottomnav");
  });
});

// Where it shows is the panel's rule, tested with the real panel in ChatBubble.test; here, how the bar draws it.
describe("the assistant's button", () => {
  it("is not in the bar where the assistant cannot be opened", () => {
    shell("md");
    expect(screen.queryByRole("button", { name: /assistant/i })).toBeNull();
  });

  it("says Assistant beside the bell on a desk, mirrors the panel, and hands itself to the panel", async () => {
    const toggle = vi.fn();
    shell("md", "/ward", fakeFeed(), { assistant: { open: false, toggle } });
    const button = within(screen.getByRole("banner")).getByRole("button", { name: "Assistant" });
    expect(button.className).toContain("hw-button");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(button.nextElementSibling?.querySelector(".hw-bell")).not.toBeNull();
    await userEvent.click(button);
    expect(toggle).toHaveBeenCalledWith(button);
  });

  it("is an icon in the phone's app bar, named Ask the assistant", () => {
    phone(true);
    shell("rn", "/patient/p-001", null, { assistant: { open: true, toggle: vi.fn() } });
    const button = within(screen.getByRole("banner")).getByRole("button", { name: "Ask the assistant" });
    expect(button.className).toBe("hw-appbar__action");
    expect(button.textContent).toBe("");
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });
});

describe("the unit switcher", () => {
  it("shows nothing for one unit, as every session today", () => {
    const { container } = render(<UnitSwitcher units={["W4"]} current="W4" onPick={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("lists the units, marks the current one, and switches", async () => {
    const onPick = vi.fn();
    render(<UnitSwitcher units={["W4", "W5"]} current="W4" onPick={onPick} />);
    await userEvent.click(screen.getByRole("button", { name: "Ward W4" }));
    expect(screen.getAllByRole("button", { name: "Ward W4" })[1].getAttribute("aria-current")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "Ward W5" }));
    expect(onPick).toHaveBeenCalledWith("W5");
    expect(screen.queryByRole("button", { name: "Ward W5" })).toBeNull();
  });
});
