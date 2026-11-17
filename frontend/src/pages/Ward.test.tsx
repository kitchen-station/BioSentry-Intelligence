// @vitest-environment jsdom
/**
 * The Ward page: the ranked list with its filters, the NOT RANKED strip, the table and the compact
 * view; alerts as a state of the row, acknowledged with an Undo window (saved to the API after it, and
 * a failed save shown, not swallowed); the bell reads the same alerts; the last ward stays when reads fail.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import { obsDue } from "../components/risk";
import { formatClock } from "../components/format";
import type { Session, WardResponse } from "../types";
import { Ward } from "./Ward";

// "Sep" or "Sept", whichever the runtime's en-GB gives: the clock uses the same call as the simulator.
const SEP = new Date(Date.UTC(2026, 8, 3)).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getWard: vi.fn(),
  getAcknowledgements: vi.fn(),
  acknowledge: vi.fn(),
  getLiveWard: vi.fn(),
  recordVitals: vi.fn(),
  build: { usesServer: false, canRecord: false },
}));
vi.mock("../api", () => ({
  ...api,
  get canRecordVitals() {
    return api.build.canRecord;
  },
  get usesServer() {
    return api.build.usesServer;
  },
}));

// The committed ward, p-001 first: a NEWS2 9 patient, so an alert is open.
const rows = synthetic.ranked as unknown as WardResponse["ranked"];
const unranked = synthetic.abstained as unknown as WardResponse["abstained"];
const ward = (over: Partial<WardResponse> = {}): WardResponse => ({
  ward: "W4",
  clinician: "rn-manalo",
  shift: "07:00",
  receiving_data: true,
  sort_tier: "A",
  tier_status: rows[0].tier_status,
  ranked: rows.slice(0, 1),
  abstained: [],
  now: "2026-09-03T07:00:00Z",
  ...over,
});
const session: Session = { clinician_id: "rn-manalo", ward: "W4", role: "charge_rn" };
const admin: Session = { clinician_id: "admin-santos", ward: "W4", role: "admin" };

function Where() {
  return <p>at {useLocation().pathname}</p>;
}
const page = (who: Session = session) =>
  render(
    <MemoryRouter initialEntries={["/ward"]}>
      <Ward session={who} onSignOut={() => undefined} />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
const loaded = () => screen.findByRole("group", { name: "Filter patients" });
const ackButton = (name = "Santos, Maria") => screen.queryByRole("button", { name: `Acknowledge the alert for ${name}` });
const rowOf = (name: string) => screen.getByRole("link", { name }).closest("li")!;
const chip = (name: RegExp) => within(screen.getByRole("group", { name: "Filter patients" })).getByRole("button", { name });
const undoButton = () => screen.queryByRole("button", { name: "Undo" });
const banner = () => screen.queryByRole("alert");

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
  vi.unstubAllGlobals();
  document.querySelector(".hw-toast-region")?.remove();
  delete (document as { visibilityState?: string }).visibilityState; // back to the real getter
});

const expectLabEntry = () => {
  expect(screen.queryByRole("link", { name: /Simulation lab/ })).toBeNull(); // in the menu, not the bar
  expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
};

describe("the simulated note", () => {
  it("says the patients are simulated, once, with the current Ward tab in the bar", async () => {
    page();
    expect(await screen.findAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
    const bar = within(screen.getByRole("navigation", { name: "Main" }));
    expect(bar.getByRole("link", { name: "Ward" }).getAttribute("aria-current")).toBe("page");
  });
});

describe("the header", () => {
  it("names the ward and counts the patients, ranked on the live tiers, or on NEWS2 when no tier is listed", async () => {
    page();
    await loaded();
    expect(screen.getByRole("heading", { level: 1, name: "Ward W4" })).toBeTruthy();
    const tiers = ward().tier_status.filter((t) => t.live).map((t) => t.label).join(", ");
    expect(screen.getByText(`1 patient · 1 ranked on ${tiers}`)).toBeTruthy();
    cleanup();
    api.getWard.mockResolvedValue(ward({ tier_status: [], ranked: rows.slice(0, 3), abstained: unranked }));
    page();
    expect(await screen.findByText("5 patients · 3 ranked on NEWS2")).toBeTruthy();
    expect(screen.queryByText(/Priority queue/)).toBeNull(); // the old heading is gone
  });
});

describe("the filter chips", () => {
  beforeEach(() => {
    api.getWard.mockResolvedValue(ward({ ranked: rows.slice(0, 6), abstained: unranked }));
  });

  it("count live, with a tone only on a High or Medium count above 0, and start on All", async () => {
    page();
    await loaded();
    const names = within(screen.getByRole("group", { name: "Filter patients" })).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["All 8", "High 2", "Medium 3", "Rising 0", "Not ranked 2", "Overdue 0"]);
    expect(chip(/^All/).getAttribute("aria-pressed")).toBe("true");
    expect(chip(/^High/).querySelector(".hw-filter__count")!.getAttribute("data-tone")).toBe("emergency");
    expect(chip(/^Medium/).querySelector(".hw-filter__count")!.getAttribute("data-tone")).toBe("urgent");
    expect(chip(/^Rising/).querySelector(".hw-filter__count")!.hasAttribute("data-tone")).toBe(false);
  });

  it("filter the list, hide the strip on a band, show only the strip on Not ranked, and All resets", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    expect(screen.getByRole("heading", { level: 2, name: "Not ranked (2)" })).toBeTruthy();
    await user.click(chip(/^High/));
    expect(chip(/^High/).getAttribute("aria-pressed")).toBe("true");
    expect(chip(/^All/).getAttribute("aria-pressed")).toBe("false");
    expect(within(screen.getByRole("region", { name: "Ranked patients" })).getAllByRole("link", { name: /^[A-Z][a-z]+, / }).map((l) => l.textContent))
      .toEqual(["Santos, Maria", "Reyes, Carlos"]);
    expect(screen.queryByRole("heading", { name: "Not ranked (2)" })).toBeNull();
    await user.click(chip(/^Medium/)); // the single 3 (low-medium) counts as medium
    expect(screen.getAllByRole("link", { name: /^Open / }).map((l) => l.getAttribute("aria-label")))
      .toEqual(["Open Villanueva, Jose", "Open Bautista, Elena", "Open Mendoza, Ana"]);
    await user.click(chip(/^Not ranked/));
    expect(screen.getByRole("heading", { level: 2, name: "Not ranked (2)" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Ranked patients" })).toBeNull();
    expect(screen.queryByText(/This filter only hides rows/)).toBeNull();
    await user.click(chip(/^All/));
    expect(screen.getByRole("region", { name: "Ranked patients" }).querySelectorAll(".hw-ranklist__row")).toHaveLength(6);
    expect(screen.getByRole("heading", { level: 2, name: "Not ranked (2)" })).toBeTruthy();
  });

  it("say so when a filter matches no one, and Show all patients resets", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(chip(/^Rising/));
    expect(screen.getByText("No patient\u2019s NEWS2 is rising")).toBeTruthy();
    expect(screen.getByText("The ranked list is unchanged. This filter only hides rows.")).toBeTruthy();
    await user.click(chip(/^Overdue/));
    expect(screen.getByText("No observations are overdue")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Show all patients" }));
    expect(chip(/^All/).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("region", { name: "Ranked patients" })).toBeTruthy();
  });

  it.each([
    ["High", { ranked: rows.slice(5, 7) }, "No patient is in the high band"],
    ["Medium", { ranked: rows.slice(0, 2) }, "No patient is in the medium band"],
    ["Not ranked", { ranked: rows.slice(0, 2), abstained: [] }, "Every patient is ranked"],
  ])("say so for %s with no match", async (name, over, text) => {
    api.getWard.mockResolvedValue(ward({ abstained: unranked, ...over }));
    page();
    await loaded();
    await userEvent.click(chip(new RegExp(`^${name}`)));
    expect(screen.getByText(text)).toBeTruthy();
  });
});

describe("the NOT RANKED strip", () => {
  it("sits above the ranked list, and folds", async () => {
    const user = userEvent.setup();
    api.getWard.mockResolvedValue(ward({ abstained: unranked }));
    page();
    await loaded();
    const strip = screen.getByRole("region", { name: "Not ranked (2)" });
    expect(strip.compareDocumentPosition(screen.getByRole("region", { name: "Ranked patients" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(strip).getByRole("link", { name: "Flores, Ramon" }).getAttribute("href")).toBe("/patient/p-004");
    await user.click(within(strip).getByRole("button", { name: "Hide" }));
    expect(within(strip).queryByRole("link", { name: "Flores, Ramon" })).toBeNull();
    await user.click(within(strip).getByRole("button", { name: "Show" }));
    expect(within(strip).getByText("Systolic BP missing.")).toBeTruthy();
  });
});

describe("acknowledging an alert", () => {
  const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));

  it("is a state of the row: the row carries data-alert and Acknowledge in place of Open; no separate alert card", async () => {
    page();
    await loaded();
    expect(rowOf("Santos, Maria").hasAttribute("data-alert")).toBe(true);
    expect(ackButton()).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open Santos, Maria" })).toBeNull();
    expect(banner()).toBeNull(); // the old role=alert cards are gone
  });

  it("hides the alert at once, offers Undo, and saves through the API only after 5 seconds", async () => {
    page();
    await loaded();
    await user().click(ackButton()!);
    expect(ackButton()).toBeNull();
    expect(rowOf("Santos, Maria").hasAttribute("data-alert")).toBe(false);
    expect(screen.getByText("Alert acknowledged for Santos, Maria.")).toBeTruthy();
    expect(undoButton()).toBeTruthy();
    await advance(4_900);
    expect(api.acknowledge).not.toHaveBeenCalled();
    await advance(100);
    expect(api.acknowledge).toHaveBeenCalledWith("p-001", 9);
    await waitFor(() => expect(undoButton()).toBeNull());
    await advance(10_000);
    expect(ackButton()).toBeNull();
    expect(document.title).toBe("BioSentry");
  });

  it("Undo within 5 seconds brings the alert back and saves nothing", async () => {
    page();
    await loaded();
    await user().click(ackButton()!);
    await advance(3_000);
    await user().click(undoButton()!);
    expect(ackButton()).toBeTruthy();
    expect(rowOf("Santos, Maria").hasAttribute("data-alert")).toBe(true);
    await advance(10_000);
    expect(api.acknowledge).not.toHaveBeenCalled();
  });

  it("is never done by a click on the row or the name", async () => {
    page();
    await loaded();
    await user().click(rowOf("Santos, Maria"));
    await user().click(screen.getByRole("link", { name: "Santos, Maria" }));
    expect(screen.getByText("at /patient/p-001")).toBeTruthy();
    await advance(10_000);
    expect(api.acknowledge).not.toHaveBeenCalled();
    expect(ackButton()).toBeTruthy();
  });

  it("brings the alert back with a notice when the save fails", async () => {
    api.acknowledge.mockRejectedValue(new Error("down"));
    page();
    await loaded();
    await user().click(ackButton()!);
    await advance(5_000);
    expect(await screen.findByText(/Could not save the acknowledgement/)).toBeTruthy();
    expect(ackButton()).toBeTruthy();
  });

  it("saves at once when the page is hidden, and when the page goes away", async () => {
    page();
    await loaded();
    await user().click(ackButton()!);
    act(() => void window.dispatchEvent(new Event("pagehide")));
    expect(api.acknowledge).toHaveBeenCalledWith("p-001", 9);
    cleanup();
    api.acknowledge.mockClear();
    page();
    await loaded();
    await user().click(ackButton()!);
    cleanup();
    expect(api.acknowledge).toHaveBeenCalledWith("p-001", 9);
  });

  it("does not raise an alert the server says was acknowledged at this score", async () => {
    api.getAcknowledgements.mockResolvedValue([{ patient_id: "p-001", score: 9 }]);
    page();
    await loaded();
    expect(ackButton()).toBeNull();
    expect(screen.getByRole("button", { name: "0 unacknowledged alerts" })).toBeTruthy();
  });

  it("sounds once per new alert and shows the count in the tab title, not again after an Undo", async () => {
    const started = vi.fn();
    vi.stubGlobal("AudioContext", class {
      currentTime = 0;
      destination = {};
      createOscillator() {
        return { frequency: {}, connect() {}, start: started, stop() {}, onended: null };
      }
      close() {}
    });
    page();
    await loaded();
    await waitFor(() => expect(document.title).toBe("(1) BioSentry"));
    expect(started).toHaveBeenCalledTimes(1);
    await user().click(ackButton()!);
    expect(document.title).toBe("BioSentry");
    await user().click(undoButton()!);
    expect(document.title).toBe("(1) BioSentry");
    expect(started).toHaveBeenCalledTimes(1);
  });
});

describe("the bell and its tray", () => {
  const twoHigh = () => api.getWard.mockResolvedValue(ward({ ranked: rows.slice(0, 3) }));
  const bell = () => screen.getByRole("button", { name: /unacknowledged alert/ });
  const tray = () => screen.queryByRole("dialog", { name: "Unacknowledged alerts" });

  it("counts the same alerts as the rows", async () => {
    twoHigh();
    page();
    await loaded();
    expect(bell().getAttribute("aria-label")).toBe("2 unacknowledged alerts");
    expect(document.querySelectorAll("li[data-alert]")).toHaveLength(2);
    await userEvent.click(ackButton("Reyes, Carlos")!);
    expect(bell().getAttribute("aria-label")).toBe("1 unacknowledged alert");
  });

  it("opens with focus on the first Acknowledge, acknowledges from the tray, moves focus on, and closes on Esc", async () => {
    twoHigh();
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(bell());
    const t = tray()!;
    expect(within(t).getByRole("heading", { name: "Unacknowledged alerts on Ward W4" })).toBeTruthy();
    expect(within(t).getByText("Bed 4A · High")).toBeTruthy(); // no time: the ward does not say since when
    const first = within(t).getByRole("button", { name: "Acknowledge the alert for Santos, Maria" });
    expect(document.activeElement).toBe(first);
    await user.click(first);
    expect(tray()).not.toBeNull(); // it stays open
    expect(document.activeElement).toBe(within(t).getByRole("button", { name: "Acknowledge the alert for Reyes, Carlos" }));
    expect(ackButton("Santos, Maria")).toBeNull(); // the row follows: one source
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(within(t).getByRole("button", { name: "Close alerts" }));
    expect(within(t).getByText(/^Nothing is waiting/)).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(tray()).toBeNull();
    expect(document.activeElement).toBe(bell());
  });

  it("keeps Tab inside the tray, opens a patient, and closes from its Close button", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(bell());
    const t = tray()!;
    const close = within(t).getByRole("button", { name: "Close alerts" });
    const ack = within(t).getByRole("button", { name: /^Acknowledge/ });
    await user.tab();
    expect(document.activeElement).toBe(close); // from the last stop back to the first
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(ack);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(within(t).getByRole("button", { name: "Open Santos, Maria" }));
    await user.click(close);
    expect(tray()).toBeNull();
    await user.click(bell());
    await user.click(within(tray()!).getByRole("button", { name: "Open Santos, Maria" }));
    expect(screen.getByText("at /patient/p-001")).toBeTruthy();
    expect(tray()).toBeNull();
  });

  it("is absent for an administrator", async () => {
    page(admin);
    await loaded();
    expect(screen.queryByRole("button", { name: /unacknowledged alert/ })).toBeNull();
  });
});

describe("the patient search", () => {
  it("is focused by /, lists ranked patients in rank order and NOT RANKED last, and Enter opens the first", async () => {
    api.getWard.mockResolvedValue(ward({ ranked: rows.slice(0, 6), abstained: unranked }));
    const user = userEvent.setup();
    page();
    await loaded();
    fireEvent.keyDown(document.body, { key: "/" });
    const box = screen.getByRole("combobox", { name: "Find a patient by name or bed" });
    expect(document.activeElement).toBe(box);
    await user.type(box, "es");
    const options = within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["#2Reyes, Carlos54 M · Bed 2B7 High", "Flores, Ramon73 M · Bed 4BNOT RANKED"]);
    await user.keyboard("{Enter}");
    expect(screen.getByText("at /patient/p-002")).toBeTruthy();
  });
});

describe("density and the table", () => {
  beforeEach(() => {
    api.getWard.mockResolvedValue(ward({ ranked: rows.slice(0, 3), abstained: unranked }));
  });

  it("switches the list to compact rows: the kit drops sparklines and chips, the tile goes small", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    const list = () => screen.getByRole("region", { name: "Ranked patients" });
    expect(list().querySelector("ol")!.getAttribute("data-density")).toBe("comfortable");
    expect(within(list()).getByRole("img", { name: "NEWS2 9, High" }).className).toBe("hw-score");
    await user.click(screen.getByRole("radio", { name: "Compact" }));
    expect(list().querySelector("ol")!.getAttribute("data-density")).toBe("compact");
    expect(within(list()).getByRole("img", { name: "NEWS2 9, High" }).className).toBe("hw-score hw-score--sm");
    expect(screen.getByRole("radio", { name: "Compact" })).toHaveProperty("checked", true);
  });

  it("names each vital for a screen reader, with the points it adds", async () => {
    page();
    await loaded();
    const row = rowOf("Santos, Maria");
    const vitals = within(row).getAllByRole("img").filter((e) => e.classList.contains("hw-ranklist__vital"));
    expect(vitals.map((v) => v.getAttribute("aria-label")?.replace(/ \d+(\/\d+)?/, " N"))).toEqual([
      expect.stringMatching(/^Heart rate N per minute/),
      expect.stringMatching(/^Respiratory rate N per minute/),
      expect.stringMatching(/^SpO2 N percent/),
      expect.stringMatching(/^Blood pressure N mmHg, adds 3 NEWS2 points$/),
      expect.stringMatching(/^ACVPU /),
    ]);
    expect(vitals[3].getAttribute("data-points")).toBe("3");
  });

  it("shows every patient as a table, NOT RANKED first, sortable by rank or bed", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    expect(screen.getByRole("group", { name: "View" })).toBeTruthy();
    await user.click(screen.getByRole("radio", { name: "Table" }));
    expect(screen.queryByRole("region", { name: "Ranked patients" })).toBeNull();
    expect(screen.queryByRole("region", { name: /^Not ranked/ })).toBeNull();
    const table = screen.getByRole("table", { name: "Patients on Ward W4" });
    const beds = () => within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[1].textContent);
    expect(beds()).toEqual(["4B", "3B", "4A", "2B", "1C"]);
    expect(within(table).getByRole("columnheader", { name: "Rank" }).getAttribute("aria-sort")).toBe("ascending");
    await user.click(within(table).getByRole("button", { name: "Bed" }));
    expect(beds()).toEqual(["1C", "2B", "3B", "4A", "4B"]);
    expect(within(table).getByRole("columnheader", { name: "Bed" }).getAttribute("aria-sort")).toBe("ascending");
    expect(within(table).getByRole("columnheader", { name: "Rank" }).hasAttribute("aria-sort")).toBe(false);
    expect(within(table).getAllByText("NOT RANKED")).toHaveLength(2);
    expect(within(table).getByRole("button", { name: "Acknowledge the alert for Santos, Maria" })).toBeTruthy();
    expect(within(table).getAllByText("Call the medical team now (registrar or above)")).toHaveLength(2); // the charge nurse's line, on both high rows
    await user.click(within(table).getByRole("button", { name: "Acknowledge the alert for Santos, Maria" }));
    expect(within(table).queryByRole("button", { name: "Acknowledge the alert for Santos, Maria" })).toBeNull();
    expect(within(table).getByRole("button", { name: "Acknowledge the alert for Reyes, Carlos" })).toBeTruthy();
  });

  it("opens the Record vitals drawer from a NOT RANKED row of the table in an API build", async () => {
    api.build.canRecord = true;
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(screen.getByRole("radio", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "Record vitals for Flores, Ramon" }));
    const dialog = screen.getByRole("dialog", { name: "Record vitals" });
    expect(within(dialog).getByRole("form", { name: "Record vitals for Flores, Ramon" })).toBeTruthy();
    expect(document.querySelector("td[colspan='12']")).toBeNull(); // nothing opens inside the table
  });

  it("records from the table and says where the patient now stands", async () => {
    api.build.canRecord = true;
    api.recordVitals.mockResolvedValue({
      ...rows[0].vitals, patient_id: "p-004", ranked: true, rank: 3, score: 5, not_ranked_reason: null, not_ranked_instruction: null,
    });
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(screen.getByRole("radio", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "Record vitals for Flores, Ramon" }));
    await user.type(screen.getByLabelText("Systolic"), "130");
    await user.type(screen.getByLabelText("Diastolic"), "84");
    await user.click(screen.getByRole("button", { name: "Record" }));
    expect(await screen.findByText("Recorded for Flores, Ramon. Ranked #3 with NEWS2 5.")).toBeTruthy();
    expect(api.getWard).toHaveBeenCalledTimes(2); // the ranking on screen is the server's
  });

  it("closes the drawer from its Close", async () => {
    api.build.canRecord = true;
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(screen.getByRole("radio", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "Record vitals for Flores, Ramon" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows a NOT RANKED patient's stored values in the table", async () => {
    api.getWard.mockResolvedValue(ward({ ranked: [], abstained: [{ ...unranked[0], vitals: rows[0].vitals }] }));
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(screen.getByRole("radio", { name: "Table" }));
    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(5, 10)).not.toContain("--");
  });
});

describe("an empty unit, and the loading skeleton", () => {
  it("says there are no patients and, for the charge nurse, links to admission", async () => {
    api.getWard.mockResolvedValue(ward({ ranked: [], abstained: [] }));
    page();
    await loaded();
    expect(screen.getByText("No patients on Ward W4")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Admit a patient" }).getAttribute("href")).toBe("/admit");
    cleanup();
    page({ ...session, role: "rn" });
    await loaded();
    expect(screen.queryByRole("link", { name: "Admit a patient" })).toBeNull();
  });

  it("shows skeleton rows while the ward loads", () => {
    api.getWard.mockReturnValue(new Promise(() => {}));
    page();
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("Loading ward…");
    expect(status.querySelectorAll(".hw-skeleton").length).toBeGreaterThan(0);
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
    expect(ackButton()).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Could not load your acknowledgements/)).toBeNull();
  });

  it("keeps the last ward, says Offline since, turns ages red, and waits to acknowledge and record", async () => {
    api.build.canRecord = true;
    api.getWard.mockResolvedValue(ward({ abstained: unranked }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    page();
    await loaded();
    expect(document.querySelector("[data-stale]")).toBeNull();
    api.getWard.mockRejectedValue(new Error("down"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    const b = banner()!;
    expect(b.className).toContain("hw-banner--inverse");
    expect(within(b).getByText(/^Offline since \d\d:\d\d/)).toBeTruthy();
    expect(b.textContent).toContain("Acknowledging and recording wait until the connection is back.");
    expect(screen.getByRole("link", { name: "Santos, Maria" })).toBeTruthy(); // the last data stays
    expect(rowOf("Santos, Maria").querySelector("[data-stale]")!.textContent).toMatch(/^\d+s ago$/); // red, not stale yet
    expect((ackButton() as HTMLButtonElement).disabled).toBe(true);
    for (const b of screen.getAllByRole("button", { name: "Record vitals" })) expect((b as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "1 unacknowledged alert" }));
    expect((screen.getByRole("dialog").querySelector("[data-ack]") as HTMLButtonElement).disabled).toBe(true);
  });
});

// What the simulator publishes in `shared`: day 2, 06:20 on the simulated clock.
const live = (shared: unknown) => ({ shared, patients: [], can_control: false });
const published = (over: object = {}) =>
  live({ clockS: 109_200, speed: 10, ended: false, counts: { received: 120, rejected: 3, abstained: 5 }, ...over });

describe("the ward's clock and speed in the bar", () => {
  beforeEach(() => {
    api.build.usesServer = true; // only the API has a simulator
  });
  const clock = () => screen.findByRole("group", { name: "Ward clock" });

  it("says the simulated time and the speed, under a name", async () => {
    api.getLiveWard.mockResolvedValue(published());
    page();
    const el = await clock();
    expect(el.textContent).toBe(`Ward clock4 ${SEP}, 21:20 \u00b7 10x`);
    expect(screen.queryByText(/Simulator:/)).toBeNull(); // the old counts line is gone
  });

  it("says the clock literally for one time and speed", async () => {
    api.getLiveWard.mockResolvedValue(published({ clockS: 109_200, speed: 10 }));
    page();
    const text = (await clock()).textContent;
    expect(text).toMatch(/^Ward clock4 Sept?, 21:20 \u00b7 10x$/); // 06:20 on day 2 of a run that starts 15:00 on 3 Sep
  });

  it.each([
    [{ speed: 60 }, `4 ${SEP}, 21:20 \u00b7 60x`],
    [{ speed: 1 }, `4 ${SEP}, 21:20 \u00b7 1x`],
    [{ speed: 0 }, `4 ${SEP}, 21:20 \u00b7 Paused`],
    [{ ended: true }, `4 ${SEP}, 21:20 \u00b7 Run ended`],
  ])("says the pace for %j", async (over, text) => {
    api.getLiveWard.mockResolvedValue(published(over));
    page();
    expect((await clock()).textContent).toContain(text);
  });

  it.each([
    ["there is no simulator", null],
    ["nothing is published yet", live(null)],
    ["what is published is not the simulator's status", live({ clockS: "soon" })],
  ])("shows no clock when %s", async (_, view) => {
    api.getLiveWard.mockResolvedValue(view);
    page();
    await loaded();
    await waitFor(() => expect(api.getLiveWard).toHaveBeenCalled());
    expect(screen.queryByRole("group", { name: "Ward clock" })).toBeNull();
  });

  it("follows the simulator with the ward, in the same round, and drops the clock when it cannot be read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.getLiveWard
      .mockResolvedValueOnce(published())
      .mockResolvedValueOnce(published({ clockS: 109_260 }))
      .mockRejectedValue(new Error("down"));
    page();
    await screen.findByText(`4 ${SEP}, 21:20 \u00b7 10x`);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await screen.findByText(`4 ${SEP}, 21:21 \u00b7 10x`);
    expect(api.getLiveWard).toHaveBeenCalledTimes(2);
    expect(api.getWard).toHaveBeenCalledTimes(2); // one round: the rows and the status together
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await waitFor(() => expect(screen.queryByRole("group", { name: "Ward clock" })).toBeNull());
  });

  it("is not asked for on the fixture: no call, now or later", async () => {
    api.build.usesServer = false;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    page();
    await loaded();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Ward clock" })).toBeNull();
  });
});

describe("how often the ward follows the run's speed", () => {
  // The clock here moves only when a test moves it: no real time passes into a test, so a slow machine changes nothing.
  // That rules out findBy and waitFor in this block (they wait on real time); `advance(0)` lets what is pending settle.
  beforeEach(() => {
    api.build.usesServer = true;
    vi.useFakeTimers();
  });
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  const setVisibility = (state: "hidden" | "visible") => {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  };
  const start = async (over: object) => {
    api.getLiveWard.mockResolvedValue(published(over));
    page();
    await advance(0);
    expect(screen.getByRole("group", { name: "Ward clock" })).toBeTruthy();
    api.getWard.mockClear();
  };

  it("reads at 2, 4 and 6 seconds at 60x", async () => {
    await start({ speed: 60 });
    await advance(1_500);
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(1_000);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    await advance(2_000);
    await advance(2_000);
    expect(api.getWard).toHaveBeenCalledTimes(3);
  });

  it.each([
    ["10x", { speed: 10 }],
    ["1x", { speed: 1 }],
    ["paused", { speed: 0 }],
    ["ended at 60x", { speed: 60, ended: true }],
  ])("reads at 5 seconds, not 2, at %s", async (_, over) => {
    await start(over);
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(2_700);
    expect(api.getWard).toHaveBeenCalledTimes(1);
  });

  it("switches when the speed changes between rounds, and back", async () => {
    await start({ speed: 60 });
    api.getLiveWard.mockResolvedValue(published({ speed: 1 }));
    await advance(2_100); // this round reads the new speed
    api.getWard.mockClear();
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(2_600);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    api.getLiveWard.mockResolvedValue(published({ speed: 60 }));
    await advance(5_000);
    api.getWard.mockClear();
    await advance(2_100);
    expect(api.getWard).toHaveBeenCalledTimes(1);
  });

  it("reads every 5 seconds in a hidden tab, even at 60x, and every 2 again when it is shown", async () => {
    await start({ speed: 60 });
    await act(async () => setVisibility("hidden"));
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(2_700);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    await act(async () => setVisibility("visible"));
    api.getWard.mockClear();
    await advance(2_100);
    expect(api.getWard).toHaveBeenCalledTimes(1);
  });

  it("starts in a hidden tab at 5 seconds", async () => {
    setVisibility("hidden");
    await start({ speed: 60 });
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
  });

  it("is 5 seconds when the live view cannot be read", async () => {
    api.getLiveWard.mockRejectedValue(new Error("down"));
    page();
    await advance(0);
    expect(screen.getByRole("group", { name: "Filter patients" })).toBeTruthy();
    api.getWard.mockClear();
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
  });

  // The API client fails a read that has not answered in 10 s (api/client.test.ts); the mock does the same.
  const hangsFor = (ms: number) =>
    new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), ms));

  it("keeps one round in flight: a round not yet timed out makes the next ticks do nothing", async () => {
    await start({ speed: 60 });
    api.getWard.mockReturnValue(hangsFor(10_000));
    await advance(2_100);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    await advance(6_000);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    expect(api.getLiveWard).toHaveBeenCalledTimes(2);
  });

  it("a round that hangs until the read timeout fails, says so, and the next tick asks again", async () => {
    await start({ speed: 60 });
    // The read starts at the 2 s tick and times out 10.5 s later, at 12.5 s: half a second after one tick, never on one.
    api.getWard.mockImplementationOnce(() => hangsFor(10_500));
    await advance(2_000);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    await advance(10_400); // the ticks at 4, 6, 8, 10 and 12 s find the round in flight
    expect(api.getWard).toHaveBeenCalledTimes(1);
    expect(banner()).toBeNull();
    await advance(100); // the read times out
    expect(banner()!.textContent).toMatch(/^Offline since /);
    api.getWard.mockClear().mockResolvedValue(ward());
    await advance(4_900); // a failing ward is asked again after 5 s, not 2
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(100);
    expect(api.getWard).toHaveBeenCalledTimes(1);
    expect(banner()).toBeNull();
  });

  it("asks a failing ward every 5 seconds at 60x, and says so", async () => {
    await start({ speed: 60 });
    api.getWard.mockRejectedValue(new Error("down"));
    await advance(2_100); // the round that fails
    expect(screen.getByText(/Retrying every 5 seconds\./)).toBeTruthy();
    api.getWard.mockClear();
    await advance(2_500);
    expect(api.getWard).not.toHaveBeenCalled();
    await advance(2_700);
    expect(api.getWard).toHaveBeenCalledTimes(1);
  });
});

describe("an old answer never replaces a newer one", () => {
  it("drops a slow answer that lands after a later Refresh was answered", async () => {
    api.build.usesServer = true;
    const user = userEvent.setup();
    page(admin);
    await loaded();
    const named = (name: string): WardResponse => ({ ...ward(), ranked: [{ ...rows[0], name }] });
    let late!: (w: WardResponse) => void;
    api.getWard
      .mockReturnValueOnce(new Promise((res) => (late = res)))
      .mockResolvedValueOnce(named("Newer, Pat"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Newer, Pat/)).toBeTruthy();
    await act(async () => late(named("Older, Pat")));
    expect(screen.queryByText(/Older, Pat/)).toBeNull();
    expect(screen.getByText(/Newer, Pat/)).toBeTruthy();
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
    await loaded();
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
  beforeEach(() => {
    api.build.usesServer = true;
    api.build.canRecord = true;
    api.getWard.mockResolvedValue({ ...ward(), abstained: unranked.slice(0, 1) });
  });

  it("shows the list and the read-only line, with no write control, alert, bell or acknowledgement call", async () => {
    page(admin);
    await loaded();
    expect(screen.getByText(/Read-only\. Views are recorded in the audit log\./)).toBeTruthy();
    expect(screen.getByText(/Not ranked \(1\)/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Record/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /unacknowledged alert/ })).toBeNull();
    expect(document.querySelector("[data-alert]")).toBeNull();
    expect(screen.getByRole("link", { name: "Open Santos, Maria" })).toBeTruthy();
    expect(screen.queryByText(/not acknowledged since/)).toBeNull(); // the ward cannot say since when: no time is made up
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.title).toBe("BioSentry");
    expect(api.getAcknowledgements).not.toHaveBeenCalled();
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByText(/Simulator:/)).toBeNull();
  });

  it("has no Acknowledge or Record vitals in the table either", async () => {
    page(admin);
    await loaded();
    await userEvent.click(screen.getByRole("radio", { name: "Table" }));
    expect(screen.queryByRole("button", { name: /^Acknowledge|^Record/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Admit a patient" })).toBeNull();
  });

  it("asks once, never on a timer, and again only on Refresh", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page(admin);
    await loaded();
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
    await loaded();
    api.getWard.mockRejectedValue(new Error("down"));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText(/Use Refresh to try again\./)).toBeTruthy();
    expect(banner()!.textContent).not.toContain("Acknowledging"); // an admin does neither
  });

  it("leaves a clinician's page as it was: Record is offered", async () => {
    page();
    await screen.findByText(/Not ranked \(1\)/);
    expect(screen.getAllByRole("button", { name: "Record vitals" }).length).toBeGreaterThan(0);
  });
});

describe("flip on change", () => {
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  // The page's own flips; the bar's (the closed tray's tiles, the clock) are counted where a test asks for them.
  const flips = () => [...document.querySelectorAll('main [class*="_flip_"], [role="group"][aria-label="Ward clock"] [class*="_flip_"]')].map((e) => e.textContent);
  const withScore = (score: number): WardResponse => ({ ...ward(), ranked: [{ ...rows[0], score }] });
  beforeEach(() => {
    api.build.usesServer = true;
    api.getLiveWard.mockResolvedValue(published({ speed: 60 }));
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  it("flips nothing on first load, and nothing on a poll that changed nothing", async () => {
    page();
    await screen.findByRole("group", { name: "Ward clock" });
    expect(flips()).toEqual([]);
    api.getWard.mockResolvedValue(ward()); // a new object, the same figures
    await advance(2_100);
    expect(api.getWard).toHaveBeenCalledTimes(2);
    expect(flips()).toEqual([]);
  });

  it("flips only the score of a row whose score changed", async () => {
    page();
    await screen.findByRole("group", { name: "Ward clock" });
    api.getWard.mockResolvedValue(withScore(8));
    await advance(2_100);
    expect(flips()).toEqual(["8"]);
    expect(screen.getByRole("img", { name: "NEWS2 8, High" }).querySelector(".hw-score__value")!.textContent).toBe("8");
  });

  it("flips a count at the top when it changes", async () => {
    page();
    await screen.findByRole("group", { name: "Ward clock" });
    api.getWard.mockResolvedValue(withScore(3));
    await advance(2_100);
    expect(flips()).toContain("0"); // high 1 -> 0
    expect(flips()).toContain("3");
  });

  it("flips the clock's time when the minute changes", async () => {
    page();
    const el = await screen.findByRole("group", { name: "Ward clock" });
    api.getLiveWard.mockResolvedValue(published({ speed: 60, clockS: 109_260 }));
    await advance(2_100);
    expect(flips()).toEqual([`4 ${SEP}, 21:21 · 60x`]);
    expect(el.textContent).toBe(`Ward clock4 ${SEP}, 21:21 · 60x`);
  });
});

describe("every age and due time follows the ward's time as served", () => {
  // A patient below the high band, so observations have a due time. The browser says 14 Nov 2026.
  const low = rows.find((r) => r.score < 7)!;
  const due = obsDue(low) as number;
  const BROWSER = Date.parse("2026-11-14T09:00:00Z");
  const wardAt = (now: number): WardResponse => ({ ...ward(), ranked: [low], now: new Date(now).toISOString() });

  it("says Obs due HH:MM, not overdue by hundreds of hours, when the ward's time is before the due time", async () => {
    vi.useFakeTimers({ now: BROWSER, toFake: ["Date"] });
    api.getWard.mockResolvedValue(wardAt(due - 30 * 60_000));
    page();
    expect(await screen.findByText(`Obs due ${formatClock(due)}`)).toBeTruthy();
    expect(screen.queryByText(/Obs overdue \d/)).toBeNull();
    expect(chip(/^Overdue/).textContent).toBe("Overdue 0");
  });

  it("stamps the update and the outage with the ward's time of the last read, not the browser's", async () => {
    vi.useFakeTimers({ now: BROWSER });
    api.build.usesServer = true;
    const served = due - 30 * 60_000;
    api.getWard.mockResolvedValue(wardAt(served));
    page();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(`Updated ${formatClock(served)}`)).toBeTruthy();
    api.getWard.mockRejectedValue(new Error("down"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(within(screen.getByRole("banner")).getByText(`Offline since ${formatClock(served)}`)).toBeTruthy(); // the bar's status
    expect(within(banner()!).getByText(`Offline since ${formatClock(served)}`)).toBeTruthy(); // and the black banner
  });

  it("goes overdue when a read brings the ward's time past the due time, and stands there while reads fail", async () => {
    vi.useFakeTimers({ now: BROWSER });
    api.build.usesServer = true; // a server's ward time is its own; only the no-server demo keeps the browser's pace
    api.getWard.mockResolvedValue(wardAt(due - 30 * 60_000));
    page();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(`Obs due ${formatClock(due)}`)).toBeTruthy();
    api.getWard.mockResolvedValue(wardAt(due + 60_000)); // the ward's clock moved on 31 minutes: only a read says so
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(screen.getByText(/^Obs overdue 1 min$/)).toBeTruthy();
    api.getWard.mockRejectedValue(new Error("down"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31 * 60_000); // the browser's minutes do not age the ward
    });
    expect(screen.getByText(/^Obs overdue 1 min$/)).toBeTruthy();
    expect(banner()!.textContent).toMatch(/^Offline since /);
    expect(chip(/^Overdue/).textContent).toBe("Overdue 1");
  });
});
