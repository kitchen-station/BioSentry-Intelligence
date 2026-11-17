// @vitest-environment jsdom
/**
 * The Admit page: admit a patient in four steps, enter labs for a ranked patient chosen first, and read a
 * document whose values are reviewed (low confidence unticked) before anything is saved. Each task sends
 * exactly what was entered (trimmed, optional fields left out) and says what it saved.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AbstainedRow, LabExtractionResult, PatientExtractionResult, Session, WardResponse } from "../types";
import { WardFeedContext, type WardFeed } from "../components/wardFeed";
import { Admit } from "./Admit";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  admitPatient: vi.fn(),
  enterLabResults: vi.fn(),
  extractLabsFromFile: vi.fn(),
  extractPatientFromFile: vi.fn(),
  listAllPatients: vi.fn(),
  canRecordVitals: true,
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

const page = (feed: WardFeed | null = null) =>
  render(
    <MemoryRouter initialEntries={["/admit"]}>
      <WardFeedContext.Provider value={feed}>
        <Admit session={session} onSignOut={() => undefined} />
      </WardFeedContext.Provider>
    </MemoryRouter>,
  );

// A required field's label ends in its * mark (hidden from readers, so the accessible name is the label alone).
const field = (label: string) => screen.getByLabelText((text) => text === label || text === `${label}*`) as HTMLInputElement;
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;
const task = (name: string) => screen.getByRole("radio", { name });
const step = () => within(screen.getByRole("list", { name: "Steps" })).getAllByRole("listitem").find((li) => li.getAttribute("aria-current") === "step")?.textContent;
const errors = () => [...document.querySelectorAll(".hw-field__error")].map((e) => e.textContent);

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

describe("Admit page", () => {
  it("says the patients are simulated, once, and that new patients start NOT RANKED", () => {
    page();
    expect(screen.getAllByText("Simulated patients until a hospital is connected.")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Admit" })).toBeTruthy();
    expect(screen.getByText(/New patients start NOT RANKED/)).toBeTruthy();
  });

  it("opens on admission under the bar's Admit, and switches between the three tasks", async () => {
    const user = userEvent.setup();
    page();
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Admit" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("group", { name: "What are you entering?" })).toBeTruthy();
    expect(screen.getAllByRole("radio").map((r) => r.closest("label")?.textContent)).toEqual(["Admit a patient", "Enter lab results", "Read a document"]);
    expect((task("Admit a patient") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("region", { name: "Admit a patient" })).toBeTruthy();
    await user.type(field("Last name"), "Garcia");

    await user.click(task("Enter lab results"));
    expect(screen.getByRole("region", { name: "Enter lab results" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Admit a patient" })).toBeNull();

    await user.click(task("Read a document"));
    expect(screen.getByRole("region", { name: "Read a document" })).toBeTruthy();

    await user.click(task("Admit a patient"));
    expect(field("Last name").value).toBe(""); // only the open task is kept: switching drops what was typed
  });
});

describe("Admit a patient", () => {
  async function fillIdentity(user: ReturnType<typeof userEvent.setup>) {
    await user.type(field("Last name"), " Garcia ");
    await user.type(field("First name"), " Maria ");
    fireEvent.change(field("Date of birth"), { target: { value: "1980-12-01" } });
    await user.selectOptions(field("Sex"), "F");
    await user.type(field("Bed"), " MW-4B ");
  }
  async function fillAdmission(user: ReturnType<typeof userEvent.setup>) {
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

  it("marks the required fields, and shows their errors only after Next", async () => {
    const user = userEvent.setup();
    page();
    expect(step()).toBe("Identity");
    expect(screen.getByText("Fields marked * are needed to admit.")).toBeTruthy();
    const required = ["Last name", "First name", "Date of birth", "Age", "Sex", "Bed"];
    for (const l of [...required, "Middle name", "Contact number"]) {
      expect(field(l).getAttribute("aria-required")).toBe(required.includes(l) ? "true" : null);
      expect(field(l).closest(".hw-field")?.querySelector(".hw-field__required") !== null).toBe(required.includes(l));
    }
    await user.type(field("Last name"), "x");
    await user.clear(field("Last name"));
    expect(errors()).toEqual([]); // typing and clearing is not an error yet

    await user.click(button("Next"));
    expect(step()).toBe("Identity");
    expect(errors()).toEqual(["Enter the last name.", "Enter the first name.", "Enter the date of birth.", "Enter the age.", "Choose the sex.", "Enter the bed."]);
    expect(field("Last name").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("textbox", { name: "Last name", description: "Enter the last name." })).toBeTruthy();
    expect(document.activeElement).toBe(field("Last name")); // the first gap takes focus
    expect(api.admitPatient).not.toHaveBeenCalled();

    await user.type(field("Last name"), "Garcia");
    expect(field("Last name").hasAttribute("aria-invalid")).toBe(false); // fixed: its error goes at once
    expect(errors()).not.toContain("Enter the last name.");
  });

  it("checks each step on its own Next, and Back keeps every value", async () => {
    const user = userEvent.setup();
    page();
    await fillIdentity(user);
    await user.click(button("Next"));
    expect(step()).toBe("Admission");
    expect(errors()).toEqual([]); // a new step starts clean
    expect(field("Admission route").value).toBe("er_triage");
    expect(field("Attending physician").value).toBe("Dr. Santos");
    await user.type(field("Allergies"), "NKDA");

    await user.click(button("Next"));
    expect(errors()).toEqual(["Enter the chief complaint.", "Enter the admitting diagnosis / context."]);

    await user.click(button("Back"));
    expect(step()).toBe("Identity");
    expect(field("Last name").value).toBe(" Garcia ");
    expect(field("Sex").value).toBe("F");
    expect(field("Age").value).not.toBe("");
    await user.click(button("Next"));
    expect(field("Allergies").value).toBe("NKDA");
    await fillAdmission(user);
    await user.click(button("Next"));
    expect(step()).toBe("Initial assessment");
    expect(screen.queryByText("Fields marked * are needed to admit.")).toBeTruthy();
    expect(button("Review")).toBeTruthy(); // the assessment is optional: straight to the review
    await user.click(button("Review"));
    expect(step()).toBe("Review");
  });

  it("shows every entered value on the review, and Edit goes back to its step", async () => {
    const user = userEvent.setup();
    page();
    await fillIdentity(user);
    await user.type(field("Contact number"), "0917 123 4567");
    await user.click(button("Next"));
    await fillAdmission(user);
    await user.selectOptions(field("Admission route"), "transfer");
    await user.selectOptions(field("Attending physician"), "Dr. Aquino");
    await user.click(button("Next"));
    await user.type(field("Blood pressure (sys/dia)"), "118/76");
    await user.selectOptions(field("Consciousness (ACVPU)"), "C");
    await user.click(button("Review"));

    const pairs = Object.fromEntries([...document.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]));
    expect(pairs).toMatchObject({
      "Last name": "Garcia",
      "First name": "Maria",
      "Middle name": "--",
      "Date of birth": "1980-12-01",
      Sex: "F",
      "Contact number": "0917 123 4567",
      Bed: "MW-4B",
      "Admission route": "Transfer from another facility",
      "Attending physician": "Dr. Aquino",
      "Chief complaint": "Fever",
      "Admitting diagnosis / context": "CAP",
      Allergies: "--",
      "Weight (kg)": "--",
      "Blood pressure (sys/dia)": "118/76",
      "Consciousness (ACVPU)": "New confusion",
    });
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Identity", "Admission", "Initial assessment"]);

    await user.click(button("Edit admission"));
    expect(step()).toBe("Admission");
    expect(field("Chief complaint").value).toBe(" Fever ");
  });

  it("sends only the required fields, trimmed, and lists the new patient NOT RANKED with a way to the ward", async () => {
    const user = userEvent.setup();
    page();
    await fillIdentity(user);
    await user.clear(field("Age"));
    await user.type(field("Age"), "44");
    await user.click(button("Next"));
    await fillAdmission(user);
    await user.click(button("Next"));
    await user.click(button("Review"));
    await user.click(button("Admit patient"));

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
    expect((await screen.findByRole("status")).textContent).toBe(
      "Admitted Garcia, Maria as p-013 -- Bed MW-4B. Status: NOT RANKED until every NEWS2 input is recorded.",
    );
    expect(screen.queryByRole("list", { name: "Steps" })).toBeNull();
    const listed = screen.getByRole("region", { name: "Not ranked (1)" });
    expect(within(listed).getByRole("link", { name: "Garcia, Maria" }).getAttribute("href")).toBe("/patient/p-013");
    expect(within(listed).getByText("No device data.")).toBeTruthy();
    expect(within(listed).queryByRole("button", { name: "Record vitals" })).toBeNull();
    expect(screen.getByRole("link", { name: "Go to the ward list" }).getAttribute("href")).toBe("/ward");

    await user.click(button("Admit another patient"));
    expect(step()).toBe("Identity");
    expect(field("Last name").value).toBe(""); // the form is cleared for the next admission
    expect(errors()).toEqual([]);
  });

  it("includes every optional field that was entered", async () => {
    const user = userEvent.setup();
    page();
    await fillIdentity(user);
    await user.type(field("Middle name"), " Lopez ");
    await user.type(field("Contact number"), " 0917 123 4567 ");
    await user.click(button("Next"));
    await fillAdmission(user);
    await user.selectOptions(field("Admission route"), "transfer");
    await user.selectOptions(field("Attending physician"), "Dr. Aquino");
    await user.type(field("Allergies"), " Penicillin ");
    await user.click(button("Next"));
    await user.type(field("Weight (kg)"), "61.5");
    await user.type(field("Height (cm)"), "158");
    await user.type(field("Blood pressure (sys/dia)"), " 118/76 ");
    await user.selectOptions(field("Consciousness (ACVPU)"), "C");
    await user.click(button("Review"));
    await user.click(button("Admit patient"));

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
    await screen.findByRole("status");
  });

  it("sends once, however often Admit patient is pressed while it saves", async () => {
    let finish!: (r: AbstainedRow) => void;
    api.admitPatient.mockReturnValue(new Promise((r) => (finish = r)));
    const user = userEvent.setup();
    page();
    await fillIdentity(user);
    await user.click(button("Next"));
    await fillAdmission(user);
    await user.click(button("Next"));
    await user.click(button("Review"));
    await user.click(button("Admit patient"));
    expect(button("Admit patient").disabled).toBe(true);
    fireEvent.submit(button("Admit patient").closest("form")!);
    expect(api.admitPatient).toHaveBeenCalledTimes(1);
    finish(admitted());
    await screen.findByRole("status");
  });
});

describe("Enter lab results", () => {
  async function open(user: ReturnType<typeof userEvent.setup>, feed: WardFeed | null = null) {
    page(feed);
    await user.click(task("Enter lab results"));
    await screen.findByRole("option", { name: "Dela Cruz, Juan -- Bed MW-4A (p-001)" });
  }
  // The ward's read, as the bar and the page share it: here only its NOT RANKED rows matter.
  const feed = (abstained: Partial<AbstainedRow>[]): WardFeed => ({
    data: { ward: "W4", ranked: [], abstained, now: "2026-11-17T03:00:00Z" } as unknown as WardResponse,
    fetchedAt: null, failedSince: null, sim: null, refresh: vi.fn(), pollMs: 5_000, readOnly: false,
    alerts: [], acknowledge: vi.fn(), flush: vi.fn(() => Promise.resolve()), notice: null, setNotice: vi.fn(),
  });

  it("asks for the patient first, and lists the NOT RANKED ones as unavailable, with the ward's reason", async () => {
    const user = userEvent.setup();
    await open(user, feed([{ id: "p-009", name: "Reyes, Ana", bed: "MW-4C", reason: "Systolic BP missing" }]));
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["1. Whose results are these?", "2. Results"]);
    expect(field("Patient").getAttribute("aria-required")).toBe("true");
    const notRanked = screen.getByRole("option", { name: "Reyes, Ana -- Bed MW-4C (p-009): Systolic BP missing" }) as HTMLOptionElement;
    expect(notRanked.disabled).toBe(true);
    expect(notRanked.closest("optgroup")?.label).toBe("Not ranked (lab entry once ranked)");
    expect(screen.getByText("INR", { selector: "label" })).toBeTruthy(); // no unit, no brackets
    expect(screen.getByRole("group", { name: "Cardiac biomarkers" })).toBeTruthy();
  });

  it("names a NOT RANKED patient without a reason when the page has no ward read", async () => {
    const user = userEvent.setup();
    await open(user);
    expect((screen.getByRole("option", { name: "Reyes, Ana -- Bed MW-4C (p-009)" }) as HTMLOptionElement).disabled).toBe(true);
  });

  it("omits the not ranked group when every patient is ranked", async () => {
    api.listAllPatients.mockResolvedValue(PATIENTS.slice(0, 1));
    const user = userEvent.setup();
    await open(user);
    expect(document.querySelector("optgroup")).toBeNull();
  });

  it("opens the values once a ranked patient is chosen, then records only the values entered", async () => {
    const user = userEvent.setup();
    await open(user);
    const save = button("Record lab results");
    expect(field("Troponin (ng/mL)").matches(":disabled")).toBe(true); // patient first
    fireEvent.submit(save.closest("form")!);
    expect(api.enterLabResults).not.toHaveBeenCalled();

    await user.selectOptions(field("Patient"), "p-001");
    expect(field("Troponin (ng/mL)").matches(":disabled")).toBe(false);
    expect(save.disabled).toBe(true); // a patient but no value yet
    await user.type(field("Troponin (ng/mL)"), "0.5");
    await user.type(field("Potassium (mEq/L)"), "4.1");
    await user.click(save);

    expect(api.enterLabResults).toHaveBeenCalledWith("p-001", { troponin: 0.5, potassium: 4.1 });
    expect(
      await screen.findByText("Lab results recorded for Dela Cruz, Juan (p-001): Troponin, Potassium."),
    ).toBeTruthy();
    expect(field("Troponin (ng/mL)").value).toBe("");

    await user.click(button("×"));
    expect(screen.queryByText(/Lab results recorded/)).toBeNull();
  });
});

describe("Read a document", () => {
  const pdf = () => new File(["x".repeat(2048)], "cbc.pdf", { type: "application/pdf" });
  const zone = () => screen.getByLabelText(/Drop a document here/) as HTMLInputElement;
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
    await user.click(task("Read a document"));
    expect(screen.getByText(/Document reading is simulated/)).toBeTruthy();
    await screen.findByRole("option", { name: "Dela Cruz, Juan -- Bed MW-4A (p-001)" });
  }

  it("reads a lab document for a ranked patient, starts a low-confidence value unticked, then saves the ticked ones", async () => {
    let finish!: (r: LabExtractionResult) => void;
    api.extractLabsFromFile.mockReturnValue(new Promise((r) => (finish = r)));
    const user = userEvent.setup();
    await open(user);
    expect((screen.getByRole("option", { name: /Reyes, Ana/ }) as HTMLOptionElement).disabled).toBe(true); // ranked only
    const read = button("Read document");
    expect(read.disabled).toBe(true);

    await user.selectOptions(field("Patient"), "p-001");
    const file = pdf();
    await user.upload(zone(), file);
    expect(screen.getByText("cbc.pdf")).toBeTruthy();
    expect(screen.getByText("2.0 KB")).toBeTruthy();

    await user.click(read);
    expect(api.extractLabsFromFile).toHaveBeenCalledWith(file);
    expect(button("Reading document...")).toBeTruthy();
    finish(labRead);

    await screen.findByText("Values read from document -- review and correct");
    expect(screen.getByText(/Anything read with low confidence starts unticked/)).toBeTruthy();
    const values = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(values.map((i) => i.labels?.[0].textContent)).toEqual([
      "Troponin (ng/mL)",
      "Hemoglobin (g/L)",
      "lactate", // a key the form does not know is shown as is; an absent value has no row
    ]);
    // The reading's confidence describes its value; a low one says so and starts unticked, its value closed.
    expect(screen.getByRole("spinbutton", { name: "Troponin (ng/mL)", description: "92%" })).toBeTruthy();
    expect(screen.queryByRole("spinbutton", { name: "Hemoglobin (g/L)", description: /%/ })).toBeNull(); // none given
    expect(screen.getByRole("spinbutton", { name: "lactate", description: "50% · Low confidence: check it" })).toBeTruthy();
    const keep = (name: string) => screen.getByRole("checkbox", { name: `Save ${name}` }) as HTMLInputElement;
    expect([keep("Troponin (ng/mL)").checked, keep("Hemoglobin (g/L)").checked, keep("lactate").checked]).toEqual([true, true, false]);
    expect(field("lactate").disabled).toBe(true);

    await user.click(keep("lactate")); // ticked: open to check
    expect(field("lactate").disabled).toBe(false);
    await user.click(keep("lactate")); // and left out again
    const hb = field("Hemoglobin (g/L)");
    await user.clear(hb);
    await user.type(hb, "131");
    await user.click(button("Confirm and save"));

    expect(api.enterLabResults).toHaveBeenCalledWith("p-001", { troponin: 0.04, hemoglobin: 131 });
    expect(
      await screen.findByText("Lab results recorded for Dela Cruz, Juan (p-001): troponin, hemoglobin."),
    ).toBeTruthy();

    await user.click(button("Upload another"));
    expect(screen.queryByText(/Lab results recorded/)).toBeNull();
    expect(button("Read document")).toBeTruthy();
    expect(screen.queryByText("cbc.pdf")).toBeNull();
  });

  it("cannot save a lab document with every value unticked, and can cancel", async () => {
    api.extractLabsFromFile.mockResolvedValue(labRead);
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Patient"), "p-001");
    await user.upload(zone(), pdf());
    await user.click(button("Read document"));
    await screen.findByText("Values read from document -- review and correct");
    for (const box of screen.getAllByRole("checkbox") as HTMLInputElement[]) if (box.checked) await user.click(box);
    expect(button("Confirm and save").disabled).toBe(true);
    await user.click(button("Cancel"));

    expect(api.enterLabResults).not.toHaveBeenCalled();
    expect(button("Read document")).toBeTruthy();
  });

  it("takes a file dropped on the drop zone", async () => {
    const user = userEvent.setup();
    await open(user);
    const drop = zone().closest("label")!;
    fireEvent.dragOver(drop);
    expect(drop.hasAttribute("data-dragover")).toBe(true);
    fireEvent.dragLeave(drop);
    expect(drop.hasAttribute("data-dragover")).toBe(false);
    fireEvent.dragOver(drop);
    fireEvent.drop(drop, { dataTransfer: { files: [pdf()] } });
    expect(drop.hasAttribute("data-dragover")).toBe(false);
    expect(screen.getByText("cbc.pdf")).toBeTruthy();
    fireEvent.drop(drop, { dataTransfer: { files: [] } });
    expect(screen.queryByText("cbc.pdf")).toBeNull();
  });

  it("reads a patient record without a patient, and admits from the ticked values", async () => {
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
        { lastName: 0.874, allergies: 0.6 },
      ),
    );
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Document type"), "patient");
    expect(screen.queryByText("Patient", { selector: "label" })).toBeNull();
    const file = pdf();
    await user.upload(zone(), file);
    await user.click(button("Read document"));
    expect(api.extractPatientFromFile).toHaveBeenCalledWith(file);

    await screen.findByText("Values read from document -- review and correct");
    expect(screen.getByText("87%")).toBeTruthy();
    expect(screen.getByText("60% · Low confidence: check it")).toBeTruthy();
    expect(screen.getByText("civilStatus")).toBeTruthy(); // unknown key shown as is
    expect(screen.queryByText("weightKg")).toBeNull();
    const bed = field("Bed");
    await user.clear(bed);
    await user.type(bed, "MW-4D");
    await user.click(button("Confirm and admit"));

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
      // allergies read with low confidence and left unticked: not sent
    });
    expect(await screen.findByText(/Admitted Garcia, Maria as p-013/)).toBeTruthy();

    await user.click(button("×")); // dismissing starts over
    expect(button("Read document")).toBeTruthy();
  });

  it("fills placeholders for whatever the record did not give", async () => {
    api.extractPatientFromFile.mockResolvedValue(patientRead({ sex: "X", admissionRoute: "walk_in" }));
    const user = userEvent.setup();
    await open(user);
    await user.selectOptions(field("Document type"), "patient");
    await user.upload(zone(), pdf());
    await user.click(button("Read document"));
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
    await user.upload(zone(), pdf());
    await user.click(button("Read document"));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(api.admitPatient).not.toHaveBeenCalled();

    await user.upload(zone(), pdf());
    await user.click(button("Read document"));
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
    await user.click(task("Read a document"));
    await user.upload(zone(), pdf());
    expect(button("Read document").disabled).toBe(true);
  });
});
