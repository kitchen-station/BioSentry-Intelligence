// @vitest-environment jsdom
/**
 * The patient page: the score and why, the role's next step,
 * labs with their freshness, notes (append-only, a failed save keeps the text), the trend
 * charts, and the not-found / load-error states.
 */

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import type { BaselineComputed, BaselineResponse, ClinicalNote, LabPanel, PatientDetail as Detail, Session, SessionRole } from "../types";
import { PatientDetail } from "./PatientDetail";

// "Sep" or "Sept", whichever the runtime's en-GB gives: the clock uses the same call as the simulator.
const SEP = new Date(Date.UTC(2026, 8, 3)).toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getPatient: vi.fn(),
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

// p-001 from the committed ward: NEWS2 9 (high), two reasons.
const row = synthetic.ranked[0] as unknown as Detail;
const base: Detail = {
  ...row,
  ...(synthetic.details as unknown as Record<string, Pick<Detail, "vital_history" | "tier_breakdown">>)[
    "p-001"
  ],
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

describe("PatientDetail", () => {
  it("says the patients are simulated, once, and has the session menu", async () => {
    page("rn");
    await loaded();
    expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Ward" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getByRole("link", { name: "← Ward view" })).toBeTruthy();
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
    expect(screen.getByText(/1h since collection/)).toBeTruthy();
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
    expect(screen.getByText("Other mechanism findings: not yet implemented")).toBeTruthy();
  });

  it("lists every recorded lab", async () => {
    api.getPatient.mockResolvedValue({ ...base, labs: { ...everyLab, freshness_hours: 2 } });
    page();
    await loaded();
    const labs = screen.getByText(/^Labs/).parentElement!;
    for (const label of LAB_LABELS) expect(within(labs).getByText(label)).toBeTruthy();
    expect(screen.queryByText("No lab values recorded")).toBeNull();
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
    expect(screen.queryByText("Next step")).toBeNull(); // band low: no response block
    expect(screen.getByText("Other mechanism findings: not yet implemented")).toBeTruthy(); // never an empty box
    expect(screen.getByText("Own-baseline findings need the live API.")).toBeTruthy();
    expect(screen.getByText("No lab values recorded")).toBeTruthy();
    expect(screen.getByText(/^-- -- · Bed/)).toBeTruthy();
    expect(screen.queryByText(/since collection/)).toBeNull();
    expect(screen.getByText("Signal 80%")).toBeTruthy(); // the strip
    expect(screen.getByText("80%")).toBeTruthy(); // the summary block
  });

  it("shows the own-baseline panel in the Tier B panel, loaded once with the patient", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const region = await screen.findByRole("region", { name: "Change from own baseline" });
    expect(api.getBaseline).toHaveBeenCalledOnce();
    expect(api.getBaseline).toHaveBeenCalledWith("p-001");
    expect(within(region).getByText(/Heart rate/).closest("li")!.textContent).toContain("from own baseline");
    const tierB = region.parentElement!;
    expect(within(tierB).getByText("Other mechanism findings: not yet implemented")).toBeTruthy();
    expect(tierB.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_CONTAINED_BY).toBeTruthy();
    expect(screen.queryByText("Own-baseline findings need the live API.")).toBeNull();
  });

  it("shows the panel after the placeholder", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    expect(await screen.findByRole("region", { name: "Change from own baseline" })).toBeTruthy();
    expect(screen.getByText("Other mechanism findings: not yet implemented")).toBeTruthy();
  });

  it("says it is loading the baseline, and never shows another patient's", async () => {
    api.getBaseline.mockReturnValue(new Promise(() => undefined));
    page();
    await loaded();
    expect(screen.getByText("Loading baseline…")).toBeTruthy();
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
    expect(screen.getByText("Other mechanism findings: not yet implemented")).toBeTruthy();
  });

  it("shows not found for a patient that is missing or not yours", async () => {
    api.getPatient.mockResolvedValue(null);
    page();
    expect(await screen.findByText("Patient not found.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
    expectLabEntry();
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
    await user.click(await screen.findByRole("button", { name: "Notes (1)" }));
    expect(screen.getByText("Clinical notes · 1")).toBeTruthy();
    expect(screen.getByText("BP trending down")).toBeTruthy();

    const add = screen.getByRole("button", { name: "Add note" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    await user.type(screen.getByPlaceholderText("Add a clinical note..."), "  Repeat lactate sent  ");
    await user.click(add);
    expect(api.addNote).toHaveBeenCalledWith("p-001", "rn-manalo", "Charge RN", "Repeat lactate sent");
    expect(await screen.findByText("Repeat lactate sent")).toBeTruthy();
    expect(screen.getByText("Clinical notes · 2")).toBeTruthy();
    expect((screen.getByPlaceholderText("Add a clinical note...") as HTMLTextAreaElement).value).toBe("");
  });

  it("saves a note with Ctrl+Enter, but not with Enter alone", async () => {
    const user = userEvent.setup();
    api.addNote.mockResolvedValue(note("n-003", "Seen by MD"));
    page("md");
    await loaded();
    await user.click(screen.getByRole("button", { name: "Notes" }));
    expect(screen.getByText("Clinical notes")).toBeTruthy();
    const box = screen.getByPlaceholderText("Add a clinical note...");
    await user.type(box, "Seen by MD{Enter}");
    expect(api.addNote).not.toHaveBeenCalled();
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(api.addNote).toHaveBeenCalledWith("p-001", "rn-manalo", "MD", "Seen by MD");
    await waitFor(() => expect(screen.getByText("Seen by MD")).toBeTruthy());
  });

  it("keeps the text and says so when a note is not saved", async () => {
    const user = userEvent.setup();
    api.addNote.mockRejectedValue(new Error("500"));
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Notes" }));
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
    await user.click(screen.getByRole("button", { name: "Notes" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Notes could not be loaded.");
  });

  it("opens the charts drawer and closes drawers by toggle, close button or backdrop", async () => {
    const user = userEvent.setup();
    const { container } = page();
    await loaded();
    const charts = screen.getByRole("button", { name: "Charts" });

    await user.click(charts);
    const title = screen.getByText("Vital trends \u00b7 5 h");
    const panel = title.closest("div")!.parentElement!;
    for (const label of ["Heart rate", "Respiratory rate", "SpO2"]) {
      expect(within(panel).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(within(panel).getByText("Charts show patch data only. Values entered by a nurse are not plotted.")).toBeTruthy();
    await user.click(charts); // same button again closes it
    expect(screen.queryByText("Vital trends \u00b7 5 h")).toBeNull();

    await user.click(charts);
    await user.click(screen.getByRole("button", { name: "×" }));
    expect(screen.queryByText("Vital trends \u00b7 5 h")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Notes" }));
    const backdrop = container.querySelector('[class*="overlayBackdrop"]') as HTMLElement;
    await user.click(backdrop);
    expect(screen.queryByPlaceholderText("Add a clinical note...")).toBeNull();
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
    const save = async (user: ReturnType<typeof userEvent.setup>) => {
      await loaded();
      await user.click(screen.getByRole("button", { name: "Record vitals" }));
      await user.type(screen.getByLabelText("Heart rate (bpm)"), "118");
      await user.click(screen.getByRole("button", { name: "Record" }));
    };
    beforeEach(() => { api.canRecordVitals = true; });
    afterEach(() => { api.canRecordVitals = false; });

    it("reads the patient again when the result is ranked", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(true));
      page();
      await save(user);
      await waitFor(() => expect(api.getPatient).toHaveBeenCalledTimes(2));
      // The panel reads again at once: a typed value can change which vital counts.
      await waitFor(() => expect(api.getBaseline).toHaveBeenCalledTimes(2));
      api.getPatient.mockResolvedValue({ ...base, score: 4, escalation_reasons: [] });
      await user.type(screen.getByLabelText("Heart rate (bpm)"), "120");
      await user.click(screen.getByRole("button", { name: "Record" }));
      await waitFor(() => expect(api.getPatient).toHaveBeenCalledTimes(3));
      expect(await screen.findByText("Sorted on NEWS2 only")).toBeTruthy();
      expect(screen.queryByRole("link", { name: "Back to ward" })).toBeNull();
    });

    it("shows the result and a link to the ward, and does not read again, when NOT RANKED", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(false));
      page();
      await save(user);
      expect(await screen.findByText(/Recorded. Still not ranked. No device data./)).toBeTruthy();
      expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
      expect(api.getPatient).toHaveBeenCalledTimes(1);
    });

    it("says so when the patient cannot be read again", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(true));
      page();
      await save(user);
      api.getPatient.mockRejectedValue(new Error("down"));
      await user.type(screen.getByLabelText("Heart rate (bpm)"), "120");
      await user.click(screen.getByRole("button", { name: "Record" }));
      expect(await screen.findByText("Recorded. The page could not refresh; reload to see the new score.")).toBeTruthy();
      expect(screen.getByLabelText("Heart rate (bpm)")).toBeTruthy();
      expect(screen.getByText(/Ranked #1 with NEWS2 9\./)).toBeTruthy();
    });

    it("clears the not ranked note on a later ranked save, and labels the old score meanwhile", async () => {
      const user = userEvent.setup();
      api.recordVitals.mockResolvedValue(recorded(false));
      page();
      await save(user);
      const label = "No longer ranked. The figures below are from before this entry.";
      expect(await screen.findByText(label)).toBeTruthy();
      api.recordVitals.mockResolvedValue(recorded(true));
      await user.type(screen.getByLabelText("Heart rate (bpm)"), "120");
      await user.click(screen.getByRole("button", { name: "Record" }));
      await waitFor(() => expect(screen.queryByText(/Still not ranked/)).toBeNull());
      expect(screen.queryByText(label)).toBeNull();
    });

    it("closes the form", async () => {
      const user = userEvent.setup();
      page();
      await loaded();
      await user.click(screen.getByRole("button", { name: "Record vitals" }));
      await user.click(screen.getByRole("button", { name: "Close" }));
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

  it("lists the two model reasons apart, as research with the rank unchanged, and adds no response line", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      score: 2,
      escalation_reasons: [
        { rule: "prediction_model_flag", text: "Prediction model flag (synthetic, not validated)", pathway: null },
        { rule: "alert_policy_flag", text: "Alert policy flag (learned by reward; synthetic, not validated)", pathway: null },
      ],
    } as Detail);
    page();
    await loaded();
    expect(screen.getByText("Prediction model flag (synthetic, not validated)")).toBeTruthy();
    expect(screen.getByText("Alert policy flag (learned by reward; synthetic, not validated)")).toBeTruthy();
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
    // Only model lines: the patient is where NEWS2 puts them, and the page still says so.
    expect(screen.getByText("Sorted on NEWS2 only")).toBeTruthy();
    const label = screen.getByText("Research models \u00b7 rank unchanged");
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
    await user.click(screen.getByRole("button", { name: "Notes" }));
    expect(screen.queryByPlaceholderText("Add a clinical note...")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add note" })).toBeNull();
  });

  it("reads the notes only when the drawer opens, once per open, and lists them", async () => {
    api.getNotes.mockResolvedValue([note("n-001", "BP trending down")]);
    const user = userEvent.setup();
    page("admin");
    await loaded();
    expect(api.getNotes).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByText("BP trending down")).toBeTruthy();
    expect(api.getNotes).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Notes (1)" })); // close
    await user.click(screen.getByRole("button", { name: "Notes (1)" })); // open again: a new read
    expect(api.getNotes).toHaveBeenCalledTimes(2);
  });

  it("says so when the notes cannot be read", async () => {
    api.getNotes.mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    page("admin");
    await loaded();
    await user.click(screen.getByRole("button", { name: "Notes" }));
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
  const bands = () => document.querySelectorAll('[data-testid="own-baseline-band"]');
  const SHADED = "Shaded: this patient's own baseline (synthetic, not validated). Not a normal range.";
  const openCharts = async (role?: SessionRole) => {
    const user = userEvent.setup();
    page(role);
    await loaded();
    await user.click(screen.getByRole("button", { name: "Charts" }));
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

describe("PatientDetail charts title", () => {
  const hist = (...times: string[]) => ({
    heart_rate: times.map((time) => ({ time, value: 80 })),
    respiratory_rate: [],
    spo2: [],
  });
  it.each([
    ["no points", [], "Vital trends"],
    ["one point", ["2026-09-03T01:00:00Z"], "Vital trends \u00b7 under 1 h"],
    ["30 minutes", ["2026-09-03T01:00:00Z", "2026-09-03T01:30:00Z"], "Vital trends \u00b7 under 1 h"],
    ["6 hours", ["2026-09-03T01:00:00Z", "2026-09-03T07:00:00Z"], "Vital trends \u00b7 6 h"],
    ["3 days", ["2026-09-01T01:00:00Z", "2026-09-04T01:00:00Z"], "Vital trends \u00b7 72 h"],
  ])("says the span for %s", async (_name, times, title) => {
    api.getPatient.mockResolvedValue({ ...base, vital_history: hist(...times) });
    const user = userEvent.setup();
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Charts" }));
    expect(screen.getByText(title)).toBeTruthy();
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
    await user.click(screen.getByRole("button", { name: "Notes" }));
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
    expect(await screen.findByText(/Cannot reach the ward server since/)).toBeTruthy();
    expect(screen.queryByText(/could not be loaded/)).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
    api.getPatient.mockResolvedValue(base);
    await advance(5_100);
    await waitFor(() => expect(screen.queryByText(/Cannot reach the ward server since/)).toBeNull());
  });

  const summaryBar = () => document.querySelector('[class*="summaryBar"]') as HTMLElement;

  it("puts the stale lines in the summary bar, beside the score", async () => {
    page();
    await loaded();
    api.getPatient.mockRejectedValue(new Error("down"));
    await advance(2_100);
    const failed = await screen.findByText(/Cannot reach the ward server since/);
    expect(summaryBar().contains(failed)).toBe(true);
    api.getPatient.mockResolvedValue(null);
    await advance(5_100);
    const gone = await screen.findByText(/No longer on your ranked list\. Figures from/);
    expect(summaryBar().contains(gone)).toBe(true);
    // The server answered, so the line that it cannot be reached is no longer true and must go.
    expect(screen.queryByText(/Cannot reach the ward server since/)).toBeNull();
  });

  it("shows the Cannot reach line in an open drawer's header too", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: /Charts/ }));
    api.getPatient.mockRejectedValue(new Error("down"));
    await advance(2_100);
    const overlay = document.querySelector('[class*="overlayHeader"]') as HTMLElement;
    await waitFor(() => expect(within(overlay).getByText(/Cannot reach the ward server since/)).toBeTruthy());
  });

  it("keeps the band across a timed read of the patient", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    api.getBaseline.mockResolvedValue({ ...baseline, vitals: [baseline.vitals[0]] });
    page();
    await loaded();
    await screen.findByRole("region", { name: "Change from own baseline" });
    await user.click(screen.getByRole("button", { name: /Charts/ }));
    const grid = document.querySelector('[class*="chartGrid"]');
    await advance(2_100);
    expect(document.querySelector('[class*="chartGrid"]')).toBe(grid);
    expect(document.querySelectorAll('[data-testid="own-baseline-band"]')).toHaveLength(1);
  });

  it("leaves the open charts drawer alone while the data does not change", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    page();
    await loaded();
    await user.click(screen.getByRole("button", { name: /Charts/ }));
    await advance(900); // the first draw-on (700 ms) has finished
    const grid = document.querySelector('[class*="chartGrid"]') as HTMLElement;
    const before = grid.innerHTML;
    await advance(4_200);
    expect(api.getPatient.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(document.querySelector('[class*="chartGrid"]')).toBe(grid);
    expect(grid.innerHTML).toBe(before);
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
    expect(await screen.findByText("Newer, Pat")).toBeTruthy();
    await act(async () => late({ ...base, name: "Older, Pat" }));
    expect(screen.queryByText("Older, Pat")).toBeNull();
    expect(screen.getByText("Newer, Pat")).toBeTruthy();
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

describe("PatientDetail research models panel", () => {
  it("sits after the own-baseline panel and asks for nothing extra", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const own = await screen.findByRole("region", { name: "Change from own baseline" });
    const models = screen.getByRole("region", { name: "Research models" });
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
    const region = screen.getByRole("region", { name: "Research models" });
    expect(within(region).getAllByText(/Not computed: the patient's own baseline is still forming/)).toHaveLength(2);
    expect(screen.getByText("Research models · rank unchanged")).toBeTruthy();
  });

  it("shows no panel when the server sends no models", async () => {
    const old: Record<string, unknown> = { ...base };
    delete old.models;
    api.getPatient.mockResolvedValue(old);
    page();
    await loaded();
    expect(screen.queryByRole("region", { name: "Research models" })).toBeNull();
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

  it("sits after Current vitals and before Labs", async () => {
    page("rn");
    await loaded();
    const heading = await screen.findByRole("heading", { level: 2, name: "Ask about this patient" });
    const vitals = screen.getByText("Current vitals");
    const labs = screen.getByText(/^Labs/);
    expect(vitals.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.compareDocumentPosition(labs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(api.getPatientQuestions).toHaveBeenCalledWith("p-001");
  });

  it("is absent, with no call, for an admin and in the demo build", async () => {
    page("admin");
    await loaded();
    expect(screen.queryByText("Ask about this patient")).toBeNull();
    cleanup();
    api.hasPatientQuestions = false;
    page("rn");
    await loaded();
    expect(screen.queryByText("Ask about this patient")).toBeNull();
    expect(api.getPatientQuestions).not.toHaveBeenCalled();
    expect(api.getPatientQuestion).not.toHaveBeenCalled();
  });

  it("is not remounted by the page's own refresh: the typed text and the open answer stay", async () => {
    api.usesServer = true;
    api.getLiveWard.mockResolvedValue(null);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      page("rn");
      await loaded();
      await user.click(await screen.findByRole("button", { name: "Show answer" }));
      await user.type(screen.getByLabelText(/Your question/), "typed");
      expect(api.getPatient).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
      expect(api.getPatient.mock.calls.length).toBeGreaterThan(1);
      expect((screen.getByLabelText(/Your question/) as HTMLInputElement).value).toBe("typed");
      expect(screen.getByText("the answer")).toBeTruthy();
      expect(api.getPatientQuestion).toHaveBeenCalledTimes(1);
      expect(api.getPatientQuestions).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
      api.usesServer = false;
    }
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

  it("sits after Current vitals and before Ask about this patient, for an admin too, and reads nothing while closed", async () => {
    api.hasPatientQuestions = true;
    api.getPatientQuestions.mockResolvedValue([]);
    try {
      page("rn");
      await loaded();
      const summary = await screen.findByText("Stay");
      const vitals = screen.getByText("Current vitals");
      const ask = await screen.findByRole("heading", { level: 2, name: "Ask about this patient" });
      expect(vitals.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(summary.compareDocumentPosition(ask) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(api.getPatientHistory).not.toHaveBeenCalled();
      cleanup();
      page("admin");
      await loaded();
      expect(await screen.findByText("Stay")).toBeTruthy();
    } finally {
      api.hasPatientQuestions = false;
    }
  });

  it("is absent, with no call, in the demo build", async () => {
    api.hasHistory = false;
    page("rn");
    await loaded();
    expect(screen.queryByText("Stay")).toBeNull();
    expect(api.getPatientHistory).not.toHaveBeenCalled();
  });

  it("is not remounted by the page's own refresh and causes no read", async () => {
    api.usesServer = true;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      page("rn");
      await loaded();
      await user.click(await screen.findByText("Stay"));
      await screen.findByText(/^Admitted \d/);
      expect(api.getPatientHistory).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100);
      });
      expect(api.getPatient.mock.calls.length).toBeGreaterThan(1);
      expect((screen.getByText("Stay").closest("details") as HTMLDetailsElement).open).toBe(true);
      expect(screen.getByText(/^Admitted \d/)).toBeTruthy();
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
