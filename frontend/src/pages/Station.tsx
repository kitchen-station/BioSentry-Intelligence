import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import type { Session, AdmitPatientInput, LabExtractionResult, PatientExtractionResult } from "../types";
import { admitPatient, enterLabResults, extractLabsFromFile, extractPatientFromFile, listAllPatients } from "../api";
import { AppShell } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { FlowSection } from "./Flow";
import styles from "./Station.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

const PHYSICIANS = [
  "Dr. Santos",
  "Dr. Reyes",
  "Dr. Aquino",
  "Dr. Villanueva",
];

type Section = "admit" | "labs" | "upload" | "flow";

const TABS: Array<{ id: Section; label: string }> = [
  { id: "admit", label: "Admit patient" },
  { id: "labs", label: "Enter lab results" },
  { id: "upload", label: "Upload" },
  { id: "flow", label: "Patient flow" },
];

export function Station({ session, onSignOut }: Props) {
  const [section, setSection] = useState<Section>("admit");

  // On a phone the tab list scrolls sideways: keep the selected tab in view.
  useEffect(() => {
    document.getElementById(`station-tab-${section}`)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [section]);

  // WAI-ARIA tabs, automatic activation: arrows, Home and End select a tab and move focus to it.
  // Only the selected panel is mounted, so switching drops whatever was typed in the other.
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === section);
    const to = ({ ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: -1 } as Record<string, number>)[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const n = (to + TABS.length) % TABS.length;
    setSection(TABS[n].id);
    (e.currentTarget.children[n] as HTMLElement).focus();
  };

  return (
    <AppShell session={session} onSignOut={onSignOut} title={`Station · Ward ${session.ward}`}>
      <SimulatedNote />
      <div className={`hw-tabs ${styles.container}`}>
        <div className="hw-tabs__list" role="tablist" aria-label="Station" onKeyDown={onTabKey}>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`station-tab-${t.id}`}
              className="hw-tabs__tab"
              aria-selected={section === t.id}
              aria-controls="station-panel"
              tabIndex={section === t.id ? 0 : -1}
              onClick={() => setSection(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="hw-tabs__panel" role="tabpanel" id="station-panel" aria-labelledby={`station-tab-${section}`}>
          {section === "admit" && <AdmitForm />}
          {section === "labs" && <LabForm />}
          {section === "upload" && <UploadForm />}
          {section === "flow" && <FlowSection />}
        </div>
      </div>
    </AppShell>
  );
}

// --- Kit fields: every label names its control ---

type FieldProps = { label: string; wide?: boolean };

function InputField({ label, wide, ...input }: FieldProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className={`hw-field hw-field--full ${wide ? styles.wide : ""}`}>
      <label className="hw-field__label" htmlFor={id}>{label}</label>
      <div className="hw-input"><input id={id} className="hw-input__control" {...input} /></div>
    </div>
  );
}

function SelectField({ label, wide, ...select }: FieldProps & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <div className={`hw-field hw-field--full ${wide ? styles.wide : ""}`}>
      <label className="hw-field__label" htmlFor={id}>{label}</label>
      <div className="hw-input"><select id={id} className="hw-input__control" {...select} /></div>
    </div>
  );
}

/** One titled group of fields. */
function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="hw-card hw-card--outlined">
      <h2 className={`hw-card__title ${styles.cardTitle}`}>{title}</h2>
      <div className={styles.fields}>{children}</div>
    </section>
  );
}

/** What was saved. No role, as before: the page does not announce it. */
function Saved({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <div className={`hw-alert hw-alert--success ${styles.banner}`}>
      <span className="hw-alert__icon hw-icon fa-solid fa-circle-check" aria-hidden="true" />
      <span className="hw-alert__body">{text}</span>
      <button type="button" className="hw-close" onClick={onDismiss}>&times;</button>
    </div>
  );
}

/** A panel's own title and lead line. */
function Head({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={styles.head}>
      <h1 className="hw-heading hw-heading--4">{title}</h1>
      {children}
    </div>
  );
}

// --- Admit Patient Form ---

