import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { closeDialog } from "hw-ui";
import { recordVitals } from "../api";
import {
  DIASTOLIC_BOUNDS,
  HEART_RATE_BOUNDS,
  RESPIRATORY_RATE_BOUNDS,
  SPO2_BOUNDS,
  SYSTOLIC_BOUNDS,
  TEMPERATURE_BOUNDS,
} from "../api/bounds";
import type { AbstainedRow, SessionRole, VitalsRecorded } from "../types";
import { band, response } from "./risk";
import {
  ACVPU_CHOICES,
  EMPTY_DRAFT,
  type DraftProblems,
  type VitalsDraft,
  echoDraft,
  sentence,
  standing,
  submitDraft,
} from "./vitalsDraft";
import styles from "./VitalsEntry.module.css";

const PATCH_INPUTS = [
  { key: "heartRate", label: "Heart rate (bpm)", bounds: HEART_RATE_BOUNDS },
  { key: "respiratoryRate", label: "Respiratory rate (/min)", bounds: RESPIRATORY_RATE_BOUNDS },
  { key: "spo2", label: "SpO2 (%)", bounds: SPO2_BOUNDS },
  { key: "temperature", label: "Temperature (°C)", bounds: TEMPERATURE_BOUNDS },
] as const;

// --- Presentation: everything the form shows is a function of its props ---

export interface VitalsEntryFormProps {
  readonly patientId: string;
  readonly patientName: string;
  readonly clinicianId: string;
  readonly draft: VitalsDraft;
  readonly problems: DraftProblems;
  readonly busy: boolean;
  readonly failure: string | null;
  /** Offline: the form stays, Record waits until the connection is back. */
  readonly offline?: boolean;
  readonly onChange: (draft: VitalsDraft) => void;
  readonly onSubmit: () => void;
  readonly onClose: () => void;
}

/**
 * The form inside the drawer: blood pressure first (what the patch cannot supply), then ACVPU, oxygen,
 * the SpO2 scale and the patch values. Problems show only after Record, each under its field.
 */
