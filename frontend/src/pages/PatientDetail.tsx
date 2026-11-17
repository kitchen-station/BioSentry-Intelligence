import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useParams, Link } from "react-router-dom";
import { closeDialog } from "hw-ui";
import type { AbstainedDetail, ClinicalNote, PatientDetail as PatientDetailType, Session, SessionRole, VitalsRecorded } from "../types";
import { getPatient, getNotes, addNote, canRecordVitals, hasHistory, hasPatientQuestions, usesServer } from "../api";
import { Flip } from "../components/Flip";
import { AppShell } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { StayPanel } from "../components/StayPanel";
import { since, syncWardNow, useWardNow } from "../components/wardNow";
import { BaselinePanel, BaselineRange, bands } from "../components/BaselinePanel";
import { ResearchModels } from "../components/ResearchModels";
import { useBaseline } from "../components/useBaseline";
import { CurrentVitals, VitalRow } from "../components/CurrentVitals";
import { LastSeen } from "../components/AbstainedRow";
import { TrendChart } from "../components/TrendChart";
import { formatClock, formatClockSeconds, formatSimClock, patchInUse } from "../components/format";
import { useLivePoll } from "../components/useLivePoll";
import { band, driftResponse, response } from "../components/risk";
import { OPEN_CHAT } from "../components/roles";
import { VitalsEntry } from "../components/VitalsEntry";
import { standing } from "../components/vitalsDraft";
import styles from "./PatientDetail.module.css";

// --- Shared sub-components ---

// The read serves every stored bin, so the title says the span of what is drawn.
function trendTitle(history: PatientDetailType["vital_history"]) {
  const ms = Object.values(history).flatMap((pts) => pts.map((p) => Date.parse(p.time))).filter(Number.isFinite);
  if (ms.length === 0) return "Vital trends";
  const h = Math.floor((Math.max(...ms) - Math.min(...ms)) / 3_600_000);
  return `Vital trends · ${h < 1 ? "under 1 h" : `${h} h`}`;
}

// The lab rows the page draws, in order. Other lab keys the server may send have no row.
const LAB_ROWS = [
  ["troponin", "Troponin", "ng/mL"],
  ["ck_mb", "CK-MB", "U/L"],
  ["bnp", "BNP", "pg/mL"],
  ["nt_probnp", "NT-proBNP", "pg/mL"],
  ["sodium", "Sodium", "mEq/L"],
  ["potassium", "Potassium", "mEq/L"],
  ["magnesium", "Magnesium", "mg/dL"],
  ["creatinine", "Creatinine", "mg/dL"],
  ["bun", "BUN", "mg/dL"],
  ["hemoglobin", "Hemoglobin", "g/L"],
  ["wbc", "WBC", "x10^9/L"],
  ["platelet_count", "Platelets", "/mm^3"],
  ["lactate", "Lactate", "mmol/L"],
  ["sgpt", "SGPT", "U/L"],
  ["sgot", "SGOT", "U/L"],
  ["blood_glucose", "Blood glucose", "mg/dL"],
  ["inr", "INR", ""],
] as const;

type Drawer = "notes" | "charts" | null;

const MODEL_RULES = new Set<string>(["prediction_model_flag"]);

const ROLE_LABEL: Record<SessionRole, string> = { rn: "RN", charge_rn: "Charge RN", md: "MD", admin: "Admin" };

// An icon is a span, never <i>, and never carries meaning alone.
const Icon = ({ name, alert }: { name: string; alert?: boolean }) => (
  <span className={`${alert ? "hw-alert__icon " : ""}hw-icon fa-solid fa-${name}`} aria-hidden="true" />
);

// The way back to the ward, above the content.
function Crumbs({ current }: { current: string }) {
  return (
    <nav aria-label="Breadcrumb" className={styles.crumbs}>
      <ol className="hw-breadcrumb">
        <li>
          <Link to="/ward"><Icon name="arrow-left" /> Ward view</Link>
        </li>
        <li><span aria-current="page">{current}</span></li>
      </ol>
    </nav>
  );
}