function AdmitForm() {
  const [lastName, setLastName] = useState("");
  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [dob, setDob] = useState("");
  const [age, setAge] = useState("");
  const [sex, setSex] = useState<"M" | "F" | "">("");
  const [contactNumber, setContactNumber] = useState("");
  const [bed, setBed] = useState("");
  const [route, setRoute] = useState<"er_triage" | "direct_admission" | "transfer">("er_triage");
  const [chiefComplaint, setChiefComplaint] = useState("");
  const [context, setContext] = useState("");
  const [allergies, setAllergies] = useState("");
  const [physician, setPhysician] = useState(PHYSICIANS[0]);
  const [weightKg, setWeightKg] = useState("");
  const [heightCm, setHeightCm] = useState("");
  const [bp, setBp] = useState("");
  const [avpu, setAvpu] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const contextId = useId();

  const canSubmit = lastName.trim() && firstName.trim() && dob && age && sex && bed.trim() && chiefComplaint.trim() && context.trim();

  const handleDobChange = (value: string) => {
    setDob(value);
    if (value) {
      const birth = new Date(value);
      const today = new Date();
      let years = today.getFullYear() - birth.getFullYear();
      const m = today.getMonth() - birth.getMonth();
      if (m < 0 || (m === 0 && today.getDate() < birth.getDate())) years--;
      setAge(String(Math.max(0, years)));
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || submitting) return;
    setSubmitting(true);

    const input: AdmitPatientInput = {
      lastName: lastName.trim(),
      firstName: firstName.trim(),
      ...(middleName.trim() ? { middleName: middleName.trim() } : {}),
      dateOfBirth: dob,
      age: parseInt(age, 10),
      sex: sex as "M" | "F",
      ...(contactNumber.trim() ? { contactNumber: contactNumber.trim() } : {}),
      bed: bed.trim(),
      admissionRoute: route,
      chiefComplaint: chiefComplaint.trim(),
      admittingContext: context.trim(),
      attendingPhysician: physician,
      ...(allergies.trim() ? { allergies: allergies.trim() } : {}),
      ...(weightKg ? { weightKg: parseFloat(weightKg) } : {}),
      ...(heightCm ? { heightCm: parseFloat(heightCm) } : {}),
      ...(bp.trim() ? { initialBp: bp.trim() } : {}),
      ...(avpu ? { initialAvpu: avpu as "A" | "C" | "V" | "P" | "U" } : {}),
    };

    const row = await admitPatient(input);
    setResult(`Admitted ${row.name} as ${row.id} -- Bed ${row.bed}. Status: NOT RANKED until every NEWS2 input is recorded.`);
    setLastName(""); setFirstName(""); setMiddleName("");
    setDob(""); setAge(""); setSex(""); setContactNumber("");
    setBed(""); setChiefComplaint(""); setContext(""); setAllergies("");
    setWeightKg(""); setHeightCm(""); setBp(""); setAvpu("");
    setSubmitting(false);
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <Head title="Admit patient">
        <p className="hw-text-muted">
          New patients start NOT RANKED. This is a synthetic ward: never enter a real patient's details.
        </p>
      </Head>

      {result && <Saved text={result} onDismiss={() => setResult(null)} />}

      <div className={styles.cards}>
        <Card title="Patient identity">
          <InputField label="Last name" value={lastName} onChange={(e) => setLastName(e.target.value)} required />
          <InputField label="First name" value={firstName} onChange={(e) => setFirstName(e.target.value)} required />
          <InputField label="Middle name" value={middleName} onChange={(e) => setMiddleName(e.target.value)} />
          <InputField label="Date of birth" type="date" value={dob} onChange={(e) => handleDobChange(e.target.value)} required />
          <InputField label="Age" type="number" min={0} max={150} value={age} onChange={(e) => setAge(e.target.value)} required />
          <SelectField label="Sex" value={sex} onChange={(e) => setSex(e.target.value as "M" | "F" | "")} required>
            <option value="">--</option>
            <option value="M">M</option>
            <option value="F">F</option>
          </SelectField>
          <InputField label="Contact number" type="tel" placeholder="09XX XXX XXXX" value={contactNumber} onChange={(e) => setContactNumber(e.target.value)} />
          <InputField label="Bed" placeholder="e.g. MW-4A" value={bed} onChange={(e) => setBed(e.target.value)} required />
        </Card>

        <Card title="Admission details">
          <SelectField label="Admission route" value={route} onChange={(e) => setRoute(e.target.value as "er_triage" | "direct_admission" | "transfer")}>
            <option value="er_triage">ER Triage</option>
            <option value="direct_admission">Direct Ward Admission</option>
            <option value="transfer">Transfer from another facility</option>
          </SelectField>
          <SelectField label="Attending physician" value={physician} onChange={(e) => setPhysician(e.target.value)}>
            {PHYSICIANS.map((p) => <option key={p} value={p}>{p}</option>)}
          </SelectField>
          <InputField wide label="Chief complaint" placeholder="e.g. Chest pain, onset 2 hours ago" value={chiefComplaint} onChange={(e) => setChiefComplaint(e.target.value)} required />
          <div className={`hw-field hw-field--full ${styles.wide}`}>
            <label className="hw-field__label" htmlFor={contextId}>Admitting diagnosis / context</label>
            <div className="hw-textarea">
              <textarea id={contextId} className="hw-textarea__control" rows={3} value={context} onChange={(e) => setContext(e.target.value)} required />
            </div>
          </div>
          <InputField wide label="Allergies" placeholder="NKDA or list allergies" value={allergies} onChange={(e) => setAllergies(e.target.value)} />
        </Card>

        <Card title="Initial assessment (nurse-entered)">
          <InputField label="Weight (kg)" type="number" step="0.1" min={0} value={weightKg} onChange={(e) => setWeightKg(e.target.value)} />
          <InputField label="Height (cm)" type="number" step="0.1" min={0} value={heightCm} onChange={(e) => setHeightCm(e.target.value)} />
          <InputField label="Blood pressure (sys/dia)" placeholder="e.g. 120/80" value={bp} onChange={(e) => setBp(e.target.value)} />
          <SelectField label="Consciousness (ACVPU)" value={avpu} onChange={(e) => setAvpu(e.target.value)}>
            <option value="">--</option>
            <option value="A">Alert</option>
            <option value="C">New confusion</option>
            <option value="V">Verbal</option>
            <option value="P">Pain</option>
            <option value="U">Unresponsive</option>
          </SelectField>
        </Card>
      </div>

      <div className={styles.actions}>
        <button type="submit" className="hw-button hw-button--primary" disabled={!canSubmit || submitting}>
          Admit patient
        </button>
      </div>
    </form>
  );
}

