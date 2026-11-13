// @vitest-environment jsdom
/**
 * The patient page: the score and why, the role's next step, Tier B findings grouped by axis,
 * labs with their freshness, notes (append-only, a failed save keeps the text), the trend
 * charts, and the not-found / load-error states.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import type { BaselineResponse, ClinicalNote, LabPanel, PatientDetail as Detail, Session, SessionRole } from "../types";
import { PatientDetail } from "./PatientDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getPatient: vi.fn(),
  getNotes: vi.fn(),
  getBaseline: vi.fn(),
  addNote: vi.fn(),
  canRecordVitals: false,
  recordVitals: vi.fn(),
}));
vi.mock("../api", () => api);

// p-001 from the committed ward: NEWS2 9 (high), two reasons, eight Tier B findings.
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
      latest_age_s: 60, anchored_at: "admission", baseline_from: "2026-09-03T00:45:00Z", delta: 20, z: 4.1, run_bins: 4, since: "2026-09-03T06:00:00Z",
      significant: true, direction: "rising", pathway: "circulatory", unit: "bpm",
    },
  ],
};

const session = (role: SessionRole = "rn"): Session => ({ clinician_id: "rn-manalo", ward: "W4", role });
const page = (role?: SessionRole, path = "/patient/p-001") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/patient/:id" element={<PatientDetail session={session(role)} />} />
        <Route path="*" element={<PatientDetail session={session(role)} />} />
      </Routes>
    </MemoryRouter>,
  );
const loaded = () => screen.findByRole("heading", { level: 1 });

beforeEach(() => {
  api.getPatient.mockResolvedValue(base);
  api.getNotes.mockResolvedValue([]);
  api.getBaseline.mockResolvedValue(null); // fixture mode
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PatientDetail", () => {
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

  it("groups Tier B findings by axis, with direction, source, detail and pathway", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      escalation_reasons: [{ rule: "tier_b_finding", text: "Perfusion falling", pathway: "perfusion" }],
      tier_b: {
        ...base.tier_b!,
        findings: [
          ...base.tier_b!.findings,
          { id: "qt", label: "QTc", value: "480 ms", direction: "new", source: "device", significant: false, detail: null },
          { id: "st", label: "ST deviation", value: "0.1 mV", direction: null, source: "device", significant: false, detail: null },
        ],
      },
    } as Detail);
    page();
    await loaded();
    for (const group of ["Device axes", "Lab substrate", "Secondary"]) expect(screen.getByText(group)).toBeTruthy();
    expect(screen.getByText("Arrhythmia burden")).toBeTruthy();
    expect(screen.getByText("AF episodes over 6h window")).toBeTruthy();
    expect(screen.getByText("QTc")).toBeTruthy(); // ungrouped
    expect(screen.getByText("ST deviation")).toBeTruthy();
    expect(screen.getByText("(perfusion)", { exact: false })).toBeTruthy();
  });

  it("lists every recorded lab", async () => {
    api.getPatient.mockResolvedValue({ ...base, labs: { ...everyLab, freshness_hours: 2 } });
    page();
    await loaded();
    const labs = screen.getByText(/^Labs/).parentElement!; // Tier B also has a "Lactate" finding
    for (const label of LAB_LABELS) expect(within(labs).getByText(label)).toBeTruthy();
    expect(screen.queryByText("No lab values recorded")).toBeNull();
  });

  it("says plainly what is not there: no reasons, no Tier B, no labs, no age or sex", async () => {
    api.getPatient.mockResolvedValue({
      ...base,
      score: 0,
      escalation_reasons: [],
      tier_b: null,
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

  it("shows the own-baseline panel below the Tier B findings, loaded once with the patient", async () => {
    api.getBaseline.mockResolvedValue(baseline);
    page();
    await loaded();
    const region = await screen.findByRole("region", { name: "Change from own baseline" });
    expect(api.getBaseline).toHaveBeenCalledOnce();
    expect(api.getBaseline).toHaveBeenCalledWith("p-001");
    expect(within(region).getByText(/Heart rate/).closest("li")!.textContent).toContain("from own baseline");
    const tierB = region.parentElement!;
    expect(within(tierB).getByText("Device axes")).toBeTruthy(); // the stored findings stay
    expect(tierB.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_CONTAINED_BY).toBeTruthy();
    expect(screen.queryByText("Own-baseline findings need the live API.")).toBeNull();
  });

  it("shows the panel after the placeholder when there are no stored Tier B findings", async () => {
    api.getPatient.mockResolvedValue({ ...base, tier_b: null } as Detail);
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
    expect(screen.getByText("Device axes")).toBeTruthy();
  });

  it("shows not found for a patient that is missing or not yours", async () => {
    api.getPatient.mockResolvedValue(null);
    page();
    expect(await screen.findByText("Patient not found.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to ward" }).getAttribute("href")).toBe("/ward");
  });

  it("shows an error when the patient cannot be loaded", async () => {
    api.getPatient.mockRejectedValue(new Error("offline"));
    page();
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not be loaded/);
  });

  it("asks for nothing without a patient id", () => {
    const { container } = page(undefined, "/elsewhere");
    expect(api.getPatient).not.toHaveBeenCalled();
    expect(api.getBaseline).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
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
    const title = screen.getByText("Vital trends -- last 6 hours");
    const panel = title.closest("div")!.parentElement!;
    for (const label of ["Heart rate", "Respiratory rate", "SpO2"]) {
      expect(within(panel).getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(within(panel).getByText("Charts show patch data only. Values entered by a nurse are not plotted.")).toBeTruthy();
    await user.click(charts); // same button again closes it
    expect(screen.queryByText("Vital trends -- last 6 hours")).toBeNull();

    await user.click(charts);
    await user.click(screen.getByRole("button", { name: "×" }));
    expect(screen.queryByText("Vital trends -- last 6 hours")).toBeNull();

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