// The shell and quiet line for the states with no patient to show.
function Bare({ session, onSignOut, children }: { session: Session; onSignOut: () => void; children?: ReactNode }) {
  return (
    <AppShell session={session} onSignOut={onSignOut} title="Patient">
      <SimulatedNote />
      <div className={styles.container}>
        <Crumbs current="Patient" />
        {children}
      </div>
    </AppShell>
  );
}

// --- Main component ---

function AbstainedPatientPage({ patient, session, onSignOut }: { patient: AbstainedDetail; session: Session; onSignOut: () => void }) {
  const history = patient.vital_history;
  const hasHistory = Object.values(history).some((points) => points.length > 0);
  return (
    <AppShell session={session} onSignOut={onSignOut} title={patient.name}>
      <SimulatedNote />
      <div className={styles.container}>
        <Crumbs current={patient.name} />
        <div className={styles.abstainedPage}>
          <header>
            <h1 className={styles.patientName}>{patient.name}</h1>
            <p className={styles.patientSub}>
              {patient.unit ?? "Unit unavailable"} · {patient.floor == null ? "Floor unavailable" : `Floor ${patient.floor}`} · Bed {patient.bed}
            </p>
            <p className={styles.patientSub}>{patient.age ?? "Age unavailable"}{patient.sex ? ` · ${patient.sex}` : ""}</p>
          </header>
          <section className="hw-card" aria-label="Not ranked details">
            <span className="hw-tag">NOT RANKED</span>
            <p className={styles.abstainedReason}>{patient.reason}</p>
            <p>{patient.instruction}</p>
            <p className={styles.patientSub}>{patient.admitting_context}</p>
            {patient.last_seen && <LastSeen seen={patient.last_seen} now={Date.parse(patient.now)} />}
          </section>
          <section className="hw-card" aria-label="Stored vital history">
            <h2 className={styles.eyebrow}>Stored vital history</h2>
            {hasHistory ? (
              <div className={styles.historyList}>
                <TrendChart data={history.heart_rate} label="Heart rate" unit="bpm" />
                <TrendChart data={history.respiratory_rate} label="Respiratory rate" unit="/min" />
                <TrendChart data={history.spo2} label="SpO2" unit="%" />
              </div>
            ) : <p className={styles.quiet}>No stored history available.</p>}
          </section>
        </div>
      </div>
    </AppShell>
  );
}