// --- Enter Lab Results Form ---

// Lab field config: key, label, unit
const LAB_FIELDS: Array<{ key: string; label: string; unit: string; section: string }> = [
  { key: "troponin", label: "Troponin", unit: "ng/mL", section: "Cardiac biomarkers" },
  { key: "ck_mb", label: "CK-MB", unit: "U/L", section: "Cardiac biomarkers" },
  { key: "bnp", label: "BNP", unit: "pg/mL", section: "Cardiac biomarkers" },
  { key: "nt_probnp", label: "NT-proBNP", unit: "pg/mL", section: "Cardiac biomarkers" },
  { key: "sodium", label: "Sodium", unit: "mEq/L", section: "Electrolytes" },
  { key: "potassium", label: "Potassium", unit: "mEq/L", section: "Electrolytes" },
  { key: "magnesium", label: "Magnesium", unit: "mg/dL", section: "Electrolytes" },
  { key: "calcium", label: "Calcium", unit: "mg/dL", section: "Electrolytes" },
  { key: "creatinine", label: "Creatinine", unit: "mg/dL", section: "Renal" },
  { key: "bun", label: "BUN", unit: "mg/dL", section: "Renal" },
  { key: "hemoglobin", label: "Hemoglobin", unit: "g/L", section: "Hematology" },
  { key: "wbc", label: "WBC", unit: "x10^9/L", section: "Hematology" },
  { key: "platelet_count", label: "Platelets", unit: "/mm^3", section: "Hematology" },
  { key: "inr", label: "INR", unit: "", section: "Coagulation" },
  { key: "sgpt", label: "SGPT", unit: "U/L", section: "Liver" },
  { key: "sgot", label: "SGOT", unit: "U/L", section: "Liver" },
  { key: "blood_glucose", label: "Blood glucose", unit: "mg/dL", section: "Metabolic" },
];

