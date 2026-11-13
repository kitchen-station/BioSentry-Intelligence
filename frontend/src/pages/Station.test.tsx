// @vitest-environment jsdom
/**
 * The Station page: admit a patient, enter labs for a ranked patient, and upload a document
 * whose read values are reviewed before anything is saved. Each form sends exactly what was
 * entered (trimmed, optional fields left out) and says what it saved.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AbstainedRow, LabExtractionResult, PatientExtractionResult, Session } from "../types";
import { Station } from "./Station";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  admitPatient: vi.fn(),
  enterLabResults: vi.fn(),
  extractLabsFromFile: vi.fn(),
  extractPatientFromFile: vi.fn(),
  listAllPatients: vi.fn(),
  getStays: vi.fn(),
  usesServer: false,
}));
vi.mock("../api", () => api);

const session: Session = { clinician_id: "md-santos", ward: "W4", role: "md" };
const PATIENTS = [
  { id: "p-001", name: "Dela Cruz, Juan", bed: "MW-4A", ranked: true },
  { id: "p-009", name: "Reyes, Ana", bed: "MW-4C", ranked: false },
];
const admitted = (over: Partial<AbstainedRow> = {}): AbstainedRow => ({
  id: "p-013",
  name: "Garcia, Maria",
  bed: "MW-4B",
  age: 45,
  sex: "F",
  admitting_context: "CAP",
  reason: "No device data",
  instruction: "Apply the patch and verify the signal",
  synthetic: true,
  ...over,
});

const page = () =>
  render(
    <MemoryRouter>
      <Station session={session} onSignOut={() => undefined} />
    </MemoryRouter>,
  );

// The form labels are not tied to their controls, so a control is the label's next sibling.
const field = (label: string) =>
  screen.getByText(label, { selector: "label" }).nextElementSibling as HTMLInputElement;
const submit = () => document.querySelector('button[type="submit"]') as HTMLButtonElement;

beforeEach(() => {
  api.listAllPatients.mockResolvedValue(PATIENTS);
  api.enterLabResults.mockResolvedValue({});
  api.admitPatient.mockResolvedValue(admitted());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("Station tabs", () => {
  it("says the patients are simulated, once", () => {
    page();
    expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
  });

  it("opens patient flow", async () => {
    api.getStays.mockResolvedValue([]);
    page();
    fireEvent.click(screen.getByRole("button", { name: "Patient flow" }));
    expect(await screen.findByText("No arrivals or departures yet.")).toBeTruthy();
  });

  it("opens on admission, names the ward, and switches between the three sections", async () => {
    const user = userEvent.setup();
    page();
    expect(screen.getByText(/Station · Ward W4/)).toBeTruthy();
    expect(screen.getByText(/New patients start NOT RANKED/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Enter lab results" }));
    expect(screen.getByText(/Select a ranked patient/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText("Upload document")).toBeTruthy();

    await user.click(screen.getAllByRole("button", { name: "Admit patient" })[0]);
    expect(screen.getByText(/New patients start NOT RANKED/)).toBeTruthy();
  });
});

describe("Admit patient", () => {
  async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
    await user.type(field("Last name"), " Garcia ");
    await user.type(field("First name"), " Maria ");
    fireEvent.change(field("Date of birth"), { target: { value: "1980-12-01" } });
    await user.selectOptions(field("Sex"), "F");
    await user.type(field("Bed"), " MW-4B ");
    await user.type(field("Chief complaint"), " Fever ");
    await user.type(field("Admitting diagnosis / context"), " CAP ");
  }

  it("works out the age from the date of birth, counting a birthday not yet reached", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-09T08:00:00"));
    page();
    fireEvent.change(field("Date of birth"), { target: { value: "1980-12-01" } });
    expect(field("Age").value).toBe("45"); // birthday in December: still 45
    fireEvent.change(field("Date of birth"), { target: { value: "1980-11-09" } });
    expect(field("Age").value).toBe("46"); // birthday today
    fireEvent.change(field("Date of birth"), { target: { value: "" } });
    expect(field("Age").value).toBe("46"); // clearing the date leaves the age as typed
  });

  it("stays disabled and sends nothing until every required field is filled", async () => {
    const user = userEvent.setup();
    page();
    expect(submit().disabled).toBe(true);
    fireEvent.submit(submit().closest("form")!);
    expect(api.admitPatient).not.toHaveBeenCalled();

    await fillRequired(user);
    expect(submit().disabled).toBe(false);
  });

  it("sends only the required fields, trimmed, and reports the NOT RANKED admission", async () => {
    const user = userEvent.setup();
    page();
    await fillRequired(user);
    await user.clear(field("Age"));
    await user.type(field("Age"), "44");
    await user.click(submit());

    expect(api.admitPatient).toHaveBeenCalledWith({
      lastName: "Garcia",
      firstName: "Maria",
      dateOfBirth: "1980-12-01",
      age: 44,
      sex: "F",
      bed: "MW-4B",
      admissionRoute: "er_triage",
      chiefComplaint: "Fever",
      admittingContext: "CAP",
      attendingPhysician: "Dr. Santos",
    });
    expect(
      await screen.findByText(
        "Admitted Garcia, Maria as p-013 -- Bed MW-4B. Status: NOT RANKED until every NEWS2 input is recorded.",
      ),
    ).toBeTruthy();
    expect(field("Last name").value).toBe(""); // the form is cleared for the next admission
    expect(field("Age").value).toBe("");
    expect(submit().disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "×" }));
    expect(screen.queryByText(/Admitted Garcia/)).toBeNull();
  });

  it("includes every optional field that was entered", async () => {
    const user = userEvent.setup();
    page();
    await fillRequired(user);
    await user.type(field("Middle name"), " Lopez ");
    await user.type(field("Contact number"), " 0917 123 4567 ");
    await user.selectOptions(field("Admission route"), "transfer");
    await user.selectOptions(field("Attending physician"), "Dr. Aquino");
    await user.type(field("Allergies"), " Penicillin ");
    await user.type(field("Weight (kg)"), "61.5");
    await user.type(field("Height (cm)"), "158");
    await user.type(field("Blood pressure (sys/dia)"), " 118/76 ");
    await user.selectOptions(field("Consciousness (ACVPU)"), "C");
    await user.click(submit());

    expect(api.admitPatient).toHaveBeenCalledWith(
      expect.objectContaining({
        middleName: "Lopez",
        contactNumber: "0917 123 4567",
        admissionRoute: "transfer",
        attendingPhysician: "Dr. Aquino",
        allergies: "Penicillin",
        weightKg: 61.5,
        heightCm: 158,
        initialBp: "118/76",
        initialAvpu: "C",
      }),
    );
    await screen.findByText(/Admitted Garcia, Maria/);
  });
});

describe("Enter lab results", () => {
  async function open(user: ReturnType<typeof userEvent.setup>) {
    page();
    await user.click(screen.getByRole("button", { name: "Enter lab results" }));
    await screen.findByRole("option", { name: "Dela Cruz, Juan -- Bed MW-4A (p-001)" });
  }

  it("offers ranked patients and lists the not ranked ones as unavailable", async () => {
    const user = userEvent.setup();
    await open(user);
    const notRanked = screen.getByRole("option", { name: "Reyes, Ana -- Bed MW-4C (p-009)" });
    expect((notRanked as HTMLOptionElement).disabled).toBe(true);
    expect(notRanked.closest("optgroup")?.label).toBe("Not ranked (lab entry once ranked)");
    expect(screen.getByText("INR", { selector: "label" })).toBeTruthy(); // no unit, no brackets
    expect(screen.getByText("Troponin (ng/mL)", { selector: "label" })).toBeTruthy();
  });

  it("omits the not ranked group when every patient is ranked", async () => {
    api.listAllPatients.mockResolvedValue(PATIENTS.slice(0, 1));
    const user = userEvent.setup();
    await open(user);
    expect(document.querySelector("optgroup")).toBeNull();
  });

  it("needs a patient and at least one value, then records only the values entered", async () => {
    const user = userEvent.setup();
    await open(user);
    fireEvent.submit(submit().closest("form")!);
    expect(api.enterLabResults).not.toHaveBeenCalled();

    await user.selectOptions(field("Patient"), "p-001");
    expect(submit().disabled).toBe(true); // a patient but no value yet
    await user.type(field("Troponin (ng/mL)"), "0.5");
    await user.type(field("Potassium (mEq/L)"), "4.1");
    await user.click(submit());

    expect(api.enterLabResults).toHaveBeenCalledWith("p-001", { troponin: 0.5, potassium: 4.1 });
    expect(
      await screen.findByText("Lab results recorded for Dela Cruz, Juan (p-001): Troponin, Potassium."),
    ).toBeTruthy();
    expect(field("Troponin (ng/mL)").value).toBe("");

    await user.click(screen.getByRole("button", { name: "×" }));
    expect(screen.queryByText(/Lab results recorded/)).toBeNull();
  });
});

describe("Upload", () => {
  const pdf = () => new File(["x".repeat(2048)], "cbc.pdf", { type: "application/pdf" });
  const labRead: LabExtractionResult = {
    values: { troponin: 0.04, hemoglobin: 128, sodium: undefined, lactate: 2.1 } as LabExtractionResult["values"],
    confidence: { troponin: 0.92, lactate: 0.5 },
    model_id: "placeholder",
    synthetic: true,
  };
  const patientRead = (values: Record<string, unknown>, confidence = {}): PatientExtractionResult => ({
    values: values as PatientExtractionResult["values"],
    confidence,
    model_id: "placeholder",
    synthetic: true,
  });

  async function open(user: ReturnType<typeof userEvent.setup>) {
    page();
    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(screen.getByText(/Document reading is simulated/)).toBeTruthy();
    await screen.findByRole("option", { name: "Dela Cruz, Juan -- Bed MW-4A (p-001)" });
  }

  it("reads a lab document for a ranked patient, lets each value be corrected, then saves", async () => {
    let finish!: (r: LabExtractionResult) => void;
    api.extractLabsFromFile.mockReturnValue(new Promise((r) => (finish = r)));
    const user = userEvent.setup();
    await open(user);
    expect(screen.queryByRole("option", { name: /Reyes, Ana/ })).toBeNull(); // ranked only
    const read = screen.getByRole("button", { name: "Read document" });
    expect((read as HTMLButtonElement).disabled).toBe(true);

    await user.selectOptions(field("Patient"), "p-001");
    const file = pdf();
    await user.upload(field("File"), file);
    expect(screen.getByText("cbc.pdf")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();

    await user.click(read);
    expect(api.extractLabsFromFile).toHaveBeenCalledWith(file);
    expect(screen.getByRole("button", { name: "Reading document..." })).toBeTruthy();
    finish(labRead);

    await screen.findByText("Values read from document -- review and correct");
    const rows = screen.getAllByRole("spinbutton").map((i) => i.parentElement!);
    expect(rows.map((r) => within(r).getAllByText(/./)[0].textContent)).toEqual([
      "Troponin (ng/mL)",
      "Hemoglobin (g/L)",
      "lactate", // a key the form does not know is shown as is; an absent value has no row
    ]);
    expect(within(rows[0]).getByText("92%")).toBeTruthy();
    expect(within(rows[1]).queryByText(/%/)).toBeNull(); // no confidence given

    const hb = within(rows[1]).getByRole("spinbutton");
    await user.clear(hb);
    await user.type(hb, "131");
    await user.clear(within(rows[2]).getByRole("spinbutton")); // emptied: not saved
    await user.click(screen.getByRole("button", { name: "Confirm and save" }));

    expect(api.enterLabResults).toHaveBeenCalledWith("p-001", { troponin: 0.04, hemoglobin: 131 });
    expect(
      await screen.findByText("Lab results recorded for Dela Cruz, Juan (p-001): troponin, hemoglobin."),
    ).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Upload another" }));
    expect(screen.queryByText(/Lab results recorded/)).toBeNull();
    expect(screen.getByRole("button", { name: "Read document" })).toBeTruthy();
    expect(screen.queryByText("cbc.pdf")).toBeNull();
  });

  it("can cancel a lab review without saving", async () => {
    api.extractLabsFromFile.mockResolvedValue(labRead);
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Patient"), "p-001");
    await user.upload(field("File"), pdf());
    await user.click(screen.getByRole("button", { name: "Read document" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(api.enterLabResults).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Read document" })).toBeTruthy();
  });

  it("reads a patient record without a patient, and admits from the reviewed values", async () => {
    api.extractPatientFromFile.mockResolvedValue(
      patientRead(
        {
          lastName: " Garcia ",
          firstName: "Maria",
          middleName: " Lopez ",
          dateOfBirth: "1980-12-01",
          age: 45,
          sex: "F",
          contactNumber: " 0917 ",
          bed: "MW-4B",
          admissionRoute: "transfer",
          chiefComplaint: "Fever",
          admittingContext: "CAP",
          attendingPhysician: "Dr. Reyes",
          allergies: " NKDA ",
          civilStatus: "single",
          weightKg: undefined,
        },
        { lastName: 0.874 },
      ),
    );
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Document type"), "patient");
    expect(screen.queryByText("Patient", { selector: "label" })).toBeNull();
    const file = pdf();
    await user.upload(field("File"), file);
    await user.click(screen.getByRole("button", { name: "Read document" }));
    expect(api.extractPatientFromFile).toHaveBeenCalledWith(file);

    await screen.findByText("Values read from document -- review and correct");
    expect(screen.getByText("87%")).toBeTruthy();
    expect(screen.getByText("civilStatus")).toBeTruthy(); // unknown key shown as is
    expect(screen.queryByText("weightKg")).toBeNull();
    const bed = screen.getByText("Bed").parentElement!.querySelector("input")!;
    await user.clear(bed);
    await user.type(bed, "MW-4D");
    await user.click(screen.getByRole("button", { name: "Confirm and admit" }));

    expect(api.admitPatient).toHaveBeenCalledWith({
      lastName: "Garcia",
      firstName: "Maria",
      middleName: "Lopez",
      dateOfBirth: "1980-12-01",
      age: 45,
      sex: "F",
      contactNumber: "0917",
      bed: "MW-4D",
      admissionRoute: "transfer",
      chiefComplaint: "Fever",
      admittingContext: "CAP",
      attendingPhysician: "Dr. Reyes",
      allergies: "NKDA",
    });
    expect(await screen.findByText(/Admitted Garcia, Maria as p-013/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "×" })); // dismissing starts over
    expect(screen.getByRole("button", { name: "Read document" })).toBeTruthy();
  });

  it("fills placeholders for whatever the record did not give", async () => {
    api.extractPatientFromFile.mockResolvedValue(patientRead({ sex: "X", admissionRoute: "walk_in" }));
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Document type"), "patient");
    await user.upload(field("File"), pdf());
    await user.click(screen.getByRole("button", { name: "Read document" }));
    await user.click(await screen.findByRole("button", { name: "Confirm and admit" }));

    expect(api.admitPatient).toHaveBeenCalledWith({
      lastName: "Unknown",
      firstName: "Unknown",
      dateOfBirth: "1970-01-01",
      age: 0,
      sex: "M",
      bed: "TBD",
      admissionRoute: "er_triage",
      chiefComplaint: "See uploaded document",
      admittingContext: "Extracted from uploaded document",
      attendingPhysician: "Dr. Santos",
    });
    await screen.findByText(/Admitted Garcia, Maria/);
  });

  it("keeps a direct ward admission, and can cancel a patient review", async () => {
    api.extractPatientFromFile.mockResolvedValue(patientRead({ admissionRoute: "direct_admission" }));
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Document type"), "patient");
    await user.upload(field("File"), pdf());
    await user.click(screen.getByRole("button", { name: "Read document" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(api.admitPatient).not.toHaveBeenCalled();

    await user.upload(field("File"), pdf());
    await user.click(screen.getByRole("button", { name: "Read document" }));
    await user.click(await screen.findByRole("button", { name: "Confirm and admit" }));
    await waitFor(() =>
      expect(api.admitPatient).toHaveBeenCalledWith(
        expect.objectContaining({ admissionRoute: "direct_admission" }),
      ),
    );
  });

  it("does not read a lab document for a patient who is not ranked yet", async () => {
    api.listAllPatients.mockResolvedValue([{ ...PATIENTS[0], ranked: false }]);
    const user = userEvent.setup();
    page();
    await user.click(screen.getByRole("button", { name: "Upload" }));
    await user.upload(field("File"), pdf());
    expect((screen.getByRole("button", { name: "Read document" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
