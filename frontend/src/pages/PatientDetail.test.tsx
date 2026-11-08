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
import type { ClinicalNote, LabPanel, PatientDetail as Detail, Session, SessionRole } from "../types";
import { PatientDetail } from "./PatientDetail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  getPatient: vi.fn(),
  getNotes: vi.fn(),
  addNote: vi.fn(),
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
    expect(screen.getByText("not yet implemented")).toBeTruthy();
    expect(screen.getByText("No lab values recorded")).toBeTruthy();
    expect(screen.getByText(/^-- -- · Bed/)).toBeTruthy();
    expect(screen.queryByText(/since collection/)).toBeNull();
    expect(screen.getByText("Signal 80%")).toBeTruthy(); // the strip
    expect(screen.getByText("80%")).toBeTruthy(); // the summary block
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
});