const LAB_SECTIONS = [...new Set(LAB_FIELDS.map((f) => f.section))];

const LAB_LABELS: Record<string, string> = Object.fromEntries(
  LAB_FIELDS.map((f) => [f.key, f.unit ? `${f.label} (${f.unit})` : f.label])
);

function LabForm() {
  const [patients, setPatients] = useState<Array<{ id: string; name: string; bed: string; ranked: boolean }>>([]);
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listAllPatients().then(setPatients);
  }, []);

  const hasAnyLab = LAB_FIELDS.some((f) => values[f.key]?.trim());
  const selectedPatient = patients.find((p) => p.id === selectedId);
  const canSubmit = selectedId && hasAnyLab && selectedPatient?.ranked;

  const setField = (key: string, val: string) =>
    setValues((prev) => ({ ...prev, [key]: val }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || submitting) return;
    setSubmitting(true);

    const labs: Record<string, number> = {};
    for (const f of LAB_FIELDS) {
      const v = values[f.key];
      if (v?.trim()) {
        const n = parseFloat(v);
        if (!isNaN(n)) labs[f.key] = n;
      }
    }

    await enterLabResults(selectedId, labs);
    const names = Object.keys(labs).map((k) => LAB_FIELDS.find((f) => f.key === k)?.label ?? k).join(", ");
    setResult(`Lab results recorded for ${selectedPatient?.name} (${selectedId}): ${names}.`);
    setValues({});
    setSubmitting(false);
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <Head title="Enter lab results">
        <p className="hw-text-muted">Select a ranked patient.</p>
      </Head>

      {result && <Saved text={result} onDismiss={() => setResult(null)} />}

      <div className={styles.patient}>
        <SelectField label="Patient" value={selectedId} onChange={(e) => setSelectedId(e.target.value)} required>
          <option value="">Select patient...</option>
          {patients.filter((p) => p.ranked).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} -- Bed {p.bed} ({p.id})
            </option>
          ))}
          {patients.filter((p) => !p.ranked).length > 0 && (
            <optgroup label="Not ranked (lab entry once ranked)">
              {patients.filter((p) => !p.ranked).map((p) => (
                <option key={p.id} value={p.id} disabled>
                  {p.name} -- Bed {p.bed} ({p.id})
                </option>
              ))}
            </optgroup>
          )}
        </SelectField>
      </div>

      <div className={styles.cards}>
        {LAB_SECTIONS.map((sec) => (
          <Card key={sec} title={sec}>
            {LAB_FIELDS.filter((f) => f.section === sec).map((f) => (
              <InputField
                key={f.key}
                label={LAB_LABELS[f.key]}
                type="number"
                step="any"
                min={0}
                value={values[f.key] ?? ""}
                onChange={(e) => setField(f.key, e.target.value)}
              />
            ))}
          </Card>
        ))}
      </div>

      <div className={styles.actions}>
        <button type="submit" className="hw-button hw-button--primary" disabled={!canSubmit || submitting}>
          Record lab results
        </button>
      </div>
    </form>
  );
}

// --- Upload (document extraction for labs or patient records) ---

type UploadType = "lab" | "patient";
type UploadStep = "select" | "review" | "done";

const PATIENT_LABELS: Record<string, string> = {
  lastName: "Last name",
  firstName: "First name",
  middleName: "Middle name",
  dateOfBirth: "Date of birth",
  age: "Age",
  sex: "Sex",
  contactNumber: "Contact number",
  bed: "Bed",
  admissionRoute: "Admission route",
  chiefComplaint: "Chief complaint",
  admittingContext: "Admitting diagnosis / context",
  attendingPhysician: "Attending physician",
  allergies: "Allergies",
};

/** One value read from the document: its name, the editable value, and how sure the reading was. */
function ReviewRow({ label, confidence, ...input }: { label: string; confidence?: number } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className={styles.reviewRow}>
      <label className={styles.reviewLabel} htmlFor={id}>{label}</label>
      <div className="hw-input hw-input--sm">
        <input id={id} className="hw-input__control" aria-describedby={confidence === undefined ? undefined : `${id}-c`} {...input} />
      </div>
      {confidence !== undefined && (
        <span id={`${id}-c`} className={`hw-tag hw-tag--round ${styles.confidence}`}>{Math.round(confidence * 100)}%</span>
      )}
    </div>
  );
}

