import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { Link } from "react-router-dom";
import type { Session, AbstainedRow, AdmitPatientInput, LabExtractionResult, PatientExtractionResult } from "../types";
import { admitPatient, enterLabResults, extractLabsFromFile, extractPatientFromFile, listAllPatients } from "../api";
import { AppShell } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { useWardFeed } from "../components/wardFeed";
import { NotRankedSection } from "./NotRankedSection";
import styles from "./Admit.module.css";

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

type Task = "admit" | "labs" | "doc";

const TASKS: Array<{ id: Task; label: string }> = [
  { id: "admit", label: "Admit a patient" },
  { id: "labs", label: "Enter lab results" },
  { id: "doc", label: "Read a document" },
];

/** Admit: three ways to get a patient's information in. Only the chosen task is mounted, so switching drops what was typed. */
export function Admit({ session, onSignOut }: Props) {
  const [task, setTask] = useState<Task>("admit");
  return (
    <AppShell session={session} onSignOut={onSignOut} title="Admit">
      <SimulatedNote />
      <div className={styles.page}>
        <header>
          <h1 className="hw-heading hw-heading--2">Admit</h1>
          <p className="hw-text-muted">
            New patients start NOT RANKED. This is a synthetic ward: never enter a real patient's details.
          </p>
        </header>
        <fieldset className={`hw-segmented hw-segmented--accent ${styles.tasks}`}>
          <legend className="hw-visually-hidden">What are you entering?</legend>
          {TASKS.map((t) => (
            <label key={t.id}>
              <input type="radio" className="hw-segmented__input" name="task" checked={task === t.id} onChange={() => setTask(t.id)} />
              <span className="hw-segmented__option">{t.label}</span>
            </label>
          ))}
        </fieldset>
        {task === "admit" && <AdmitPatient />}
        {task === "labs" && <LabForm />}
        {task === "doc" && <ReadDocument />}
      </div>
    </AppShell>
  );
}

// --- Kit fields: every label names its control; a required one is marked, an error describes it ---

type FieldProps = { label: string; wide?: boolean; req?: boolean; error?: string };

function Field({ id, label, wide, req, error, children }: FieldProps & { id: string; children: ReactNode }) {
  return (
    <div className={`hw-field hw-field--full ${wide ? styles.wide : ""}`}>
      <label className="hw-field__label" htmlFor={id}>
        {label}
        {req && <span className="hw-field__required" aria-hidden="true">*</span>}
      </label>
      {children}
      {error && <p id={`${id}-e`} className="hw-field__error">{error}</p>}
    </div>
  );
}

/** What a field's control says about itself: required, and in error with its message. */
const states = (id: string, req?: boolean, error?: string) => ({
  "aria-required": req || undefined,
  "aria-invalid": error ? true : undefined,
  "aria-describedby": error ? `${id}-e` : undefined,
});

function InputField({ label, wide, req, error, ...input }: FieldProps & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <Field id={id} label={label} wide={wide} req={req} error={error}>
      <div className="hw-input"><input id={id} className="hw-input__control" {...states(id, req, error)} {...input} /></div>
    </Field>
  );
}

function SelectField({ label, wide, req, error, ...select }: FieldProps & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <Field id={id} label={label} wide={wide} req={req} error={error}>
      <select id={id} className="hw-native-select" {...states(id, req, error)} {...select} />
    </Field>
  );
}