export function PatientDetail({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const role = session.role;
  const readOnly = role === "admin"; // an admin reads the page: every write control is absent
  const { id } = useParams<{ id: string }>();
  const [saves, setSaves] = useState(0);
  const baseline = useBaseline(id, saves);
  const [patient, setPatient] = useState<PatientDetailType | AbstainedDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const now = useWardNow(); // ward time as served at the last good read
  const [notes, setNotes] = useState<ClinicalNote[]>([]);
  const [noteText, setNoteText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [loadError, setLoadError] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [entering, setEntering] = useState(false);
  const [unranked, setUnranked] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const drawerTitle = useId();

  // The first read decides "not found" or "could not be loaded". A timed read only replaces the
  // patient: it never touches the drawer, the note box or the vitals form, and says what went wrong
  // in one line.
  const shown = useRef<string | null>(null);
  const [staleSince, setStaleSince] = useState<number | null>(null);
  const [lastGood, setLastGood] = useState<number | null>(null);
  const [failedSince, setFailedSince] = useState<number | null>(null);
  // Each request takes the next number and an answer counts only if no later request was answered
  // first: a slow old answer never brings back an older score. The timer and a saved entry share it.
  const issued = useRef(0);
  const applied = useRef(0);
  const loadPatient = useCallback(async () => {
    if (!id) return;
    const n = ++issued.current;
    try {
      const p = await getPatient(id);
      if (n > applied.current) {
        applied.current = n;
        const first = shown.current !== id;
        if (p) {
          shown.current = id;
          syncWardNow(p.now, !usesServer);
          setPatient(p);
          setLastGood(Date.parse(p.now)); // the ward's own time of the read
          setFailedSince(null);
          setStaleSince(null);
        } else if (first) setNotFound(true);
        else {
          setFailedSince(null); // the server answered: only the no-longer line is true now
          setStaleSince(Date.now());
        }
      }
      return true;
    } catch {
      if (n > applied.current) {
        if (shown.current !== id) setLoadError(true);
        else setFailedSince((t) => t ?? Date.now());
      }
      return false;
    }
  }, [id]);
  const { sim } = useLivePoll(loadPatient, usesServer && !readOnly, staleSince !== null || unranked !== null);

  useEffect(() => {
    if (id && !readOnly) getNotes(id).then(setNotes).catch(() => setNoteError("Notes could not be loaded."));
  }, [id, readOnly]);

  // An admin's page reads the notes only when the drawer opens: one audited read per real look.
  useEffect(() => {
    if (!readOnly || drawer !== "notes" || !id) return;
    getNotes(id).then(setNotes).catch(() => setNoteError("Notes could not be loaded."));
  }, [readOnly, drawer, id]);

  // The drawer is a native modal <dialog>: focus is held inside it and Esc closes it.
  useEffect(() => {
    if (drawer && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [drawer]);

  const handleAddNote = async () => {
    if (!id || !noteText.trim() || submitting) return;
    setSubmitting(true);
    setNoteError(null);
    try {
      // The signed-in clinician is the author. Notes are append-only: no delete in the UI.
      const note = await addNote(id, session.clinician_id, ROLE_LABEL[role], noteText.trim());
      setNotes((prev) => [note, ...prev]);
      setNoteText("");
    } catch {
      setNoteError("The note was not saved. Your text is kept; try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleRecorded = (recorded: VitalsRecorded) => {
    if (!id) return;
    setRefreshFailed(false);
    setSaves((n) => n + 1);
    if (recorded.ranked) {
      setUnranked(null);
      void loadPatient().then((ok) => ok === false && setRefreshFailed(true));
    } else {
      setUnranked(`Recorded. ${standing(recorded)}`);
      void loadPatient().then((ok) => ok === false && setRefreshFailed(true));
    }
  };

  const toggleDrawer = (d: Drawer) => setDrawer((prev) => prev === d ? null : d);

  if (notFound) {
    return (
      <Bare session={session} onSignOut={onSignOut}>
        <p className={styles.bare}>This patient is unavailable.</p>
        <Link to="/ward">Back to ward</Link>
      </Bare>
    );
  }

  if (loadError) {
    return (
      <Bare session={session} onSignOut={onSignOut}>
        <p role="alert" className={styles.bare}>This patient could not be loaded. Check the connection and reload.</p>
        <Link to="/ward">Back to ward</Link>
      </Bare>
    );
  }

  if (!patient) return <Bare session={session} onSignOut={onSignOut} />;
  if ("reason" in patient) return <AbstainedPatientPage patient={patient} session={session} onSignOut={onSignOut} />;

  // The patient's own band for each charted vital, from the answer the page already loaded.
  const own = bands(baseline);
  const bandOf = (vital: string) => {
    const v = own.find((x) => x.vital === vital);
    return v && { low: v.band_low, high: v.band_high, flagged: v.significant };
  };
  const hrBand = bandOf("heart_rate");
  const rrBand = bandOf("respiratory_rate");
  const spo2Band = bandOf("spo2");
  // A model flag is a reason line on the row, but it does not put the patient where they are: the page
  // lists it apart, and "Sorted on NEWS2 only" stays true when no other reason exists.
  const reasons = patient.escalation_reasons.filter((r) => !MODEL_RULES.has(r.rule));
  const modelReasons = patient.escalation_reasons.filter((r) => MODEL_RULES.has(r.rule));
  const signal = patchInUse(patient.vitals) ? patient.signal_quality : null;
  const level = band(patient);
  const anyLab = LAB_ROWS.some(([key]) => patient.labs[key]);
  const cannotReach = failedSince !== null && `Cannot reach the ward server since ${formatClock(since(failedSince))}.`;

  const status = (
    <>
      {sim !== null && (
        <span className={styles.statusItem} role="group" aria-label="Ward clock">
          <span className={styles.statusLabel}>Ward clock</span>{" "}
          <span className={styles.statusValue}><Flip>{formatSimClock(sim)}</Flip></span>
        </span>
      )}
      <span className={styles.statusItem} data-tone={signal === null ? "warn" : undefined}>
        <Icon name="signal" />
        {signal === null ? "No patch signal" : `Signal ${signal}%`}
      </span>
      <span className={styles.statusItem} data-wide="">{formatClockSeconds(now)}</span>
      <span className={styles.statusItem} data-wide="">{patient.id}</span>
    </>
  );

  return (
    <AppShell session={session} onSignOut={onSignOut} title={patient.name} status={status}>
      <SimulatedNote />
      <div className={styles.container}>
        <div className={styles.top}>
          <Crumbs current={patient.name} />
          <div className={styles.actions}>
            {/* The assistant's panel (the chat bubble) opens on this patient's scope. Not for an admin, who cannot ask. */}
            {hasPatientQuestions && !readOnly && (
              <button type="button" className="hw-button hw-button--sm" onClick={() => window.dispatchEvent(new Event(OPEN_CHAT))}>
                <Icon name="comments" />
                Ask the assistant
              </button>
            )}
            <button type="button" className="hw-button hw-button--sm" aria-expanded={drawer === "notes"} onClick={() => toggleDrawer("notes")}>
              <Icon name="note-sticky" />
              Notes{notes.length > 0 ? ` (${notes.length})` : ""}
            </button>
            <button type="button" className="hw-button hw-button--sm" aria-expanded={drawer === "charts"} onClick={() => toggleDrawer("charts")}>
              <Icon name="chart-line" />
              Charts
            </button>
          </div>
        </div>

        {/* Summary: NEWS2, the next step, the signal and why the patient is up (no rank, no tier badge) */}
        <div className={styles.summaryBar}>
          {unranked !== null && (
            <p className={`hw-alert ${styles.line}`}>
              <span className="hw-alert__body">No longer ranked. The figures below are from before this entry.</span>
            </p>
          )}
          {/* Here, not in a panel: the big score must not look current when it is not. */}
          {cannotReach && (
            <p className={`hw-alert hw-alert--warning ${styles.line}`} role="status">
              <Icon name="triangle-exclamation" alert />
              <span className="hw-alert__body">{cannotReach}</span>
            </p>
          )}
          {staleSince !== null && unranked === null && (
            <p className={`hw-alert hw-alert--warning ${styles.line}`} role="status">
              <Icon name="triangle-exclamation" alert />
              <span className="hw-alert__body">
                No longer on your ranked list. Figures from {formatClock(lastGood ?? since(staleSince))}.{" "}
                <Link to="/ward">Back to ward</Link>
              </span>
            </p>
          )}
          <div className={styles.summary}>
            <div className={`hw-stat ${styles.stat}`} data-band={level}>
              <span className="hw-stat__label">NEWS2</span>
              <span className={`hw-stat__value ${styles.big}`}><Flip>{patient.score}</Flip></span>
            </div>
            {level !== "low" && (
              <div className={`hw-stat ${styles.stat}`} data-band={level} data-kind="response">
                <span className="hw-stat__label">Next step</span>
                <span className={styles.step}>{response(level, role)}</span>
              </div>
            )}
            {level === "low" && reasons.some((r) => r.rule === "tier_b_finding") && (
              <div className={`hw-stat ${styles.stat}`} data-band="low" data-kind="response">
                <span className="hw-stat__label">Next step</span>
                <span className={styles.step}>{driftResponse(role, patient.score)}</span>
              </div>
            )}
            <div className={`hw-stat ${styles.stat}`} data-signal={signal === null ? "none" : signal > 90 ? "good" : "degraded"}>
              <span className="hw-stat__label">Signal</span>
              <span className={`hw-stat__value ${styles.big}`}>{signal === null ? "No patch" : `${signal}%`}</span>
            </div>
            <div className={`hw-stat ${styles.stat} ${styles.why}`}>
              <span className="hw-stat__label">Why this patient is up</span>
              {reasons.length > 0 ? (
                <div className={styles.tags}>
                  {reasons.map((r, i) => (
                    <span key={i} className={`hw-tag ${styles.reason}`}>
                      {r.text}
                      {r.pathway && <span className={styles.pathway}> ({r.pathway})</span>}
                    </span>
                  ))}
                </div>
              ) : (
                <span className={styles.none}>Sorted on NEWS2 only</span>
              )}
              {modelReasons.length > 0 && (
                <>
                  <span className={`hw-stat__label ${styles.modelLabel}`}>Prediction models · rank unchanged</span>
                  <div className={styles.tags}>
                    {modelReasons.map((r) => (
                      <span key={r.rule} className={`hw-tag ${styles.reason}`}>{r.text}</span>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        {readOnly && (
          <p className={`hw-alert ${styles.line} ${styles.readOnly}`}>
            <span className="hw-alert__body">Read-only. Views are recorded in the audit log.</span>
          </p>
        )}

        {/* Notes and charts: a drawer from the right (full screen on a phone) */}
        {drawer && (
          <dialog
            ref={dialog}
            className="hw-dialog hw-dialog--drawer"
            aria-labelledby={drawerTitle}
            onClose={() => setDrawer(null)}
            onCancel={(e) => {
              e.preventDefault(); // Esc: animate out, then close
              void closeDialog(e.currentTarget);
            }}
            onClick={(e) => {
              if (e.target === e.currentTarget) void closeDialog(e.currentTarget); // the backdrop
            }}
          >
            <div className="hw-dialog__header">
              <h2 id={drawerTitle} className="hw-dialog__title">
                {drawer === "notes" ? `Clinical notes${notes.length > 0 ? ` · ${notes.length}` : ""}` : trendTitle(patient.vital_history)}
              </h2>
              <button type="button" className="hw-close" aria-label={`Close ${drawer}`} onClick={(e) => void closeDialog(e.currentTarget.closest("dialog"))}>
                <Icon name="xmark" />
              </button>
            </div>
            <div className={`hw-dialog__body ${styles.drawerBody}`}>
              {cannotReach && (
                <p className="hw-alert hw-alert--warning">
                  <Icon name="triangle-exclamation" alert />
                  <span className="hw-alert__body">{cannotReach}</span>
                </p>
              )}

              {drawer === "notes" && (
                <>
                  {!readOnly && (
                    <div className={styles.noteInput}>
                      <div className="hw-textarea">
                        <textarea
                          className="hw-textarea__control"
                          placeholder="Add a clinical note..."
                          value={noteText}
                          onChange={(e) => setNoteText(e.target.value)}
                          rows={2}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAddNote();
                          }}
                        />
                      </div>
                      <button
                        type="button"
                        className="hw-button hw-button--primary"
                        onClick={handleAddNote}
                        disabled={!noteText.trim() || submitting}
                      >
                        Add note
                      </button>
                    </div>
                  )}
                  {noteError !== null && <p role="alert" className="hw-alert hw-alert--danger"><span className="hw-alert__body">{noteError}</span></p>}
                  {notes.length > 0 && (
                    <ol className="hw-timeline">
                      {notes.map((note) => (
                        <li key={note.id} className="hw-timeline__item">
                          <span className="hw-timeline__time">{formatClock(Date.parse(note.timestamp))}</span>
                          <span className={styles.noteMeta}>
                            <span className={styles.noteAuthor}>{note.author}</span>
                            <span className="hw-tag">{note.role}</span>
                          </span>
                          <p className={styles.noteContent}>{note.content}</p>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}

              {drawer === "charts" && (
                <>
                  <TrendChart
                    data={patient.vital_history.heart_rate}
                    label="Heart rate"
                    unit="bpm"
                    warningHigh={90}
                    warningLow={51}
                    band={hrBand}
                  />
                  <TrendChart
                    data={patient.vital_history.respiratory_rate}
                    label="Respiratory rate"
                    unit="/min"
                    warningHigh={20}
                    warningLow={12}
                    band={rrBand}
                  />
                  <TrendChart
                    data={patient.vital_history.spo2}
                    label="SpO2"
                    unit="%"
                    warningLow={94}
                    band={spo2Band}
                  />
                  {(hrBand || rrBand || spo2Band) && (
                    <p className="hw-caption">
                      Own baseline: this patient's own baseline (synthetic, not validated). Not a normal range.
                    </p>
                  )}
                  <p className="hw-caption">
                    Charts show patch data only. Values entered by a nurse are not plotted.
                  </p>
                </>
              )}
            </div>
          </dialog>
        )}

        <div className={styles.layout}>
          {/* Left: identity, score and the research panels, all open (density over progressive disclosure) */}
          <div className={styles.column}>
            <div className="hw-card hw-card--outlined">
              <p className={styles.eyebrow}>Patient</p>
              <h1 className={styles.patientName}>{patient.name}</h1>
              <p className={styles.patientSub}>
                {patient.age ?? "--"} {patient.sex ?? "--"} &middot; Bed {patient.bed}
              </p>
              <p className={styles.admitting}>{patient.admitting_context}</p>
            </div>

            <div className="hw-card hw-card--outlined">
              <p className={styles.eyebrow}>NEWS2</p>
              <p className={styles.scoreBlock} data-band={level}>
                <span className={styles.scoreValue}>{patient.score}</span>
                <span className={styles.scoreLabel}>NEWS2</span>
              </p>
            </div>

            <div className="hw-card hw-card--outlined">
              <p className={styles.eyebrow}>Own baseline</p>
              <BaselineRange baseline={baseline} />
              <BaselinePanel baseline={baseline} />
              <ResearchModels models={patient.models} />
            </div>
          </div>

          {/* Right: vitals, the stay, asking, labs (always visible) */}
          <div className={styles.column}>
            <div className="hw-card hw-card--outlined">
              <p className={styles.eyebrow}>Current vitals</p>
              <dl className={styles.vitalsGrid}>
                <CurrentVitals vitals={patient.vitals} now={now} />
              </dl>
              {canRecordVitals && !readOnly && (
                <button type="button" className={`hw-button hw-button--sm ${styles.record}`} aria-expanded={entering} onClick={() => setEntering((open) => !open)}>
                  <Icon name="heart-pulse" />
                  Record vitals
                </button>
              )}
              {canRecordVitals && !readOnly && entering && (
                <VitalsEntry
                  patientId={patient.id}
                  patientName={patient.name}
                  clinicianId={session.clinician_id}
                  onRecorded={handleRecorded}
                  onClose={() => setEntering(false)}
                />
              )}
              {refreshFailed && (
                <p role="alert" className={`hw-alert hw-alert--danger ${styles.after}`}>
                  <span className="hw-alert__body">Recorded. The page could not refresh; reload to see the new score.</span>
                </p>
              )}
              {unranked !== null && (
                <p role="status" className={`hw-alert ${styles.after}`}>
                  <span className="hw-alert__body">
                    {unranked} <Link to="/ward">Back to ward</Link>
                  </span>
                </p>
              )}
            </div>

            {hasHistory && (
              <div className="hw-card hw-card--outlined">
                <StayPanel key={patient.id} patientId={patient.id} onSignOut={onSignOut} now={now} />
              </div>
            )}

            <div className="hw-card hw-card--outlined">
              <p className={styles.eyebrow}>
                Labs
                {patient.labs.freshness_hours !== null &&
                  ` · ${patient.labs.freshness_hours}h since collection`}
              </p>
              {anyLab ? (
                <dl className={styles.vitalsGrid}>
                  {LAB_ROWS.map(([key, label, unit]) => {
                    const m = patient.labs[key];
                    return m && <VitalRow key={key} now={now} label={label} measurement={m} unit={unit} />;
                  })}
                </dl>
              ) : (
                <p className={styles.quiet}>No lab result yet.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