export function VitalsEntryForm(props: VitalsEntryFormProps) {
  const { patientId, patientName, clinicianId, draft, problems, busy, offline = false } = props;
  const { failure, onChange, onSubmit, onClose } = props;
  const echo = echoDraft(draft);
  const group = (name: string) => `${patientId}-${name}`;
  const uid = useId();
  const err = (key: string, text: string | undefined) =>
    text !== undefined && <p key={key} id={`${uid}-${key}`} className="hw-field__error">{text}</p>;
  const described = (...keys: (keyof DraftProblems)[]) =>
    keys.filter((k) => problems[k] !== undefined).map((k) => `${uid}-${k}`).join(" ") || undefined;

  return (
    <form
      className={styles.form}
      aria-label={`Record vitals for ${patientName}`}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className={`hw-dialog__body ${styles.body}`}>
        <p className={styles.sub}>
          Nothing is filled in for you. A patch value typed here counts for 12 hours, or until the patch reads it again.
        </p>

        <fieldset className={styles.group} disabled={busy}>
          <legend className={styles.legend}>Blood pressure (mmHg)</legend>
          <div className={styles.pair}>
            <label className={`hw-field ${styles.field}`}>
              <span className="hw-field__label">Systolic</span>
              <span className="hw-input">
                <input
                  className="hw-input__control"
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder={`${SYSTOLIC_BOUNDS.min}-${SYSTOLIC_BOUNDS.max}`}
                  value={draft.systolic}
                  aria-invalid={problems.systolic !== undefined || problems.pair !== undefined}
                  aria-describedby={described("systolic", "pair")}
                  onChange={(e) => onChange({ ...draft, systolic: e.target.value })}
                />
              </span>
            </label>
            <span className={styles.slash} aria-hidden="true">/</span>
            <label className={`hw-field ${styles.field}`}>
              <span className="hw-field__label">Diastolic</span>
              <span className="hw-input">
                <input
                  className="hw-input__control"
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder={`${DIASTOLIC_BOUNDS.min}-${DIASTOLIC_BOUNDS.max}`}
                  value={draft.diastolic}
                  aria-invalid={problems.diastolic !== undefined || problems.pair !== undefined}
                  aria-describedby={described("diastolic", "pair")}
                  onChange={(e) => onChange({ ...draft, diastolic: e.target.value })}
                />
              </span>
            </label>
          </div>
          {err("systolic", problems.systolic)}
          {err("diastolic", problems.diastolic)}
          {err("pair", problems.pair)}
        </fieldset>

        <fieldset className={styles.group} disabled={busy}>
          <legend className={styles.legend}>Level of consciousness (ACVPU)</legend>
          <div className={styles.choices}>
            <Choice name={group("acvpu")} checked={draft.acvpu === ""} label="Not recorded" onPick={() => onChange({ ...draft, acvpu: "" })} />
            {ACVPU_CHOICES.map((c) => (
              <Choice
                key={c.value}
                name={group("acvpu")}
                checked={draft.acvpu === c.value}
                label={`${c.value} ${c.label}`}
                onPick={() => onChange({ ...draft, acvpu: c.value })}
              />
            ))}
          </div>
        </fieldset>

        <fieldset className={styles.group} disabled={busy}>
          <legend className={styles.legend}>Oxygen</legend>
          <div className={styles.choices}>
            <Choice name={group("oxygen")} checked={draft.oxygen === ""} label="Not recorded" onPick={() => onChange({ ...draft, oxygen: "" })} />
            <Choice name={group("oxygen")} checked={draft.oxygen === "air"} label="Room air" onPick={() => onChange({ ...draft, oxygen: "air" })} />
            <Choice
              name={group("oxygen")}
              checked={draft.oxygen === "oxygen"}
              label="On supplemental oxygen"
              onPick={() => onChange({ ...draft, oxygen: "oxygen" })}
            />
          </div>
        </fieldset>

        <fieldset className={styles.group} disabled={busy}>
          <legend className={styles.legend}>SpO2 scale</legend>
          <div className={styles.choices}>
            <Choice name={group("scale")} checked={draft.spo2Scale === ""} label="Not recorded" onPick={() => onChange({ ...draft, spo2Scale: "" })} />
            <Choice name={group("scale")} checked={draft.spo2Scale === "1"} label="Scale 1" onPick={() => onChange({ ...draft, spo2Scale: "1" })} />
            <Choice name={group("scale")} checked={draft.spo2Scale === "2"} label="Scale 2" onPick={() => onChange({ ...draft, spo2Scale: "2" })} />
          </div>
          <p className={styles.hint}>Scale 2 only on a clinician&rsquo;s decision (hypercapnic respiratory failure).</p>
        </fieldset>

        <fieldset className={styles.group} disabled={busy}>
          <legend className={styles.legend}>Patch values, if measured by hand</legend>
          <div className={styles.grid}>
            {PATCH_INPUTS.map(({ key, label, bounds }) => (
              <div key={key} className={styles.field}>
                <label className="hw-field">
                  <span className="hw-field__label">{label}</span>
                  <span className="hw-input">
                    <input
                      className="hw-input__control"
                      inputMode={key === "temperature" ? "decimal" : "numeric"}
                      autoComplete="off"
                      placeholder={`${bounds.min}-${bounds.max}`}
                      value={draft[key]}
                      aria-invalid={problems[key] !== undefined}
                      aria-describedby={described(key)}
                      onChange={(e) => onChange({ ...draft, [key]: e.target.value })}
                    />
                  </span>
                </label>
                {err(key, problems[key])}
              </div>
            ))}
          </div>
        </fieldset>

        <div className={styles.echo} aria-live="polite">
          {echo.length > 0 ? (
            <>
              <p className={styles.echoTitle}>Will be recorded as a manual entry by {clinicianId}:</p>
              <ul className={styles.echoList}>
                {echo.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </>
          ) : (
            <p className={styles.echoTitle}>Nothing to record yet.</p>
          )}
        </div>

        {problems.form !== undefined && (
          <p className={`hw-alert hw-alert--danger ${styles.message}`} role="alert">{problems.form}</p>
        )}
        {failure !== null && (
          <p className={`hw-alert hw-alert--danger ${styles.message}`} role="alert">{failure}</p>
        )}
      </div>

      <div className="hw-dialog__footer">
        <button type="button" className="hw-button" onClick={onClose}>Close</button>
        <button type="submit" className="hw-button hw-button--accent" disabled={busy || offline}>
          {busy ? "Recording…" : "Record"}
        </button>
      </div>
    </form>
  );
}

function Choice(props: {
  readonly name: string;
  readonly checked: boolean;
  readonly label: string;
  readonly onPick: () => void;
}) {
  return (
    <label className="hw-radio">
      <input className="hw-radio__input" type="radio" name={props.name} checked={props.checked} onChange={props.onPick} />
      <span className="hw-radio__dot" />
      <span>{props.label}</span>
    </label>
  );
}

/** What the server said, in place of the form: where the patient now stands, and what to do next. */
function Outcome({ recorded, role, showOpen, onAgain, onClose }: {
  recorded: VitalsRecorded; role: SessionRole; showOpen: boolean; onAgain: () => void; onClose: () => void;
}) {
  const ranked = recorded.ranked && recorded.rank !== null && recorded.score !== null;
  // The write's answer has no reasons, so a single 3 cannot be seen here: only a 5+ total earns a step.
  const b = ranked ? band({ score: recorded.score as number, escalation_reasons: [] }) : "low";
  return (
    <>
      <div className={`hw-dialog__body ${styles.body}`}>
        {/* Still NOT RANKED wears the dashed NOT RANKED box: dashed means incomplete. */}
        <p className={ranked ? `hw-alert hw-alert--success ${styles.message}` : "hw-notice hw-notice--dashed"} role="status">{standing(recorded)}</p>
        {b !== "low" && (
          <p className={styles.next}>Next step: <strong>{response(b, role)}</strong></p>
        )}
      </div>
      <div className="hw-dialog__footer">
        <button type="button" className="hw-button" onClick={onClose}>Close</button>
        {ranked
          ? showOpen && <Link to={`/patient/${recorded.patient_id}`} className="hw-button hw-button--accent">Open patient</Link>
          : <button type="button" className="hw-button hw-button--accent" onClick={onAgain}>Add the missing values</button>}
      </div>
    </>
  );
}

// --- Behaviour: the drawer, the draft, the request, and what came back ---

/** Who the vitals are for: a ranked patient, or a NOT RANKED row with its reason and instruction. */
export type VitalsPatient = Pick<AbstainedRow, "id" | "name" | "age" | "sex" | "bed"> & Partial<Pick<AbstainedRow, "reason" | "instruction">>;

interface Props {
  readonly patient: VitalsPatient;
  readonly clinicianId: string;
  /** Whose NEWS2 response the result names. */
  readonly role: SessionRole;
  readonly offline?: boolean;
  /** Offer "Open patient" on a ranked result (from the Ward; the patient page is that patient already). */
  readonly showOpen?: boolean;
  readonly onRecorded: (recorded: VitalsRecorded) => void;
  /** The drawer has closed (Close, Esc or the backdrop): the caller unmounts it. */
  readonly onClose: () => void;
}

/**
 * Record vitals: one drawer from the right on a desk, full screen on a phone (kit hw-dialog--drawer on a
 * native modal <dialog>: focus held inside, Esc closes). Opened from the patient page and from the Ward's
 * NOT RANKED rows. The caller mounts it to open it.
 */
export function VitalsEntry({ patient, clinicianId, role, offline = false, showOpen = false, onRecorded, onClose }: Props) {
  const [draft, setDraft] = useState<VitalsDraft>(EMPTY_DRAFT);
  const [problems, setProblems] = useState<DraftProblems>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [recorded, setRecorded] = useState<VitalsRecorded | null>(null);
  // Why the patient is NOT RANKED: the row's reason first, then the server's after each entry.
  const [why, setWhy] = useState(patient.reason !== undefined ? { reason: patient.reason, instruction: patient.instruction ?? "" } : null);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);
  const close = () => void closeDialog(dialog.current);

  const submit = async () => {
    setBusy(true);
    setFailure(null);
    const outcome = await submitDraft(patient.id, draft, recordVitals);
    setBusy(false);
    if (outcome.kind === "invalid") {
      setProblems(outcome.problems);
    } else if (outcome.kind === "failed") {
      setProblems({});
      setFailure(outcome.message);
    } else {
      setProblems({});
      setDraft(EMPTY_DRAFT);
      setRecorded(outcome.recorded);
      const r = outcome.recorded;
      setWhy(r.ranked ? null : { reason: r.not_ranked_reason ?? "", instruction: r.not_ranked_instruction ?? "" });
      onRecorded(outcome.recorded);
    }
  };

  return (
    <dialog
      ref={dialog}
      className="hw-dialog hw-dialog--drawer"
      aria-labelledby={title}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault(); // Esc: animate out, then close
        close();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close(); // the backdrop
      }}
    >
      <div className="hw-dialog__header">
        <div>
          <h2 id={title} className="hw-dialog__title">Record vitals</h2>
          <p className={styles.who}>
            {patient.name} &middot; {patient.age ?? "--"}{patient.sex ? ` ${patient.sex}` : ""} &middot; Bed {patient.bed}
          </p>
        </div>
        <button type="button" className="hw-close" aria-label="Close record vitals" onClick={close}>
          <span className="hw-icon fa-solid fa-xmark" aria-hidden="true" />
        </button>
      </div>
      {why !== null && recorded === null && (
        <p className={styles.why}>
          <span className="hw-tag hw-tag--dashed">NOT RANKED</span> <strong>{sentence(why.reason)}</strong> {sentence(why.instruction)}
        </p>
      )}
      {offline && (
        <p className={`hw-alert hw-alert--warning ${styles.why}`} role="status">
          <span className="hw-alert__body">Cannot reach the ward server. Recording waits until the connection is back.</span>
        </p>
      )}
      {recorded !== null ? (
        <Outcome recorded={recorded} role={role} showOpen={showOpen} onAgain={() => setRecorded(null)} onClose={close} />
      ) : (
        <VitalsEntryForm
          patientId={patient.id}
          patientName={patient.name}
          clinicianId={clinicianId}
          draft={draft}
          problems={problems}
          busy={busy}
          failure={failure}
          offline={offline}
          onChange={(next) => {
            setDraft(next);
            setProblems({});
          }}
          onSubmit={() => void submit()}
          onClose={close}
        />
      )}
    </dialog>
  );
}