function TextareaField({ label, wide, req, error, ...area }: FieldProps & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  return (
    <Field id={id} label={label} wide={wide} req={req} error={error}>
      <div className="hw-textarea"><textarea id={id} className="hw-textarea__control" rows={3} {...states(id, req, error)} {...area} /></div>
    </Field>
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

// --- Admit a patient: Identity → Admission → Initial assessment → Review ---

const BLANK = {
  lastName: "", firstName: "", middleName: "", dob: "", age: "", sex: "", contactNumber: "", bed: "",
  route: "er_triage", physician: PHYSICIANS[0], chiefComplaint: "", context: "", allergies: "",
  weightKg: "", heightCm: "", bp: "", avpu: "",
};
type Draft = typeof BLANK;
type Key = keyof Draft;

const ROUTES: Record<string, string> = {
  er_triage: "ER Triage",
  direct_admission: "Direct Ward Admission",
  transfer: "Transfer from another facility",
};
const ACVPU: Record<string, string> = { A: "Alert", C: "New confusion", V: "Verbal", P: "Pain", U: "Unresponsive" };

const LABELS: Record<Key, string> = {
  lastName: "Last name", firstName: "First name", middleName: "Middle name", dob: "Date of birth", age: "Age",
  sex: "Sex", contactNumber: "Contact number", bed: "Bed", route: "Admission route", physician: "Attending physician",
  chiefComplaint: "Chief complaint", context: "Admitting diagnosis / context", allergies: "Allergies",
  weightKg: "Weight (kg)", heightCm: "Height (cm)", bp: "Blood pressure (sys/dia)", avpu: "Consciousness (ACVPU)",
};

const STEPS: Array<{ title: string; keys: Key[]; required: Key[] }> = [
  { title: "Identity", keys: ["lastName", "firstName", "middleName", "dob", "age", "sex", "contactNumber", "bed"], required: ["lastName", "firstName", "dob", "age", "sex", "bed"] },
  { title: "Admission", keys: ["route", "physician", "chiefComplaint", "context", "allergies"], required: ["chiefComplaint", "context"] },
  { title: "Initial assessment", keys: ["weightKg", "heightCm", "bp", "avpu"], required: [] },
];
const REVIEW = 3;

/** The words shown for a stored value: the option's name where there is one, "--" for nothing entered. */
function shown(k: Key, v: string): string {
  const text = k === "route" ? ROUTES[v] : k === "avpu" ? ACVPU[v] : v;
  return text?.trim() ? text.trim() : "--";
}

function AdmitPatient() {
  const [f, setF] = useState<Draft>(BLANK);
  const [step, setStep] = useState(0);
  const [tried, setTried] = useState<number[]>([]); // steps where Next was pressed: only these show their errors
  const [attempt, setAttempt] = useState(0);
  const [admitted, setAdmitted] = useState<AbstainedRow | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  const set = (k: Key) => (e: { target: { value: string } }) => setF((d) => ({ ...d, [k]: e.target.value }));
  const missing = (s: number) => STEPS[s].required.filter((k) => !f[k].trim());
  const error = (k: Key): string | undefined => {
    if (!tried.includes(step) || f[k].trim() || !STEPS[step].required.includes(k)) return undefined;
    return k === "sex" ? "Choose the sex." : `Enter the ${LABELS[k].toLowerCase()}.`;
  };
  const props = (k: Key) => ({ label: LABELS[k], value: f[k], onChange: set(k), req: STEPS[step].required.includes(k), error: error(k) });

  // After a Next with gaps, the first field in error takes focus.
  useEffect(() => {
    if (attempt) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [attempt]);

  const handleDobChange = (value: string) => {
    setF((d) => {
      if (!value) return { ...d, dob: value };
      const birth = new Date(value);
      const today = new Date();
      let years = today.getFullYear() - birth.getFullYear();
      const m = today.getMonth() - birth.getMonth();
      if (m < 0 || (m === 0 && today.getDate() < birth.getDate())) years--;
      return { ...d, dob: value, age: String(Math.max(0, years)) };
    });
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (step < REVIEW) {
      if (missing(step).length > 0) {
        setTried((t) => [...t, step]);
        setAttempt((n) => n + 1);
        return;
      }
      setStep(step + 1);
      return;
    }
    if (submitting) return;
    setSubmitting(true);

    const input: AdmitPatientInput = {
      lastName: f.lastName.trim(),
      firstName: f.firstName.trim(),
      ...(f.middleName.trim() ? { middleName: f.middleName.trim() } : {}),
      dateOfBirth: f.dob,
      age: parseInt(f.age, 10),
      sex: f.sex as "M" | "F",
      ...(f.contactNumber.trim() ? { contactNumber: f.contactNumber.trim() } : {}),
      bed: f.bed.trim(),
      admissionRoute: f.route as AdmitPatientInput["admissionRoute"],
      chiefComplaint: f.chiefComplaint.trim(),
      admittingContext: f.context.trim(),
      attendingPhysician: f.physician,
      ...(f.allergies.trim() ? { allergies: f.allergies.trim() } : {}),
      ...(f.weightKg ? { weightKg: parseFloat(f.weightKg) } : {}),
      ...(f.heightCm ? { heightCm: parseFloat(f.heightCm) } : {}),
      ...(f.bp.trim() ? { initialBp: f.bp.trim() } : {}),
      ...(f.avpu ? { initialAvpu: f.avpu as "A" | "C" | "V" | "P" | "U" } : {}),
    };

    setAdmitted(await admitPatient(input));
    setSubmitting(false);
  };

  const again = () => {
    setF(BLANK);
    setStep(0);
    setTried([]);
    setAdmitted(null);
  };

  if (admitted) {
    return (
      <section aria-label="Admit a patient" className={`hw-card ${styles.task}`}>
        <div className={`hw-alert hw-alert--success ${styles.banner}`} role="status">
          <span className="hw-alert__icon hw-icon fa-solid fa-circle-check" aria-hidden="true" />
          <span className="hw-alert__body">
            Admitted {admitted.name} as {admitted.id} -- Bed {admitted.bed}. Status: NOT RANKED until every NEWS2 input is recorded.
          </span>
        </div>
        <NotRankedSection rows={[admitted]} readOnly />
        <div className={styles.actions}>
          <Link className="hw-button hw-button--accent" to="/ward">Go to the ward list</Link>
          <button type="button" className="hw-button" onClick={again}>Admit another patient</button>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Admit a patient" className={`hw-card ${styles.task}`}>
      <ol className="hw-steps hw-steps--accent" aria-label="Steps">
        {[...STEPS.map((s) => s.title), "Review"].map((title, i) => (
          <li
            key={title}
            className="hw-steps__step"
            data-status={i < step ? "done" : i === step ? "current" : undefined}
            aria-current={i === step ? "step" : undefined}
          >
            {title}
          </li>
        ))}
      </ol>

      <form ref={form} className={styles.form} onSubmit={handleSubmit}>
        {step < REVIEW && <p className="hw-caption">Fields marked * are needed to admit.</p>}

        {step === 0 && (
          <div className={styles.fields}>
            <InputField {...props("lastName")} />
            <InputField {...props("firstName")} />
            <InputField {...props("middleName")} />
            <InputField {...props("dob")} type="date" onChange={(e) => handleDobChange(e.target.value)} />
            <InputField {...props("age")} type="number" min={0} max={150} />
            <SelectField {...props("sex")}>
              <option value="">--</option>
              <option value="M">M</option>
              <option value="F">F</option>
            </SelectField>
            <InputField {...props("contactNumber")} type="tel" placeholder="09XX XXX XXXX" />
            <InputField {...props("bed")} placeholder="e.g. MW-4A" />
          </div>
        )}

        {step === 1 && (
          <div className={styles.fields}>
            <SelectField {...props("route")}>
              {Object.entries(ROUTES).map(([v, text]) => <option key={v} value={v}>{text}</option>)}
            </SelectField>
            <SelectField {...props("physician")}>
              {PHYSICIANS.map((p) => <option key={p} value={p}>{p}</option>)}
            </SelectField>
            <InputField wide {...props("chiefComplaint")} placeholder="e.g. Chest pain, onset 2 hours ago" />
            <TextareaField wide {...props("context")} />
            <InputField wide {...props("allergies")} placeholder="NKDA or list allergies" />
          </div>
        )}

        {step === 2 && (
          <div className={styles.fields}>
            <InputField {...props("weightKg")} type="number" step="0.1" min={0} />
            <InputField {...props("heightCm")} type="number" step="0.1" min={0} />
            <InputField {...props("bp")} placeholder="e.g. 120/80" />
            <SelectField {...props("avpu")}>
              <option value="">--</option>
              {Object.entries(ACVPU).map(([v, text]) => <option key={v} value={v}>{text}</option>)}
            </SelectField>
          </div>
        )}

        {step === REVIEW && (
          <div className={styles.review}>
            {STEPS.map((s, i) => (
              <div key={s.title} className={`hw-card hw-card--outlined ${styles.group}`}>
                <div className={styles.groupHead}>
                  <h2 className="hw-card__title">{s.title}</h2>
                  <button type="button" className="hw-button hw-button--ghost hw-button--sm" aria-label={`Edit ${s.title.toLowerCase()}`} onClick={() => setStep(i)}>
                    Edit
                  </button>
                </div>
                <dl className={styles.pairs}>
                  {s.keys.map((k) => (
                    <div key={k}><dt>{LABELS[k]}</dt><dd>{shown(k, f[k])}</dd></div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        )}

        <div className={styles.actions}>
          {step > 0 && <button type="button" className="hw-button" onClick={() => setStep(step - 1)}>Back</button>}
          <button type="submit" className="hw-button hw-button--accent" disabled={submitting}>
            {step === REVIEW ? "Admit patient" : step === 2 ? "Review" : "Next"}
          </button>
        </div>
      </form>
    </section>
  );
}

// --- Enter lab results: the patient first, then the values ---

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

type Patient = { id: string; name: string; bed: string; ranked: boolean };

function usePatients(): Patient[] {
  const [patients, setPatients] = useState<Patient[]>([]);
  useEffect(() => {
    listAllPatients().then(setPatients);
  }, []);
  return patients;
}

/**
 * The patient, chosen first: ranked patients, then the NOT RANKED ones listed but not choosable, each with the reason
 * the ward gives for it (when this page has the ward's read).
 */
function PatientSelect({ patients, value, onChange }: { patients: Patient[]; value: string; onChange: (id: string) => void }) {
  const abstained = useWardFeed()?.data?.abstained ?? [];
  const notRanked = patients.filter((p) => !p.ranked);
  const text = (p: Patient) => `${p.name} -- Bed ${p.bed} (${p.id})`;
  return (
    <SelectField label="Patient" value={value} onChange={(e) => onChange(e.target.value)} req>
      <option value="">Select patient...</option>
      {patients.filter((p) => p.ranked).map((p) => <option key={p.id} value={p.id}>{text(p)}</option>)}
      {notRanked.length > 0 && (
        <optgroup label="Not ranked (lab entry once ranked)">
          {notRanked.map((p) => {
            const why = abstained.find((a) => a.id === p.id)?.reason;
            return <option key={p.id} value={p.id} disabled>{why ? `${text(p)}: ${why}` : text(p)}</option>;
          })}
        </optgroup>
      )}
    </SelectField>
  );
}

function LabForm() {
  const patients = usePatients();
  const [selectedId, setSelectedId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const ids = useId();

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
    <section aria-label="Enter lab results" className={`hw-card ${styles.task}`}>
      <form className={styles.form} onSubmit={handleSubmit}>
        {result && <Saved text={result} onDismiss={() => setResult(null)} />}

        <h2 id={`${ids}-who`} className="hw-heading hw-heading--4">1. Whose results are these?</h2>
        <p className="hw-text-muted">Select a ranked patient.</p>
        <div className={styles.patient}>
          <PatientSelect patients={patients} value={selectedId} onChange={setSelectedId} />
        </div>

        <h2 className="hw-heading hw-heading--4">2. Results</h2>
        {/* Patient first: the values open once a ranked patient is chosen. */}
        <fieldset className={styles.groups} disabled={!selectedPatient?.ranked}>
          <legend className="hw-visually-hidden">Results</legend>
          {LAB_SECTIONS.map((sec) => (
            <fieldset key={sec} className={styles.group}>
              <legend className="hw-card__title">{sec}</legend>
              <div className={styles.fields}>
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
              </div>
            </fieldset>
          ))}
        </fieldset>

        <div className={styles.actions}>
          <button type="submit" className="hw-button hw-button--accent" disabled={!canSubmit || submitting}>
            Record lab results
          </button>
        </div>
      </form>
    </section>
  );
}

// --- Read a document (simulated): drop zone → review with confidence → save ---

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

/** A reading below this confidence starts unticked: the person checks it before it is saved. */
const LOW_CONFIDENCE = 0.8;

/** One value read from the document: kept or not, its name, the editable value, and how sure the reading was. */
function ReviewRow({ label, confidence, kept, onKeep, ...input }: {
  label: string; confidence?: number; kept: boolean; onKeep: (kept: boolean) => void;
} & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  const low = confidence !== undefined && confidence < LOW_CONFIDENCE;
  return (
    <div className={styles.reviewRow}>
      <label className="hw-checkbox">
        <input type="checkbox" className="hw-checkbox__input" checked={kept} onChange={(e) => onKeep(e.target.checked)} aria-label={`Save ${label}`} />
        <span className="hw-checkbox__box" />
      </label>
      <label className={styles.reviewLabel} htmlFor={id}>{label}</label>
      <div className="hw-input hw-input--sm">
        <input id={id} className="hw-input__control" disabled={!kept} aria-describedby={confidence === undefined ? undefined : `${id}-c`} {...input} />
      </div>
      {confidence !== undefined && (
        <span id={`${id}-c`} className={`hw-tag hw-tag--round ${styles.confidence}`}>
          {Math.round(confidence * 100)}%{low && " · Low confidence: check it"}
        </span>
      )}
    </div>
  );
}

function ReadDocument() {
  const [uploadType, setUploadType] = useState<UploadType>("lab");
  const patients = usePatients();
  const [selectedId, setSelectedId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [step, setStep] = useState<UploadStep>("select");
  const [read, setRead] = useState<LabExtractionResult | PatientExtractionResult | null>(null);
  const [editedValues, setEditedValues] = useState<Record<string, string>>({});
  const [kept, setKept] = useState<Record<string, boolean>>({});
  const [extracting, setExtracting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const selectedPatient = patients.find((p) => p.id === selectedId);
  const canExtract = file && (uploadType === "patient" || selectedPatient?.ranked);
  const labels = uploadType === "lab" ? LAB_LABELS : PATIENT_LABELS;

  const handleExtract = async () => {
    if (!canExtract || !file) return;
    setExtracting(true);
    const res = uploadType === "lab" ? await extractLabsFromFile(file) : await extractPatientFromFile(file);
    const initial: Record<string, string> = {};
    const keep: Record<string, boolean> = {};
    for (const [key, val] of Object.entries(res.values)) {
      if (val === undefined) continue;
      initial[key] = String(val);
      keep[key] = !(res.confidence[key] < LOW_CONFIDENCE); // a reading without a confidence is not doubted
    }
    setRead(res);
    setEditedValues(initial);
    setKept(keep);
    setExtracting(false);
    setStep("review");
  };

  // Only the ticked values are saved; an unticked one is left out as if it had not been read.
  const keptValues = Object.fromEntries(Object.entries(editedValues).filter(([key]) => kept[key]));

  const handleConfirmLab = async () => {
    if (submitting) return;
    setSubmitting(true);

    const labs: Record<string, number> = {};
    for (const [key, val] of Object.entries(keptValues)) {
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

    const v = keptValues;
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
    setRead(null);
    setEditedValues({});
    setStep("select");
    setResult(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  const onDrop = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragging(false);
    setFile(e.dataTransfer.files[0] ?? null);
  };
  const onDragOver = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragging(true);
  };

  const nKept = Object.keys(keptValues).length;

  return (
    <section aria-label="Read a document" className={`hw-card ${styles.task}`}>
      <h2 className="hw-heading hw-heading--4">Read a document</h2>
      <p className={`hw-alert ${styles.banner}`}>
        <span className="hw-alert__icon hw-icon fa-solid fa-circle-info" aria-hidden="true" />
        <span className="hw-alert__body">
          Document reading is simulated: the values are sample data, not from your file.
        </span>
      </p>

      {result && <Saved text={result} onDismiss={handleReset} />}

      {step === "select" && (
        <>
          <div className={styles.fields}>
            <SelectField
              label="Document type"
              value={uploadType}
              onChange={(e) => { setUploadType(e.target.value as UploadType); setSelectedId(""); }}
            >
              <option value="lab">Lab results</option>
              <option value="patient">Patient record (admission)</option>
            </SelectField>

            {uploadType === "lab" && <PatientSelect patients={patients} value={selectedId} onChange={setSelectedId} />}
          </div>

          <label className="hw-upload" data-dragover={dragging || undefined} onDragOver={onDragOver} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
            <input
              ref={fileRef}
              className="hw-visually-hidden"
              type="file"
              accept=".pdf,.jpg,.jpeg,.png"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <span className="hw-icon hw-icon--xl fa-solid fa-file-arrow-up" aria-hidden="true" />
            <span className="hw-upload__title">Drop a document here</span>
            <span>PDF or photo, one at a time. Nothing is saved until you have checked the values.</span>
            <span className="hw-upload__browse">Choose a file</span>
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
              className="hw-button hw-button--accent"
              disabled={!canExtract || extracting}
              onClick={handleExtract}
            >
              {extracting ? "Reading document..." : "Read document"}
            </button>
          </div>
        </>
      )}

      {step === "review" && read && (
        <>
          <h3 className="hw-card__title">Values read from document -- review and correct</h3>
          <p className="hw-text-muted">Tick the values that match the document. Anything read with low confidence starts unticked.</p>

          <div className={styles.read}>
            {Object.entries(read.values).map(([key, val]) =>
              val === undefined ? null : (
                <ReviewRow
                  key={key}
                  label={labels[key] ?? key}
                  confidence={read.confidence[key]}
                  kept={kept[key]}
                  onKeep={(on) => setKept((k) => ({ ...k, [key]: on }))}
                  type={uploadType === "lab" ? "number" : "text"}
                  step={uploadType === "lab" ? "any" : undefined}
                  value={editedValues[key] ?? ""}
                  onChange={(e) => setEditedValues((prev) => ({ ...prev, [key]: e.target.value }))}
                />
              ),
            )}
          </div>

          <div className={styles.actions}>
            <button type="button" className="hw-button" onClick={handleReset}>Cancel</button>
            <button
              type="button"
              className="hw-button hw-button--accent"
              disabled={submitting || (uploadType === "lab" && nKept === 0)}
              onClick={uploadType === "lab" ? handleConfirmLab : handleConfirmPatient}
            >
              {uploadType === "lab" ? (submitting ? "Saving..." : "Confirm and save") : submitting ? "Admitting..." : "Confirm and admit"}
            </button>
          </div>
        </>
      )}

      {step === "done" && (
        <div className={styles.actions}>
          <button type="button" className="hw-button hw-button--accent" onClick={handleReset}>
            Upload another
          </button>
        </div>
      )}
    </section>
  );
}
