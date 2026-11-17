// @vitest-environment jsdom
/**
 * The patient page: the score and why, the role's next step,
 * labs with their freshness, notes (append-only, a failed save keeps the text), the trend
 * charts, and the not-found / load-error states.
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import type { AbstainedDetail, BaselineComputed, BaselineResponse, ClinicalNote, LabPanel, PatientDetail as Detail, ScoreRow, Session, SessionRole, WardResponse } from "../types";
import { trend } from "hw-ui";
import { ChatBubble } from "../components/ChatBubble";
import { formatClock, formatClockSeconds } from "../components/format";
import { WardFeedContext, type WardFeed } from "../components/wardFeed";
import { WardFeedProvider } from "../components/WardFeedProvider";
import { PatientDetail } from "./PatientDetail";

// "Sep" or "Sept", whichever the runtime's en-GB gives: the clock uses the same call as the simulator.
const SEP = new Date(Date.UTC(2026, 8, 3)).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getAssistantHistory: vi.fn(async () => []),
  getPatientRuns: vi.fn(async () => []),
  getPatientRun: vi.fn(),
  getPatient: vi.fn(),
  getWard: vi.fn(),
  getAcknowledgements: vi.fn(),
  acknowledge: vi.fn(),
  getNotes: vi.fn(),
  getBaseline: vi.fn(),
  getLiveWard: vi.fn(),
  usesServer: false,
  addNote: vi.fn(),
  canRecordVitals: false,
  recordVitals: vi.fn(),
  hasHistory: false,
  getPatientHistory: vi.fn(),
  hasPatientQuestions: false,
  askPatient: vi.fn(),
  getPatientQuestions: vi.fn(),
  getPatientQuestion: vi.fn(),
}));
vi.mock("../api", () => api);
// The kit's own graph, watched: the tests read the config each trend was drawn with.
vi.mock("hw-ui", async (importOriginal) => {
  const real = await importOriginal<typeof import("hw-ui")>();
  return { ...real, trend: vi.fn(real.trend) };
});

// p-001 from the committed ward: NEWS2 9 (high), two reasons.
const row = synthetic.ranked[0] as unknown as Detail;
const base: Detail = {
  ...row,
  ...(synthetic.details as unknown as Record<string, Pick<Detail, "vital_history" | "tier_breakdown">>)[
    "p-001"
  ],
  now: "2026-09-03T07:00:00Z", // ward time as served
};
const potassium = row.labs.potassium!;
const LAB_KEYS = [
  "troponin", "ck_mb", "bnp", "nt_probnp", "sodium", "potassium", "magnesium", "creatinine", "bun",
  "hemoglobin", "wbc", "platelet_count", "lactate", "sgpt", "sgot", "blood_glucose", "inr",
] as const;
const LAB_LABELS = [
  "Troponin", "CK-MB", "BNP", "NT-proBNP", "Sodium", "Potassium", "Magnesium", "Creatinine", "BUN",
  "Hemoglobin", "WBC", "Platelets", "Lactate", "SGPT", "SGOT", "Blood glucose", "INR",
];
const everyLab = Object.fromEntries(LAB_KEYS.map((k) => [k, potassium])) as unknown as LabPanel;
const noLabs = { ...Object.fromEntries(LAB_KEYS.map((k) => [k, null])), freshness_hours: null } as LabPanel;

const note = (id: string, content: string): ClinicalNote => ({
  id,
  patient_id: "p-001",
  author: "rn-manalo",
  role: "RN",
  timestamp: "2026-09-03T06:45:00Z",
  content,
  synthetic: true,
});

const baseline: BaselineResponse = {
  patient_id: "p-001",
  synthetic: true,
  admission_known: true,
  label: "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger.",
  params: { window_bins: 24, k: 2, p: 3 },
  vitals: [
    {
      vital: "heart_rate", status: "computed", baseline_median: 70, latest: 90, latest_at: "2026-09-03T06:45:00Z",
      latest_age_s: 60, anchored_at: "admission", baseline_from: "2026-09-03T00:45:00Z", delta: 20, z: 4.1, band_low: 58.1, band_high: 81.9, run_bins: 4, since: "2026-09-03T06:00:00Z",
      significant: true, direction: "rising", pathway: "circulatory", unit: "bpm",
    },
  ],
};

const session = (role: SessionRole = "rn"): Session => ({ clinician_id: "rn-manalo", ward: "W4", role });
const page = (role?: SessionRole, path = "/patient/p-001") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/patient/:id" element={<PatientDetail session={session(role)} onSignOut={() => undefined} />} />
        <Route path="*" element={<PatientDetail session={session(role)} onSignOut={() => undefined} />} />
      </Routes>
    </MemoryRouter>,
  );
const loaded = () => screen.findByRole("heading", { level: 1 });

beforeEach(() => {
  api.getPatient.mockResolvedValue(base);
  api.getNotes.mockResolvedValue([]);
  api.getBaseline.mockResolvedValue(null); // fixture mode
  api.getLiveWard.mockResolvedValue(null);
  api.usesServer = false;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const expectLabEntry = () => {
  expect(screen.queryByRole("link", { name: "Simulation lab" })).toBeNull(); // in the menu, not the bar
  expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
};

describe("the header clock is ward time, as served", () => {
  const clock = formatClockSeconds;
  const SERVED = Date.parse("2026-09-03T07:00:00Z");

  afterEach(() => void vi.useRealTimers());

  it("shows the server's time while the browser says another day, and does not step with the browser's seconds", async () => {
    api.usesServer = true; // the page reads on a timer
    vi.useFakeTimers({ now: Date.parse("2026-11-14T21:30:00Z") });
    page("rn");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(clock(SERVED))).toBeTruthy();
    api.getPatient.mockRejectedValue(new Error("down")); // the next read fails: the ward's clock stands at the last read
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(screen.getByText(clock(SERVED))).toBeTruthy();
    expect(screen.getByText(/^Offline since \d\d:\d\d/)).toBeTruthy();
  });

  it("shows a nurse's entry time without its date when it is the ward's today", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-11-14T21:30:00Z"), toFake: ["Date"] });
    const hr = base.vitals.heart_rate;
    api.getPatient.mockResolvedValue({
      ...base,
      vitals: { ...base.vitals, heart_rate: { ...hr, provenance: { ...hr.provenance, source: "nurse", observed_at: "2026-09-03T06:45:00Z" } } },
    });
    page("rn");
    await loaded();
    const at = formatClock(Date.parse("2026-09-03T06:45:00Z")); // the ward's clock
    expect(screen.getAllByText(`Entered by a nurse at ${at}`).length).toBeGreaterThan(0);
  });
});

describe("PatientDetail", () => {
  it("says the patients are simulated, once, and has the top bar and the account", async () => {
    page("rn");
    await loaded();
    expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
    expect(within(screen.getByRole("banner")).queryByText("Ward W4")).toBeNull(); // the breadcrumb names the ward, so the bar does not repeat it
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Ward W4" }).getAttribute("href")).toBe("/ward");
    expect(screen.getByRole("button", { name: /rn-manalo/ })).toBeTruthy();
  });

  it("shows the score, why the patient is up, and the nurse's next step", async () => {
    page("rn");
    expect((await loaded()).textContent).toBe(row.name);
    expect(api.getPatient).toHaveBeenCalledWith("p-001");
    expect(screen.getAllByText("9").length).toBeGreaterThan(0);
    expect(screen.getByText("Total 9 (≥7): high")).toBeTruthy();
    expect(screen.getByText("Single parameter at 3: systolic BP")).toBeTruthy();
    expect(screen.getByText("Call the medical team now (registrar or above)")).toBeTruthy();
    expect(screen.getByText(`Bed ${row.bed}`, { exact: false })).toBeTruthy();
    expect(screen.getByText("Collected 1 h ago")).toBeTruthy();
  });

  it("gives a physician the assessment due instead", async () => {
    page("md");
    await loaded();
    expect(screen.getByText("Emergency assessment; consider critical care")).toBeTruthy();
  });

  it("shows no seeded Tier B finding and no Tier C block, even when the data carries them", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      tier_c: { risk_probability: 0.23, forecast_window: "next 1-4h", scored_seconds_ago: 420, attributions: [] },
    } as unknown as Detail);
    page();
    await loaded();
    for (const label of ["Arrhythmia burden", "Perfusion index", "HRV (SDNN)", "Troponin trend", "KDIGO stage", "Device axes", "Lab substrate"]) {
      expect(screen.queryByText(label), label).toBeNull();
    }
    for (const text of ["+44% vs baseline", "AF episodes over 6h window", /Tier C/, /risk_probability|23%/]) {
      expect(screen.queryByText(text), String(text)).toBeNull();
    }
    expect(screen.getByText("Own baseline: not shown in this version.")).toBeTruthy();
  });

  it("says no lab result yet only when every lab is missing", async () => {
    api.getPatient.mockResolvedValue({ ...base, labs: { ...noLabs, sodium: { ...everyLab.sodium! } } });
    page();
    await loaded();
    expect(screen.getByText("Sodium")).toBeTruthy();
    expect(screen.queryByText("No lab result yet.")).toBeNull();
  });

  it("says no lab result yet when only a lab with no row is set, and keeps it hidden for an absent measurement", async () => {
    api.getPatient.mockResolvedValue({ ...base, labs: { ...noLabs, calcium: potassium } });
    page();
    await loaded();
    expect(screen.getByText("No lab result yet.")).toBeTruthy();
    cleanup();
    api.getPatient.mockResolvedValue({ ...base, labs: { ...noLabs, sodium: { ...potassium, data: { present: false } } } as unknown as LabPanel });
    page();
    await loaded();
    expect(screen.queryByText("No lab result yet.")).toBeNull();
  });

  it("lists every recorded lab", async () => {
    api.getPatient.mockResolvedValue({ ...base, labs: { ...everyLab, freshness_hours: 2 } });
    page();
    await loaded();
    const labs = screen.getByRole("region", { name: "Labs" });
    for (const label of LAB_LABELS) expect(within(labs).getByText(label)).toBeTruthy();
    expect(within(labs).getByText("Collected 2 h ago")).toBeTruthy();
    expect(screen.queryByText("No lab result yet.")).toBeNull();
    cleanup();
    api.getPatient.mockResolvedValue({ ...base, labs: { ...everyLab, freshness_hours: 0.5 } });
    page();
    await loaded();
    expect(screen.getByText("Collected 30 min ago")).toBeTruthy();
  });

  it("says plainly what is not there: no reasons, no labs, no age or sex", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      score: 0,
      escalation_reasons: [],
      labs: noLabs,
      age: null,
      sex: null,
      signal_quality: 80,
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Sorted on NEWS2 only")).toBeTruthy();
    // Band low: no response, a dash, and when the observations are due (as the ward row says it).
    const step = within(screen.getByRole("region", { name: "Decision" })).getByText("Next step").parentElement!;
    expect(step.textContent).toMatch(/^Next step—Obs (due|overdue)/);
    expect(screen.getByText("Own baseline: not shown in this version.")).toBeTruthy(); // never an empty box
    expect(screen.getByText("No lab result yet.")).toBeTruthy();
    expect(screen.getByText(/^-- -- · Bed/)).toBeTruthy();
    expect(screen.queryByText(/^Collected/)).toBeNull();
    expect(screen.getByText("Signal 80%")).toBeTruthy(); // the strip
    expect(screen.getByText("80%")).toBeTruthy(); // the summary block
  });

  it("shows the own-baseline panel in the Own baseline panel, loaded once with the patient", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const region = await screen.findByRole("region", { name: "Change from own baseline" });
    expect(api.getBaseline).toHaveBeenCalledOnce();
    expect(api.getBaseline).toHaveBeenCalledWith("p-001");
    expect(within(region).getByText(/Heart rate/).closest("li")!.textContent).toContain("from own baseline");
    const tierB = region.parentElement!;
    expect(within(tierB).getByText("Against own baseline")).toBeTruthy();
    expect(tierB.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_CONTAINED_BY).toBeTruthy();
    // The band as a graph above the list: heart rate 90 against its own 58.1 to 81.9.
    const graph = within(tierB).getByRole("figure", { name: "1 of 1 vital is outside its own band" });
    expect(graph.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Own baseline: getting ready.")).toBeNull();
  });

  it("shows the panel after the placeholder", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    expect(await screen.findByRole("region", { name: "Change from own baseline" })).toBeTruthy();
    expect(screen.getByText("Against own baseline")).toBeTruthy();
  });

  it("says it is loading the baseline, and never shows another patient's", async () => {
    api.getBaseline.mockReturnValue(new Promise(() => undefined));
    page();
    await loaded();
    expect(screen.getByText("Own baseline: getting ready.")).toBeTruthy();
    cleanup();
    api.getBaseline.mockResolvedValue({ ...baseline, patient_id: "p-002" });
    page();
    expect(await screen.findByText("Baseline could not be loaded.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Change from own baseline" })).toBeNull();
  });

  it("still renders the page, with one quiet line, when the baseline cannot be loaded", async () => {
    api.getBaseline.mockRejectedValue(new Error("404"));
    page();
    expect((await loaded()).textContent).toBe(row.name);
    expect(await screen.findByText("Baseline could not be loaded.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Change from own baseline" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull(); // quiet, not an alarm
    expect(screen.getByText("Against own baseline")).toBeTruthy();
  });

  it("shows unavailable for a patient that is missing or not yours", async () => {
    api.getPatient.mockResolvedValue(null);
    page();
    expect(await screen.findByText("This patient is unavailable.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
    expectLabEntry();
  });

  it("renders a not-ranked patient's reason, instruction, location and stored history", async () => {
    const row = synthetic.abstained[0];
    api.getPatient.mockResolvedValue({
      ...row,
      unit: "W4",
      floor: 4,
      now: "2026-09-03T07:00:00Z",
      vital_history: { heart_rate: [], respiratory_rate: [], spo2: [] },
    } as AbstainedDetail);
    page("rn", `/patient/${row.id}`);
    expect((await loaded()).textContent).toBe(row.name);
    expect(screen.getByText("NOT RANKED")).toBeTruthy();
    expect(screen.getByText(row.reason)).toBeTruthy();
    expect(screen.getByText(row.instruction)).toBeTruthy();
    expect(screen.getByText(/W4 · Floor 4 · Bed/)).toBeTruthy();
    expect(screen.getByText("No stored history available.")).toBeTruthy();
    expect(screen.queryByText("Patient not found.")).toBeNull();
  });

  it("draws a not-ranked patient's stored history where there is some", async () => {
    const row = synthetic.abstained[0];
    const hr = [{ time: "2026-09-03T06:00:00Z", value: 80 }, { time: "2026-09-03T06:30:00Z", value: 84 }];
    api.getPatient.mockResolvedValue({
      ...row, unit: null, floor: null, now: "2026-09-03T07:00:00Z",
      vital_history: { heart_rate: hr, respiratory_rate: [], spo2: [] },
    } as unknown as AbstainedDetail);
    page("rn", `/patient/${row.id}`);
    await loaded();
    expect(screen.getByRole("heading", { name: /^Heart rate / })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /^SpO2 / })).toBeNull();
    expect(screen.queryByText("No stored history available.")).toBeNull();
    expect(screen.getByText(/Unit unavailable · Floor unavailable · Bed/)).toBeTruthy();
  });

  it("shows an error when the patient cannot be loaded", async () => {
    api.getPatient.mockRejectedValue(new Error("offline"));
    page();
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
    expectLabEntry();
  });

  it("shows the lab entry and the quiet line while the patient loads", () => {
    api.getPatient.mockReturnValue(new Promise(() => {}));
    page();
    expectLabEntry();
  });

  it("asks for nothing without a patient id", () => {
    const { container } = page(undefined, "/elsewhere");
    expect(api.getPatient).not.toHaveBeenCalled();
    expect(api.getBaseline).not.toHaveBeenCalled();
    expectLabEntry();
    expect(container.querySelector("h1")).toBeNull();
  });

  it("opens the notes drawer with the patient's notes and adds one as the signed-in clinician", async () => {
    const user = userEvent.setup();
    api.getNotes.mockResolvedValue([note("n-001", "BP trending down")]);
    api.addNote.mockResolvedValue(note("n-002", "Repeat lactate sent"));
    page("charge_rn");
    await loaded();
    await user.click(await screen.findByRole("button", { name: "All notes (1)" }));
    expect(screen.getByText("Clinical notes · 1")).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getByText("BP trending down")).toBeTruthy();

    const add = screen.getByRole("button", { name: "Add note" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    await user.type(screen.getByPlaceholderText("Add a clinical note..."), "  Repeat lactate sent  ");
    await user.click(add);
    expect(api.addNote).toHaveBeenCalledWith("p-001", "rn-manalo", "Charge RN", "Repeat lactate sent");
    expect(await within(screen.getByRole("dialog")).findByText("Repeat lactate sent")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Latest note" })).getByText("Repeat lactate sent")).toBeTruthy(); // the newest is on the page too
    expect(screen.getByText("Clinical notes · 2")).toBeTruthy();
    expect((screen.getByPlaceholderText("Add a clinical note...") as HTMLTextAreaElement).value).toBe("");
  });

  it("saves a note with Ctrl+Enter, but not with Enter alone", async () => {
    const user = userEvent.setup();
    api.addNote.mockResolvedValue(note("n-003", "Seen by MD"));
    page("md");
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    expect(screen.getByText("Clinical notes")).toBeTruthy();
    const box = screen.getByPlaceholderText("Add a clinical note...");
    await user.type(box, "Seen by MD{Enter}");
    expect(api.addNote).not.toHaveBeenCalled();
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(api.addNote).toHaveBeenCalledWith("p-001", "rn-manalo", "MD", "Seen by MD");
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByText("Seen by MD")).toBeTruthy());
  });

  it("keeps the text and says so when a note is not saved", async () => {
    const user = userEvent.setup();
    api.addNote.mockRejectedValue(new Error("500"));
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    const box = screen.getByPlaceholderText("Add a clinical note...") as HTMLTextAreaElement;
    await user.type(box, "Family updated");
    await user.click(screen.getByRole("button", { name: "Add note" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The note was not saved. Your text is kept; try again.",
    );
    expect(box.value).toBe("Family updated");
  });

  it("shows when notes could not be loaded", async () => {
    const user = userEvent.setup();
    api.getNotes.mockRejectedValue(new Error("offline"));
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Notes could not be loaded.");
  });

  it("closes the notes drawer by its close button, Esc or the backdrop, not by a click inside", async () => {
    const user = userEvent.setup();
    page();
    await loaded();
    const all = screen.getByRole("button", { name: "All notes" });
    expect(all.getAttribute("aria-haspopup")).toBe("dialog");

    await user.click(all);
    await user.click(screen.getByRole("button", { name: "Close notes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(all);
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true })); // Esc
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(all);
    await user.click(screen.getByPlaceholderText("Add a clinical note...")); // inside: stays open
    expect(screen.getByRole("dialog", { name: "Clinical notes" })).toBeTruthy();
    await user.click(screen.getByRole("dialog")); // the backdrop is the dialog itself
    await waitFor(() => expect(screen.queryByPlaceholderText("Add a clinical note...")).toBeNull());
  });

  it("shows the latest note on the page, links Notes to it, and keeps the full list for All notes", async () => {
    const user = userEvent.setup();
    api.getNotes.mockResolvedValue([
      { ...note("n-001", "Older note"), timestamp: "2026-09-03T05:00:00Z" },
      note("n-002", "BP trending down"),
    ]);
    page();
    await loaded();
    const card = await screen.findByRole("region", { name: "Latest note" });
    await within(card).findByText("BP trending down"); // the newest by its time, wherever it sits in the list
    expect(within(card).queryByText("Older note")).toBeNull();
    expect(within(card).getByText("rn-manalo")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Notes (2)" }).getAttribute("href")).toBe("#notes");
    expect(card.id).toBe("notes");
    expect(screen.queryByRole("button", { name: "Charts" })).toBeNull(); // the charts drawer is gone: the trends are on the page
    await user.click(within(card).getByRole("button", { name: "All notes (2)" }));
    const drawer = screen.getByRole("dialog", { name: "Clinical notes · 2" });
    expect(within(drawer).getByText("Older note")).toBeTruthy();
    expect(within(drawer).getByText("BP trending down")).toBeTruthy();
  });

  it("says there are no notes yet, and nothing when they could not be loaded", async () => {
    page();
    await loaded();
    const card = screen.getByRole("region", { name: "Latest note" });
    expect(await within(card).findByText("No notes yet for this patient.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Notes" })).toBeTruthy();
    cleanup();
    api.getNotes.mockRejectedValue(new Error("down"));
    page();
    await loaded();
    await waitFor(() => expect(within(screen.getByRole("region", { name: "Latest note" })).queryByText(/No notes yet/)).toBeNull());
  });

  it("says there is no patch signal, and who entered the values, when the row has none", async () => {
    const hr = base.vitals.heart_rate;
    api.getPatient.mockResolvedValue({
      ...base,
      signal_quality: null,
      last_update_s: null,
      vitals: { ...base.vitals, heart_rate: { ...hr, provenance: { ...hr.provenance, source: "nurse", observed_at: "2026-09-03T06:45:00Z" } } },
    });
    page();
    await loaded();
    expect(screen.getByText("No patch signal")).toBeTruthy();
    expect(screen.getByText("No patch").closest("[data-signal]")?.getAttribute("data-signal")).toBe("none");
    expect(screen.getAllByText(/^Entered by a nurse at /).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Signal \d+%/)).toBeNull();
  });

  it("offers no vitals form when the build cannot record", async () => {
    page();
    await loaded();
    expect(screen.queryByRole("button", { name: "Record vitals" })).toBeNull();
  });

  describe("recording vitals from the page", () => {
    const recorded = (ranked: boolean) => ({
      patient_id: "p-001", blood_pressure: null, consciousness: null, supplemental_o2: null, spo2_scale: null,
      heart_rate: null, respiratory_rate: null, spo2: null, temperature: null,
      ranked, rank: ranked ? 1 : null, score: ranked ? 9 : null,
      not_ranked_reason: ranked ? null : "No device data", not_ranked_instruction: ranked ? null : "Apply the patch",
    });
    const open = (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: "Record vitals" }));
    const save = async (user: ReturnType<typeof userEvent.setup>, hr = "118") => {
      await user.type(screen.getByLabelText("Heart rate (bpm)"), hr);
      await user.click(screen.getByRole("button", { name: "Record" }));
    };
    const close = async (user: ReturnType<typeof userEvent.setup>) => {
      await user.click(screen.getByRole("button", { name: "Close" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    };
    beforeEach(() => { api.canRecordVitals = true; });
    afterEach(() => { api.canRecordVitals = false; });

    it("opens one drawer from the header: who, errors only after Record, the saving state, and the ranked result", async () => {
      const user = userEvent.setup();
      let answer!: (r: ReturnType<typeof recorded>) => void;
      api.recordVitals.mockReturnValue(new Promise((res) => (answer = res)));
      page();
      await loaded();
      const button = screen.getByRole("button", { name: "Record vitals" });
      expect(button.getAttribute("aria-haspopup")).toBe("dialog");
      await open(user);
      const dialog = within(screen.getByRole("dialog", { name: "Record vitals" }));
      expect(dialog.getByText(`${row.name} · ${row.age} ${row.sex} · Bed ${row.bed}`)).toBeTruthy();
      expect(dialog.queryByText("NOT RANKED")).toBeNull(); // a ranked patient has no reason line
      expect(dialog.queryByRole("alert")).toBeNull(); // nothing is wrong before Record
      await user.type(dialog.getByLabelText("Heart rate (bpm)"), "400");
      expect(dialog.queryByRole("alert")).toBeNull();
      await user.click(dialog.getByRole("button", { name: "Record" }));
      expect(dialog.getByText("Heart rate must be 1 to 300.")).toBeTruthy(); // under its field
      expect(api.recordVitals).not.toHaveBeenCalled();
      await user.clear(dialog.getByLabelText("Heart rate (bpm)"));
      await save(user);
      expect((dialog.getByRole("button", { name: "Recording…" }) as HTMLButtonElement).disabled).toBe(true);
      await act(async () => answer(recorded(true)));
      expect(dialog.getByRole("status").textContent).toBe("Ranked #1 with NEWS2 9.");
      expect(dialog.getByText("Call the medical team now (registrar or above)", { selector: "strong" })).toBeTruthy();
      expect(dialog.queryByRole("link", { name: "Open patient" })).toBeNull(); // this is the patient
    });

    it("reads the patient again when the result is ranked", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(true));
      page();
      await loaded();
      await open(user);
      await save(user);
      await waitFor(() => expect(api.getPatient).toHaveBeenCalledTimes(2));
      // The panel reads again at once: a typed value can change which vital counts.
      await waitFor(() => expect(api.getBaseline).toHaveBeenCalledTimes(2));
      api.getPatient.mockResolvedValue({ ...base, score: 4, escalation_reasons: [] });
      await close(user);
      await open(user);
      await save(user, "120");
      await waitFor(() => expect(api.getPatient).toHaveBeenCalledTimes(3));
      expect(await screen.findByText("Sorted on NEWS2 only")).toBeTruthy();
      expect(screen.queryByRole("link", { name: "Back to ward" })).toBeNull();
    });

    it("shows the result and a link to the ward, and does not read again, when NOT RANKED", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(false));
      page();
      await loaded();
      await open(user);
      await save(user);
      expect(await screen.findByText(/Recorded. Still not ranked. No device data./)).toBeTruthy();
      expect(within(screen.getByRole("dialog")).getByRole("status").textContent).toBe("Still not ranked. No device data. Apply the patch.");
      expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
      expect(api.getPatient).toHaveBeenCalledTimes(2);
    });

    it("says so when the patient cannot be read again", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(true));
      page();
      await loaded();
      await open(user);
      await save(user);
      api.getPatient.mockRejectedValue(new Error("down"));
      await user.click(await screen.findByRole("button", { name: "Close" }));
      await open(user);
      await save(user, "120");
      expect(await screen.findByText("Recorded. The page could not refresh; reload to see the new score.")).toBeTruthy();
      expect(screen.getByText(/Ranked #1 with NEWS2 9\./)).toBeTruthy();
    });

    it("clears the not ranked note on a later ranked save, and labels the old score meanwhile", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(false));
      page();
      await loaded();
      await open(user);
      await save(user);
      const label = "No longer ranked. The figures below are from before this entry.";
      expect(await screen.findByText(label)).toBeTruthy();
      api.recordVitals.mockResolvedValue(recorded(true));
      await user.click(screen.getByRole("button", { name: "Add the missing values" }));
      await save(user, "120");
      await waitFor(() => expect(screen.queryByText(/Still not ranked/)).toBeNull());
      expect(screen.queryByText(label)).toBeNull();
    });

    it("closes the drawer", async () => {
      const user = userEvent.setup();
      page();
      await loaded();
      await open(user);
      await close(user);
      expect(screen.queryByLabelText("Heart rate (bpm)")).toBeNull();
    });
  });
});

describe("PatientDetail own-baseline reason and patch display", () => {
  const drift = { rule: "tier_b_finding", text: "Drifting from own baseline: heart rate, 3 h (synthetic, not validated)", pathway: "circulatory" };
  const lowWithDrift = (role: SessionRole, score: number) => {
    api.getPatient.mockResolvedValue({ ...base, score, escalation_reasons: [drift] } as Detail);
    page(role);
    return loaded();
  };

  it("says the NEWS2 response for a low patient with a drift reason, by role and score", async () => {
    await lowWithDrift("rn", 2);
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: nurse assessment")).toBeTruthy();
    cleanup();
    await lowWithDrift("charge_rn", 0);
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: routine monitoring")).toBeTruthy();
    cleanup();
    await lowWithDrift("md", 0);
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: no medical review triggered")).toBeTruthy();
  });

  it("says nothing of the kind for a low patient with no drift reason", async () => {
    api.getPatient.mockResolvedValue({ ...base, score: 2, escalation_reasons: [] } as Detail);
    page();
    await loaded();
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
  });

  it("shows the signal figure when two values are from the device and two from a nurse", async () => {
    const v = base.vitals;
    const nurse = (m: Detail["vitals"]["heart_rate"]) => ({ ...m, provenance: { ...m.provenance, source: "nurse" as const } });
    const device = (m: Detail["vitals"]["heart_rate"]) => ({ ...m, provenance: { ...m.provenance, source: "device" as const } });
    api.getPatient.mockResolvedValue({
      ...base,
      signal_quality: 40,
      vitals: { ...v, heart_rate: device(v.heart_rate), respiratory_rate: device(v.respiratory_rate), spo2: nurse(v.spo2), temperature: nurse(v.temperature) },
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Signal 40%")).toBeTruthy();
    expect(screen.getByText("40%").closest("[data-signal]")?.getAttribute("data-signal")).toBe("degraded");
  });

  it("lists the drift reason with its pathway under why the patient is up", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      escalation_reasons: [{ rule: "tier_b_finding", text: "Drifting from own baseline: heart rate, 3 h (synthetic, not validated)", pathway: "circulatory" }],
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Drifting from own baseline: heart rate, 3 h (synthetic, not validated)")).toBeTruthy();
    expect(screen.getByText("(circulatory)")).toBeTruthy();
  });

  it("lists the model reason apart, as research with the rank unchanged, and adds no response line", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      score: 2,
      escalation_reasons: [
        { rule: "prediction_model_flag", text: "Prediction model flag (synthetic, not validated)", pathway: null },
      ],
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Prediction model flag (synthetic, not validated)")).toBeTruthy();
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
    // Only model lines: the patient is where NEWS2 puts them, and the page still says so.
    expect(screen.getByText("Sorted on NEWS2 only")).toBeTruthy();
    const label = screen.getByText("Prediction models \u00b7 rank unchanged.");
    const why = screen.getByText("Why this patient is up");
    expect(why.compareDocumentPosition(label) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(label.compareDocumentPosition(screen.getByText("Prediction model flag (synthetic, not validated)")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows no research line for a patient with no model flag", async () => {
    api.getPatient.mockResolvedValue({ ...base, score: 2, escalation_reasons: [] } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Sorted on NEWS2 only")).toBeTruthy();
    expect(screen.queryByText(/rank unchanged/)).toBeNull();
  });

  it("shows no signal figure when all four values are nurse-typed", async () => {
    const nurse = (m: Detail["vitals"]["heart_rate"]) => ({ ...m, provenance: { ...m.provenance, source: "nurse" as const } });
    const v = base.vitals;
    api.getPatient.mockResolvedValue({
      ...base,
      signal_quality: 40,
      vitals: { ...v, heart_rate: nurse(v.heart_rate), respiratory_rate: nurse(v.respiratory_rate), spo2: nurse(v.spo2), temperature: nurse(v.temperature) },
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("No patch signal")).toBeTruthy();
    expect(screen.getByText("No patch").closest("[data-signal]")?.getAttribute("data-signal")).toBe("none");
    expect(screen.queryByText(/Signal \d+%/)).toBeNull();
  });
});

describe("PatientDetail for an administrator (read only)", () => {
  afterEach(() => { api.canRecordVitals = false; });

  it("shows the page and the read-only line, with no note box and no Record vitals", async () => {
    api.canRecordVitals = true;
    page("admin");
    await loaded();
    expect(screen.getByText("Read-only. Views are recorded in the audit log.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record vitals" })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    expect(screen.queryByPlaceholderText("Add a clinical note...")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add note" })).toBeNull();
  });

  it("reads the notes only when the drawer opens, once per open, and lists them", async () => {
    api.getNotes.mockResolvedValue([note("n-001", "BP trending down")]);
    const user = userEvent.setup();
    page("admin");
    await loaded();
    expect(api.getNotes).not.toHaveBeenCalled();
    const card = screen.getByRole("region", { name: "Latest note" });
    expect(within(card).queryByText(/No notes yet/)).toBeNull(); // nothing is read, so nothing is claimed
    await user.click(screen.getByRole("button", { name: "All notes" }));
    expect(await within(screen.getByRole("dialog")).findByText("BP trending down")).toBeTruthy();
    expect(api.getNotes).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close notes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "All notes (1)" })); // open again: a new read
    expect(api.getNotes).toHaveBeenCalledTimes(2);
  });

  it("says so when the notes cannot be read", async () => {
    api.getNotes.mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    page("admin");
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    expect(await screen.findByText("Notes could not be loaded.")).toBeTruthy();
  });

  it("does not show the read-only line to a clinician", async () => {
    page("md");
    await loaded();
    expect(screen.queryByText(/Read-only/)).toBeNull();
  });
});

describe("PatientDetail own baseline on the charts", () => {
  const hr = baseline.vitals[0] as BaselineComputed;
  const withVitals = (...vitals: BaselineResponse["vitals"]): BaselineResponse => ({ ...baseline, vitals });
  // Each chart also says its band in its subtitle.
  const bands = () => within(screen.getByRole("region", { name: "Trends" })).queryAllByText(/Own baseline \d/);
  const SHADED = "Own baseline: this patient's own baseline (synthetic, not validated). Not a normal range.";
  const openCharts = async (role?: SessionRole) => {
    page(role);
    await loaded();
  };

  it("draws each computed vital's band from the answer the page already has, with one label line", async () => {
    api.getBaseline.mockResolvedValue(
      withVitals(hr, { ...hr, vital: "respiratory_rate", band_low: 10, band_high: 18, significant: false, unit: "/min" }),
    );
    await openCharts();
    await waitFor(() => expect(bands()).toHaveLength(2)); // SpO2 has no entry: no band
    expect(screen.getByText(/Own baseline 58\.1 to 81\.9 bpm · Flagged/)).toBeTruthy();
    expect(screen.getByText(/Own baseline 10 to 18 \/min$/)).toBeTruthy();
    expect(screen.getAllByText(SHADED)).toHaveLength(1);
    expect(api.getBaseline).toHaveBeenCalledOnce(); // no second request for the charts
  });

  it("shows an admin the same band", async () => {
    api.getBaseline.mockResolvedValue(withVitals(hr));
    await openCharts("admin");
    await waitFor(() => expect(bands()).toHaveLength(1));
    expect(api.getBaseline).toHaveBeenCalledOnce();
  });

  it("draws no band and no label line when the baseline is not computed, absent or old", async () => {
    const oldServer = { ...hr, band_low: undefined }; // a server from before the two fields
    for (const answer of [
      null, // fixture build
      withVitals({ vital: "heart_rate", status: "forming", bins_valid: 3, bins_needed: 24 }),
      withVitals({ vital: "heart_rate", status: "unavailable", reason: "no_history" }),
      withVitals(oldServer as unknown as BaselineComputed),
      withVitals({ ...hr, delta: "x" } as unknown as BaselineComputed),
      { ...withVitals(hr), vitals: null }, // an answer of an odd shape must not blank the page
      { ...withVitals(hr), vitals: [null] },
      "failed", // a rejected request
    ]) {
      if (answer === "failed") api.getBaseline.mockRejectedValue(new Error("404"));
      else api.getBaseline.mockResolvedValue(answer);
      await openCharts();
      await waitFor(() => expect(api.getBaseline).toHaveBeenCalled());
      await act(async () => undefined);
      expect(bands()).toHaveLength(0);
      expect(screen.queryByText(SHADED)).toBeNull();
      expect(screen.queryByText(/Own baseline \d/)).toBeNull();
      cleanup();
      vi.clearAllMocks();
    }
  });
});

describe("PatientDetail trends on the page", () => {
  const configs = (label: string) => vi.mocked(trend).mock.calls.filter(([, c]) => c.series[0].name === label).map(([, c]) => c);
  const last = (label: string) => configs(label).at(-1)!;

  it("draws heart rate, respiratory rate and SpO2 on clinical ranges, with the NEWS2 lines, the own band and the points stretch", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const trends = screen.getByRole("region", { name: "Trends" });
    for (const label of ["Heart rate", "Respiratory rate", "SpO2"]) {
      expect(within(trends).getByRole("heading", { level: 3, name: new RegExp(`^${label} `) })).toBeTruthy();
    }
    await waitFor(() => expect(last("Heart rate").band).toEqual([58.1, 81.9]));
    expect(last("Heart rate").bandLabel).toBe("Own baseline 58.1–81.9");
    const hr = last("Heart rate");
    expect(hr.range).toEqual([40, 140]);
    expect(hr.limits).toEqual([{ at: 90, label: "+1 point above 90" }, { at: 51, label: "+1 point below 51" }]);
    expect([91, 90, 51, 50].map(hr.highlightWhere!)).toEqual([true, false, false, true]);
    const rr = last("Respiratory rate");
    expect(rr.range).toEqual([8, 30]);
    expect(rr.limits).toEqual([{ at: 20, label: "+2 points above 20" }, { at: 12, label: "+1 point below 12" }]);
    expect(rr.band).toBeUndefined(); // no computed band for it
    const spo2 = last("SpO2");
    expect(spo2.range).toEqual([85, 100]);
    expect(spo2.limits).toEqual([{ at: 96, label: "+1 point below 96" }]); // scale 1
    expect([95, 96].map(spo2.highlightWhere!)).toEqual([true, false]);
    expect(trends.closest("[data-hw-space]")).toBeNull(); // no space: the points stretch keeps the default (red) highlight
    expect(within(trends).getByText("Charts show patch data only. Values entered by a nurse are not plotted.")).toBeTruthy();
  });

  it("filters the stored readings with 1 h, 6 h, 24 h and Stay, counted from the newest", async () => {
    // p-001 has 24 sets from 01:00 to 06:45; one more two days before.
    const h = base.vital_history;
    api.getPatient.mockResolvedValue({ ...base, vital_history: { ...h, heart_rate: [{ time: "2026-09-01T01:00:00Z", value: 70 }, ...h.heart_rate] } });
    const user = userEvent.setup();
    page();
    await loaded();
    const group = within(screen.getByRole("group", { name: "Time range" }));
    expect(group.getAllByRole("radio").map((r) => (r as HTMLInputElement).labels![0].textContent)).toEqual(["1 h", "6 h", "24 h", "Stay"]);
    expect((group.getByRole("radio", { name: "6 h" }) as HTMLInputElement).checked).toBe(true);
    expect(last("Heart rate").x).toHaveLength(24);
    await user.click(group.getByRole("radio", { name: "1 h" }));
    expect(last("Heart rate").x).toHaveLength(4);
    await user.click(group.getByRole("radio", { name: "Stay" }));
    expect(last("Heart rate").x).toHaveLength(25);
    await user.click(group.getByRole("radio", { name: "24 h" }));
    expect(last("Heart rate").x).toHaveLength(24);
  });

  it("says when a vital has too few readings in the range, and draws scale 2's lines for a patient on oxygen", async () => {
    const v = base.vitals;
    api.getPatient.mockResolvedValue({
      ...base,
      vital_history: { ...base.vital_history, respiratory_rate: [] },
      vitals: {
        ...v,
        spo2_scale: { ...v.spo2_scale, data: { present: true, value: 2 } },
        supplemental_o2: { ...v.supplemental_o2, data: { present: true, value: true } },
      },
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Respiratory rate: no stored readings in this range.")).toBeTruthy();
    const spo2 = last("SpO2");
    expect(spo2.limits).toEqual([{ at: 92, label: "+1 point above 92" }, { at: 88, label: "+1 point below 88" }]);
    expect([93, 90, 87].map(spo2.highlightWhere!)).toEqual([true, false, true]);
  });

  it("reads scale 1 when no scale is recorded", async () => {
    const v = base.vitals;
    api.getPatient.mockResolvedValue({ ...base, vitals: { ...v, spo2_scale: { ...v.spo2_scale, data: { present: false, reason: "not_recorded" } } } } as Detail);
    page();
    await loaded();
    expect(last("SpO2").limits).toEqual([{ at: 96, label: "+1 point below 96" }]);
  });
});

describe("PatientDetail follows the ward's speed", () => {
  const published = (over: object = {}) => ({
    shared: { clockS: 109_200, speed: 60, ended: false, counts: { received: 1, rejected: 0, abstained: 0 }, ...over },
    patients: [],
    can_control: false,
  });
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  beforeEach(() => {
    api.usesServer = true;
    api.getLiveWard.mockResolvedValue(published());
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    api.canRecordVitals = false;
  });

  it("reads the patient and the live view again every 2 seconds at 60x, and shows the clock", async () => {
    page();
    await loaded();
    expect(screen.getByRole("group", { name: "Ward clock" }).textContent).toBe(`Ward clock 4 ${SEP}, 21:20 \u00b7 60x`);
    expect(api.getPatient).toHaveBeenCalledTimes(1);
    await advance(2_100);
    expect(api.getPatient).toHaveBeenCalledTimes(2);
    await advance(2_000);
    expect(api.getPatient).toHaveBeenCalledTimes(3);
    expect(api.getLiveWard).toHaveBeenCalledTimes(3);
  });

  it("reads every 5 seconds at 10x", async () => {
    api.getLiveWard.mockResolvedValue(published({ speed: 10 }));
    page();
    await loaded();
    await advance(2_500);
    expect(api.getPatient).toHaveBeenCalledTimes(1);
    await advance(2_700);
    expect(api.getPatient).toHaveBeenCalledTimes(2);
  });

  it("keeps the open drawer, the typed note, the vitals form and the focus across refreshes", async () => {
    api.canRecordVitals = true;
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page("charge_rn");
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    await user.type(screen.getByPlaceholderText("Add a clinical note..."), "Repeat lactate");
    await user.click(screen.getByRole("button", { name: "Record vitals" }));
    const hr = screen.getByLabelText("Heart rate (bpm)");
    await user.type(hr, "118");
    api.getPatient.mockResolvedValue({ ...base, score: 4 });
    await advance(4_200);
    expect(api.getPatient.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByPlaceholderText("Add a clinical note...")).toHaveProperty("value", "Repeat lactate");
    expect(screen.getByLabelText("Heart rate (bpm)")).toBe(hr);
    expect(hr).toHaveProperty("value", "118");
    expect(document.activeElement).toBe(hr);
  });

  it("keeps the page and says so when the patient is no longer ranked, and stops asking", async () => {
    page();
    await loaded();
    api.getPatient.mockResolvedValue(null);
    await advance(2_100);
    expect((await screen.findByText(/No longer on your ranked list\. Figures from/)).textContent).toMatch(/\d{2}:\d{2}/);
    expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
    expect(screen.queryByText("Patient not found.")).toBeNull();
    api.getPatient.mockClear();
    await advance(10_000);
    expect(api.getPatient).not.toHaveBeenCalled();
  });

  it("keeps the last good data and says so when a refresh fails, and clears it on the next good answer", async () => {
    page();
    await loaded();
    api.getPatient.mockRejectedValue(new Error("down"));
    await advance(2_100);
    const banner = await screen.findByRole("alert");
    expect(banner.className).toContain("hw-banner--inverse"); // Phase 1's black banner
    expect(within(banner).getByText(/^Offline since \d\d:\d\d/)).toBeTruthy();
    expect(banner.textContent).toContain("Retrying every 5 seconds. Acknowledging and recording wait until the connection is back.");
    expect(screen.queryByText(/could not be loaded/)).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
    api.getPatient.mockResolvedValue(base);
    await advance(5_100);
    await waitFor(() => expect(screen.queryByText(/Offline since/)).toBeNull());
  });

  it("puts the offline banner above the page and the no-longer line above the decision strip", async () => {
    page();
    await loaded();
    api.getPatient.mockRejectedValue(new Error("down"));
    await advance(2_100);
    const failed = await screen.findByText(/^Offline since/);
    const note = screen.getByText("Simulated patients until a hospital is connected.");
    expect(failed.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    api.getPatient.mockResolvedValue(null);
    await advance(5_100);
    const gone = await screen.findByText(/No longer on your ranked list\. Figures from/);
    const decision = screen.getByRole("region", { name: "Decision" });
    expect(gone.compareDocumentPosition(decision) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The server answered, so the line that it cannot be reached is no longer true and must go.
    expect(screen.queryByText(/Offline since/)).toBeNull();
  });

  it("shows the offline line in an open drawer too", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: "All notes" }));
    api.getPatient.mockRejectedValue(new Error("down"));
    await advance(2_100);
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByText(/^Offline since \d\d:\d\d.*\. Cannot reach the ward server\.$/)).toBeTruthy());
  });

  it("keeps the band across a timed read of the patient", async () => {
    api.getBaseline.mockResolvedValue({ ...baseline, vitals: [baseline.vitals[0]] });
    page();
    await loaded();
    await screen.findByRole("region", { name: "Change from own baseline" });
    const trends = screen.getByRole("region", { name: "Trends" });
    const line = within(trends).getByText(/Own baseline 58\.1 to 81\.9 bpm/);
    await advance(2_100);
    expect(within(trends).getAllByText(/Own baseline 58\.1 to 81\.9 bpm/)).toEqual([line]);
  });

  it("leaves the trends alone while the data does not change", async () => {
    page();
    await loaded();
    await advance(900);
    const trends = screen.getByRole("region", { name: "Trends" });
    const before = trends.innerHTML;
    await advance(4_200);
    expect(api.getPatient.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole("region", { name: "Trends" })).toBe(trends);
    expect(trends.innerHTML).toBe(before); // the graphs are not rebuilt
  });

  it("drops a slow old answer that lands after a newer one", async () => {
    api.canRecordVitals = true;
    api.recordVitals.mockResolvedValue({
      patient_id: "p-001", blood_pressure: null, consciousness: null, supplemental_o2: null, spo2_scale: null,
      heart_rate: null, respiratory_rate: null, spo2: null, temperature: null,
      ranked: true, rank: 1, score: 4, not_ranked_reason: null, not_ranked_instruction: null,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page();
    await loaded();
    let late!: (p: Detail) => void;
    api.getPatient.mockReturnValueOnce(new Promise((res) => (late = res)));
    await advance(2_100); // a timed round starts and hangs
    api.getPatient.mockResolvedValue({ ...base, name: "Newer, Pat" });
    await user.click(screen.getByRole("button", { name: "Record vitals" }));
    await user.type(screen.getByLabelText("Heart rate (bpm)"), "118");
    await user.click(screen.getByRole("button", { name: "Record" })); // the save's read is answered first
    expect(await screen.findByRole("heading", { level: 1, name: "Newer, Pat" })).toBeTruthy();
    await act(async () => late({ ...base, name: "Older, Pat" }));
    expect(screen.queryByText("Older, Pat")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Newer, Pat" })).toBeTruthy();
  });

  it("after null, recorded vitals and ranked again, the stale line is gone and the timer reads again", async () => {
    api.canRecordVitals = true;
    api.recordVitals.mockResolvedValue({
      patient_id: "p-001", blood_pressure: null, consciousness: null, supplemental_o2: null, spo2_scale: null,
      heart_rate: null, respiratory_rate: null, spo2: null, temperature: null,
      ranked: true, rank: 1, score: 9, not_ranked_reason: null, not_ranked_instruction: null,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page();
    await loaded();
    api.getPatient.mockResolvedValue(null);
    await advance(2_100);
    expect(await screen.findByText(/No longer on your ranked list/)).toBeTruthy();
    api.getPatient.mockClear().mockResolvedValue(base);
    await user.click(screen.getByRole("button", { name: "Record vitals" }));
    await user.type(screen.getByLabelText("Heart rate (bpm)"), "118");
    await user.click(screen.getByRole("button", { name: "Record" }));
    await waitFor(() => expect(screen.queryByText(/No longer on your ranked list/)).toBeNull());
    api.getPatient.mockClear();
    await advance(2_100);
    expect(api.getPatient).toHaveBeenCalledTimes(1);
  });

  it("does not ask again, or for the live view, for an administrator", async () => {
    page("admin");
    await loaded();
    await advance(30_000);
    expect(api.getPatient).toHaveBeenCalledTimes(1);
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Ward clock" })).toBeNull();
  });

  it("does not ask again, or for the live view, in the demo build", async () => {
    api.usesServer = false;
    page();
    await loaded();
    await advance(30_000);
    expect(api.getPatient).toHaveBeenCalledTimes(1);
    expect(api.getLiveWard).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Ward clock" })).toBeNull();
  });
});

describe("PatientDetail prediction models panel", () => {
  it("sits after the own-baseline panel and asks for nothing extra", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const own = await screen.findByRole("region", { name: "Change from own baseline" });
    const models = screen.getByRole("region", { name: "Prediction models" });
    expect(own.compareDocumentPosition(models) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(own.parentElement).toBe(models.parentElement);
    expect(api.getPatient).toHaveBeenCalledOnce();
    expect(api.getBaseline).toHaveBeenCalledOnce();
    expect(api.getNotes).toHaveBeenCalledOnce();
  });

  it("shows the fixture words, also for an admin, and keeps the summary line", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      escalation_reasons: [{ rule: "prediction_model_flag", text: "Prediction model flag (synthetic, not validated)", pathway: null }],
    } as Detail);
    page("admin");
    await loaded();
    const region = screen.getByRole("region", { name: "Prediction models" });
    expect(within(region).getAllByText(/No result: the patient's own baseline is still forming/)).toHaveLength(1);
    expect(screen.getByText("Prediction models · rank unchanged.")).toBeTruthy();
  });

  it("shows no panel when the server sends no models", async () => {
    const old: Record<string, unknown> = { ...base };
    delete old.models;
    api.getPatient.mockResolvedValue(old);
    page();
    await loaded();
    expect(screen.queryByRole("region", { name: "Prediction models" })).toBeNull();
  });
});

describe("PatientDetail: ask about this patient", () => {
  const asked = {
    id: 4, requested_by: "rn-manalo", requested_at: "2026-11-13T09:30:00Z", question: "Why is the rate up?", state: "done" as const,
    reason: null, claimed_at: null, finished_at: null,
  };
  beforeEach(() => {
    api.hasPatientQuestions = true;
    api.getPatientQuestions.mockResolvedValue([asked]);
    api.getPatientQuestion.mockResolvedValue({ ...asked, answer: "the answer", report: null });
  });
  afterEach(() => {
    api.hasPatientQuestions = false;
  });

  it("has a header button that opens the assistant panel on this patient, and reads no thread itself", async () => {
    for (const role of ["rn", "md"] as const) {
      render(
        <MemoryRouter initialEntries={["/patient/p-001"]}>
          <Routes>
            <Route path="/patient/:id" element={<><PatientDetail session={session(role)} onSignOut={() => undefined} /><ChatBubble session={session(role)} onSignOut={() => undefined} /></>} />
          </Routes>
        </MemoryRouter>,
      );
      await loaded();
      expect(api.getPatientQuestions).not.toHaveBeenCalled(); // closed: the page and the panel read nothing
      await userEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
      const dialog = screen.getByRole("dialog", { name: "Assistant" });
      expect(within(dialog).getByText("This patient")).toBeTruthy();
      await waitFor(() => expect(api.getPatientQuestions).toHaveBeenCalledWith("p-001"));
      expect(screen.queryByText("Ask about this patient")).toBeNull(); // the old card is gone
      cleanup();
      vi.clearAllMocks();
      api.getPatient.mockResolvedValue(base);
      api.getNotes.mockResolvedValue([]);
      api.getBaseline.mockResolvedValue(null);
      api.getLiveWard.mockResolvedValue(null);
      api.getPatientQuestions.mockResolvedValue([asked]);
    }
  });

  it("has no button, and no call, for an admin and in the demo build", async () => {
    page("admin");
    await loaded();
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).toBeNull();
    cleanup();
    api.hasPatientQuestions = false;
    page("rn");
    await loaded();
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).toBeNull();
    expect(api.getPatientQuestions).not.toHaveBeenCalled();
    expect(api.getPatientQuestion).not.toHaveBeenCalled();
  });

});

describe("PatientDetail: stay panel", () => {
  const stay = {
    bins: 4, events: [{ at: "2026-11-13T09:00:00Z", by: null, kind: "admitted", note_id: null, role: null, score: null, source: "patient" }],
    events_cut: false, grid_from: "2026-11-13T09:00:00Z", grid_to: "2026-11-13T10:00:00Z", label: "Rebuilt.", patient_id: "p-001",
    runs: [], runs_cut: false, synthetic: true, unavailable: null,
  };
  beforeEach(() => {
    api.hasHistory = true;
    api.getPatientHistory.mockResolvedValue(stay);
  });
  afterEach(() => {
    api.hasHistory = false;
  });

  it("sits under Trends as an accordion, for an admin too, and reads nothing while closed", async () => {
    api.hasPatientQuestions = true;
    api.getPatientQuestions.mockResolvedValue([]);
    try {
      page("rn");
      await loaded();
      const summary = await screen.findByText("Stay", { selector: "summary" });
      const trends = screen.getByRole("region", { name: "Trends" });
      expect(trends.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(trends.parentElement!.contains(summary)).toBe(true); // the same (left) column
      expect(summary.closest("details")!.open).toBe(false);
      expect(api.getPatientHistory).not.toHaveBeenCalled();
      cleanup();
      page("admin");
      await loaded();
      expect(await screen.findByText("Stay", { selector: "summary" })).toBeTruthy();
    } finally {
      api.hasPatientQuestions = false;
    }
  });

  it("is absent, with no call, in the demo build", async () => {
    api.hasHistory = false;
    page("rn");
    await loaded();
    expect(screen.queryByText("Stay", { selector: "summary" })).toBeNull();
    expect(api.getPatientHistory).not.toHaveBeenCalled();
  });

  it("is not remounted by the page's own refresh and causes no read", async () => {
    api.usesServer = true;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      page("rn");
      await loaded();
      await user.click(await screen.findByText("Stay", { selector: "summary" }));
      await screen.findByText(/^Admitted at /);
      expect(api.getPatientHistory).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
      expect(api.getPatient.mock.calls.length).toBeGreaterThan(1);
      expect((screen.getByText("Stay", { selector: "summary" }).closest("details") as HTMLDetailsElement).open).toBe(true);
      expect(screen.getByText(/^Admitted at /)).toBeTruthy();
      expect(api.getPatientHistory).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
      api.usesServer = false;
    }
  });
});

describe("PatientDetail flips on a changed refresh", () => {
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  const flips = () => [...document.querySelectorAll('[class*="_flip_"]')].map((e) => e.textContent);
  beforeEach(() => {
    api.usesServer = true;
    api.getLiveWard.mockResolvedValue({
      shared: { clockS: 109_200, speed: 60, ended: false, counts: { received: 1, rejected: 0, abstained: 0 } },
      patients: [],
      can_control: false,
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => vi.useRealTimers());

  it("flips the score and one vital, and nothing on first load or an unchanged refresh", async () => {
    page();
    await loaded();
    expect(flips()).toEqual([]);
    api.getPatient.mockResolvedValue({ ...base }); // a new object, the same figures
    await advance(2_100);
    expect(flips()).toEqual([]);
    const hr = base.vitals.heart_rate;
    api.getPatient.mockResolvedValue({
      ...base,
      score: base.score - 1,
      vitals: { ...base.vitals, heart_rate: { ...hr, data: { present: true, value: 77 } } },
    });
    await advance(2_100);
    expect(flips().sort()).toEqual([String(base.score - 1), "77 bpm"].sort());
  });
});

// ---- Phase 2: the header, the decision strip, the pager and the phone ----

const ranks = synthetic.ranked.slice(0, 3) as unknown as ScoreRow[]; // p-001, p-002, p-003 in rank order
const names = Object.fromEntries((synthetic.ranked as unknown as ScoreRow[]).map((r) => [r.id, r.name]));
const feedOf = (over: Partial<WardFeed> = {}): WardFeed => ({
  data: {
    ward: "W4", clinician: "rn-manalo", shift: "07:00", receiving_data: true, sort_tier: "A", tier_status: row.tier_status,
    ranked: ranks, abstained: [], now: "2026-09-03T07:00:00Z",
  } as WardResponse,
  fetchedAt: Date.parse("2026-09-03T07:00:00Z"), failedSince: null, sim: null, refresh: () => undefined, pollMs: 15_000,
  readOnly: false, alerts: [], acknowledge: vi.fn(), flush: () => Promise.resolve(), notice: null, setNotice: () => undefined,
  unit: "W4", setUnit: () => undefined, ...over,
});
const fed = (feed: WardFeed, role: SessionRole = "charge_rn", path = "/patient/p-002") =>
  render(
    <WardFeedContext.Provider value={feed}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/patient/:id" element={<PatientDetail session={session(role)} onSignOut={() => undefined} />} />
        </Routes>
      </MemoryRouter>
    </WardFeedContext.Provider>,
  );
const showing = (id: string) => screen.findByRole("heading", { level: 1, name: names[id] });
const pager = () => within(screen.getByRole("group", { name: "Move through patients in rank order" }));

describe("PatientDetail: the decision strip", () => {
  it("leads with the score and its band, the next step, why and the signal, and has one NEWS2 only", async () => {
    api.getPatient.mockResolvedValue({ ...base, trend: "rising" });
    page("rn");
    await loaded();
    const region = screen.getByRole("region", { name: "Decision" });
    const strip = within(region);
    expect(region.getAttribute("data-band")).toBe("high");
    expect(strip.getByText("Call the medical team now (registrar or above)")).toBeTruthy();
    expect(strip.getByText("Total 9 (≥7): high")).toBeTruthy();
    expect(strip.getByText("100%").closest("[data-signal]")!.getAttribute("data-signal")).toBe("good");
    expect(strip.getByText(/s ago$/)).toBeTruthy(); // the data's age under the signal
    expect(screen.getByText("Advisory only. BioSentry does not replace a monitor alarm. Views are logged for audit under rn-manalo.")).toBeTruthy();
  });

  it("names the patient with the context under the name", async () => {
    page("rn");
    expect((await loaded()).textContent).toBe(row.name);
    expect(screen.getByText(`${row.age} ${row.sex} · Bed ${row.bed} · ${row.admitting_context}`)).toBeTruthy();
  });
});

describe("PatientDetail: Previous and Next in rank order", () => {
  beforeEach(() => {
    api.getPatient.mockImplementation(async (id: string) => ({ ...base, id, name: names[id] }));
  });

  it("walks the ward's ranked order and is disabled at both ends", async () => {
    const user = userEvent.setup();
    fed(feedOf());
    await showing("p-002");
    expect(pager().getByText("2 of 3 in rank order")).toBeTruthy();
    expect(pager().getByRole("button", { name: "Next" }).querySelector("kbd")!.getAttribute("aria-hidden")).toBe("true");
    await user.click(pager().getByRole("button", { name: "Next" }));
    await showing("p-003");
    expect(pager().getByText("3 of 3 in rank order")).toBeTruthy();
    expect((pager().getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(pager().getByRole("button", { name: "Previous" }));
    await showing("p-002");
    await user.click(pager().getByRole("button", { name: "Previous" }));
    await showing("p-001");
    expect(pager().getByText("1 of 3 in rank order")).toBeTruthy();
    expect((pager().getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.getPatient.mock.calls.map(([id]) => id)).toEqual(["p-002", "p-003", "p-002", "p-001"]);
  });

  it("moves with J and K outside a field, and not from a field, a dialog or with a modifier", async () => {
    const user = userEvent.setup();
    fed(feedOf());
    await showing("p-002");
    await user.click(screen.getByRole("radio", { name: "1 h" })); // a field: the key is the field's
    await user.keyboard("j");
    await user.keyboard("{Control>}j{/Control}");
    await user.click(screen.getByRole("button", { name: "All notes" }));
    screen.getByRole("button", { name: "Close notes" }).focus(); // inside a dialog
    await user.keyboard("k");
    await user.click(screen.getByRole("button", { name: "Close notes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(api.getPatient).toHaveBeenCalledTimes(1);
    (document.activeElement as HTMLElement).blur();
    await user.keyboard("j");
    await showing("p-003");
    await user.keyboard("j"); // the end: nothing further
    await user.keyboard("x");
    await user.keyboard("K");
    await showing("p-002");
    expect(api.getPatient.mock.calls.map(([id]) => id)).toEqual(["p-002", "p-003", "p-002"]);
  });

  it("says a patient outside the ranked order is not in it, with both ways shut", async () => {
    fed(feedOf(), "charge_rn", "/patient/p-009");
    await showing("p-009");
    expect(pager().getByText("Not in the ranked order")).toBeTruthy();
    for (const name of ["Previous", "Next"]) expect((pager().getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows no pager without the ward's order (an administrator), rather than make one up", async () => {
    page("admin");
    await loaded();
    expect(screen.queryByRole("group", { name: "Move through patients in rank order" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Acknowledge alert" })).toBeNull();
  });
});

describe("PatientDetail: Acknowledge alert", () => {
  afterEach(() => {
    document.querySelector(".hw-toast-region")?.remove();
    api.canRecordVitals = false;
  });

  it("acknowledges this patient's open alert with the ward's Undo, and only while one is open", async () => {
    const user = userEvent.setup();
    api.getWard.mockResolvedValue(feedOf().data);
    api.getAcknowledgements.mockResolvedValue([]);
    api.acknowledge.mockResolvedValue(undefined);
    render(
      <WardFeedProvider session={session("rn")}>
        <MemoryRouter initialEntries={["/patient/p-001"]}>
          <Routes><Route path="/patient/:id" element={<PatientDetail session={session("rn")} onSignOut={() => undefined} />} /></Routes>
        </MemoryRouter>
      </WardFeedProvider>,
    );
    await loaded();
    await user.click(await screen.findByRole("button", { name: "Acknowledge alert" }));
    expect(screen.queryByRole("button", { name: "Acknowledge alert" })).toBeNull(); // hidden while Undo is open
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("button", { name: "Acknowledge alert" })).toBeTruthy();
    expect(api.acknowledge).not.toHaveBeenCalled(); // undone inside the window: nothing saved
  });

  it("is not offered for a patient with no open alert", async () => {
    api.getPatient.mockResolvedValue({ ...base, id: "p-002" });
    fed(feedOf({ alerts: [ranks[0]] }), "rn", "/patient/p-002");
    await loaded();
    expect(screen.queryByRole("button", { name: "Acknowledge alert" })).toBeNull();
  });

  it("waits while offline, as Record vitals does, and the last data stays", async () => {
    api.canRecordVitals = true;
    fed(feedOf({ alerts: [ranks[0]], failedSince: Date.now() }), "rn", "/patient/p-001");
    await loaded();
    expect(within(screen.getByRole("alert")).getByText(/^Offline since/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Acknowledge alert" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Record vitals" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/s ago$/).getAttribute("data-stale")).toBe("true"); // the age turns red
    expect(screen.getByText("Call the medical team now (registrar or above)")).toBeTruthy();
  });
});

describe("PatientDetail on a phone", () => {
  const real = window.matchMedia;
  beforeEach(() => {
    window.matchMedia = ((q: string) => ({ ...real(q), matches: q === "(width < 768px)" })) as typeof window.matchMedia;
    api.getPatient.mockImplementation(async (id: string) => ({ ...base, id, name: names[id] }));
  });
  afterEach(() => {
    window.matchMedia = real;
    api.canRecordVitals = false;
  });
  const bar = () => within(document.querySelector(".hw-actionbar") as HTMLElement);

  it("puts the decision strip first, then the tabs Now, Trends, Labs and Notes, driven by the keyboard", async () => {
    const user = userEvent.setup();
    page("rn");
    await loaded();
    const list = screen.getByRole("tablist", { name: "Patient sections" });
    const tabs = within(list).getAllByRole("tab");
    const panel = () => screen.getByRole("tabpanel");
    const selected = () => tabs.findIndex((t) => t.getAttribute("aria-selected") === "true");
    expect(tabs.map((t) => t.textContent)).toEqual(["Now", "Trends", "Labs", "Notes"]);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1, -1]);
    expect(tabs[0].getAttribute("aria-controls")).toBe(panel().id);
    expect(panel().getAttribute("aria-labelledby")).toBe(tabs[0].id);
    expect(screen.getByRole("region", { name: "Decision" }).compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(panel()).getByRole("region", { name: "Current vitals" })).toBeTruthy();
    expect(within(panel()).getByRole("region", { name: "Against own baseline" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /^Notes/ })).toBeNull(); // the action bar has Notes on a phone

    tabs[0].focus();
    await user.keyboard("{ArrowRight}");
    expect([selected(), tabs.indexOf(document.activeElement as HTMLElement)]).toEqual([1, 1]);
    expect(within(panel()).getByRole("region", { name: "Trends" })).toBeTruthy();
    await user.keyboard("{End}");
    expect(selected()).toBe(3);
    expect(within(panel()).getByPlaceholderText("Add a clinical note...")).toBeTruthy();
    await user.keyboard("{ArrowRight}");
    expect(selected()).toBe(0); // round to the first
    await user.keyboard("{ArrowLeft}");
    expect(selected()).toBe(3);
    await user.keyboard("{Home}");
    expect(selected()).toBe(0);
    await user.keyboard("a");
    expect(selected()).toBe(0);
    await user.click(tabs[2]);
    expect(within(panel()).getByRole("region", { name: "Labs" })).toBeTruthy();
  });

  it("holds Record vitals, Notes and Next in the action bar, in place of the bottom tabs and the header pager", async () => {
    api.canRecordVitals = true;
    const user = userEvent.setup();
    fed(feedOf());
    await showing("p-002");
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull(); // a charge nurse's bottom tabs give way
    expect(screen.queryByRole("group", { name: "Move through patients in rank order" })).toBeNull();
    expect(bar().getAllByRole("button").map((b) => b.textContent)).toEqual(["Record vitals", "Notes", "Next"]);
    await user.click(bar().getByRole("button", { name: "Notes" }));
    expect(screen.getByRole("tab", { name: "Notes" }).getAttribute("aria-selected")).toBe("true");
    await user.click(bar().getByRole("button", { name: "Record vitals" }));
    expect(screen.getByRole("dialog", { name: "Record vitals" })).toBeTruthy(); // full screen on a phone (the kit's drawer)
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(bar().getByRole("button", { name: "Next" }));
    await showing("p-003");
    expect((bar().getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reads an administrator's notes only when the Notes tab opens, and offers only Notes", async () => {
    api.getNotes.mockResolvedValue([note("n-001", "BP trending down")]);
    const user = userEvent.setup();
    page("admin");
    await loaded();
    expect(bar().getAllByRole("button").map((b) => b.textContent)).toEqual(["Notes"]);
    expect(api.getNotes).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: "Notes" }));
    expect(await screen.findByText("BP trending down")).toBeTruthy();
    expect(api.getNotes).toHaveBeenCalledTimes(1);
  });
});