function UploadForm() {
  const [uploadType, setUploadType] = useState<UploadType>("lab");
  const [patients, setPatients] = useState<Array<{ id: string; name: string; bed: string; ranked: boolean }>>([]);
  const [selectedId, setSelectedId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<UploadStep>("select");
  const [labExtraction, setLabExtraction] = useState<LabExtractionResult | null>(null);
  const [patientExtraction, setPatientExtraction] = useState<PatientExtractionResult | null>(null);
  const [editedValues, setEditedValues] = useState<Record<string, string>>({});
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listAllPatients().then(setPatients);
  }, []);

  const selectedPatient = patients.find((p) => p.id === selectedId);
  const canExtractLab = uploadType === "lab" && selectedId && file && selectedPatient?.ranked;
  const canExtractPatient = uploadType === "patient" && file;
  const canExtract = canExtractLab || canExtractPatient;

  const handleExtract = async () => {
    if (!canExtract || !file) return;
    setExtracting(true);

    if (uploadType === "lab") {
      const res = await extractLabsFromFile(file);
      setLabExtraction(res);
      const initial: Record<string, string> = {};
      for (const [key, val] of Object.entries(res.values)) {
        if (val !== undefined) initial[key] = String(val);
      }
      setEditedValues(initial);
    } else {
      const res = await extractPatientFromFile(file);
      setPatientExtraction(res);
      const initial: Record<string, string> = {};
      for (const [key, val] of Object.entries(res.values)) {
        if (val !== undefined) initial[key] = String(val);
      }
      setEditedValues(initial);
    }

    setExtracting(false);
    setStep("review");
  };

  const handleConfirmLab = async () => {
    if (!selectedId || submitting) return;
    setSubmitting(true);

    const labs: Record<string, number> = {};
    for (const [key, val] of Object.entries(editedValues)) {
      const n = parseFloat(val);
      if (!isNaN(n) && val.trim()) labs[key] = n;
    }

    await enterLabResults(selectedId, labs);
    const names = Object.keys(labs).join(", ");
    setResult(`Lab results recorded for ${selectedPatient?.name} (${selectedId}): ${names}.`);
    setStep("done");
    setSubmitting(false);
  };

  const handleConfirmPatient = async () => {
    if (submitting) return;
    setSubmitting(true);

    const v = editedValues;
    const input: AdmitPatientInput = {
      lastName: v.lastName?.trim() || "Unknown",
      firstName: v.firstName?.trim() || "Unknown",
      ...(v.middleName?.trim() ? { middleName: v.middleName.trim() } : {}),
      dateOfBirth: v.dateOfBirth?.trim() || "1970-01-01",
      age: parseInt(v.age, 10) || 0,
      sex: (v.sex === "M" || v.sex === "F") ? v.sex : "M",
      ...(v.contactNumber?.trim() ? { contactNumber: v.contactNumber.trim() } : {}),
      bed: v.bed?.trim() || "TBD",
      admissionRoute: v.admissionRoute === "direct_admission" ? "direct_admission"
        : v.admissionRoute === "transfer" ? "transfer" : "er_triage",
      chiefComplaint: v.chiefComplaint?.trim() || "See uploaded document",
      admittingContext: v.admittingContext?.trim() || "Extracted from uploaded document",
      attendingPhysician: v.attendingPhysician?.trim() || PHYSICIANS[0],
      ...(v.allergies?.trim() ? { allergies: v.allergies.trim() } : {}),
    };

    const row = await admitPatient(input);
    setResult(`Admitted ${row.name} as ${row.id} -- Bed ${row.bed}. Status: NOT RANKED until every NEWS2 input is recorded.`);
    setStep("done");
    setSubmitting(false);
  };

  const handleReset = () => {
    setFile(null);
    setLabExtraction(null);
    setPatientExtraction(null);
    setEditedValues({});
    setStep("select");
    setResult(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const edit = (key: string) => (e: { target: { value: string } }) =>
    setEditedValues((prev) => ({ ...prev, [key]: e.target.value }));

  return (
    <div className={styles.form}>
      <Head title="Upload document">
        <p className={`hw-alert ${styles.banner}`}>
          <span className="hw-alert__icon hw-icon fa-solid fa-circle-info" aria-hidden="true" />
          <span className="hw-alert__body">
            Document reading is simulated: the values are sample data, not from your file.
          </span>
        </p>
      </Head>

      {result && <Saved text={result} onDismiss={handleReset} />}

      {step === "select" && (
        <section className={`hw-card hw-card--outlined ${styles.stack}`}>
          <div className={styles.fields}>
            <SelectField
              label="Document type"
              value={uploadType}
              onChange={(e) => { setUploadType(e.target.value as UploadType); setSelectedId(""); }}
            >
              <option value="lab">Lab results</option>
              <option value="patient">Patient record (admission)</option>
            </SelectField>

            {uploadType === "lab" && (
              <SelectField label="Patient" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                <option value="">Select patient...</option>
                {patients.filter((p) => p.ranked).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} -- Bed {p.bed} ({p.id})
                  </option>
                ))}
              </SelectField>
            )}
          </div>

          <label className="hw-upload">
            <input
              ref={fileRef}
              className="hw-visually-hidden"
              type="file"
              accept=".pdf,.jpg,.jpeg,.png"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <span className="hw-icon hw-icon--xl fa-solid fa-upload" aria-hidden="true" />
            <span className="hw-upload__title">File</span>
          </label>

          {file && (
            <div className={`hw-file ${styles.file}`}>
              <span className="hw-icon fa-regular fa-file" aria-hidden="true" />
              <span className={styles.fileName}>{file.name}</span>
              <span className="hw-caption">{(file.size / 1024).toFixed(1)} KB</span>
            </div>
          )}

          <div className={styles.actions}>
            <button
              type="button"
              className="hw-button hw-button--primary"
              disabled={!canExtract || extracting}
              onClick={handleExtract}
            >
              {extracting ? "Reading document..." : "Read document"}
            </button>
          </div>
        </section>
      )}

      {step === "review" && uploadType === "lab" && labExtraction && (
        <section className={`hw-card hw-card--outlined ${styles.stack}`}>
          <h2 className={`hw-card__title ${styles.cardTitle}`}>Values read from document -- review and correct</h2>

          <div className={styles.review}>
            {Object.entries(labExtraction.values).map(([key, val]) =>
              val === undefined ? null : (
                <ReviewRow
                  key={key}
                  label={LAB_LABELS[key] ?? key}
                  confidence={labExtraction.confidence[key]}
                  type="number"
                  step="any"
                  value={editedValues[key] ?? ""}
                  onChange={edit(key)}
                />
              ),
            )}
          </div>

          <div className={styles.actions}>
            <button type="button" className="hw-button hw-button--primary" disabled={submitting} onClick={handleConfirmLab}>
              {submitting ? "Saving..." : "Confirm and save"}
            </button>
            <button type="button" className="hw-button hw-button--ghost" onClick={handleReset}>Cancel</button>
          </div>
        </section>
      )}

      {step === "review" && uploadType === "patient" && patientExtraction && (
        <section className={`hw-card hw-card--outlined ${styles.stack}`}>
          <h2 className={`hw-card__title ${styles.cardTitle}`}>Values read from document -- review and correct</h2>

          <div className={styles.review}>
            {Object.entries(patientExtraction.values).map(([key, val]) =>
              val === undefined ? null : (
                <ReviewRow
                  key={key}
                  label={PATIENT_LABELS[key] ?? key}
                  confidence={patientExtraction.confidence[key]}
                  type="text"
                  value={editedValues[key] ?? ""}
                  onChange={edit(key)}
                />
              ),
            )}
          </div>

          <div className={styles.actions}>
            <button type="button" className="hw-button hw-button--primary" disabled={submitting} onClick={handleConfirmPatient}>
              {submitting ? "Admitting..." : "Confirm and admit"}
            </button>
            <button type="button" className="hw-button hw-button--ghost" onClick={handleReset}>Cancel</button>
          </div>
        </section>
      )}

      {step === "done" && (
        <div className={styles.actions}>
          <button type="button" className="hw-button hw-button--primary" onClick={handleReset}>
            Upload another
          </button>
        </div>
      )}
    </div>
  );
}
