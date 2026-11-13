import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useParams, Link } from "react-router-dom";
import type { ClinicalNote, PatientDetail as PatientDetailType, Session, SessionRole, VitalsRecorded } from "../types";
import { getPatient, getNotes, addNote, canRecordVitals, usesServer } from "../api";
import { SessionMenu } from "../components/SessionMenu";
import { SimulatedNote } from "../components/LabFrame";
import { BaselinePanel, isComputed, isObj, num } from "../components/BaselinePanel";
import { useBaseline, type BaselineState } from "../components/useBaseline";
import { CurrentVitals, VitalRow } from "../components/CurrentVitals";
import { TrendChart } from "../components/TrendChart";
import { formatClock, formatSimClock, patchInUse } from "../components/format";
import { useLivePoll } from "../components/useLivePoll";
import { band, driftResponse, response } from "../components/risk";
import { VitalsEntry } from "../components/VitalsEntry";
import { standing } from "../components/vitalsDraft";
import styles from "./PatientDetail.module.css";

// --- Shared sub-components ---

// The patient's own band for one vital, from the answer the page already loaded. An older server
// without the two fields, a vital still forming, a build without the route and an answer of an
// odd shape give no band (the panel says the baseline could not be loaded; the page never blanks).
function bandOf(baseline: BaselineState, vital: string) {
  const b: unknown = baseline;
  if (!isObj(b) || !Array.isArray(b.vitals)) return undefined;
  const v: unknown = b.vitals.find((x) => isObj(x) && x.vital === vital);
  if (!isObj(v) || v.status !== "computed" || !isComputed(v) || !num(v.band_low) || !num(v.band_high)) return undefined;
  return { low: v.band_low, high: v.band_high, flagged: v.significant };
}

// The read serves every stored bin, so the title says the span of what is drawn.
function trendTitle(history: PatientDetailType["vital_history"]) {
  const ms = Object.values(history).flatMap((pts) => pts.map((p) => Date.parse(p.time))).filter(Number.isFinite);
  if (ms.length === 0) return "Vital trends";
  const h = Math.floor((Math.max(...ms) - Math.min(...ms)) / 3_600_000);
  return `Vital trends \u00b7 ${h < 1 ? "under 1 h" : `${h} h`}`;
}

type Drawer = "notes" | "charts" | null;

const ROLE_LABEL: Record<SessionRole, string> = { rn: "RN", charge_rn: "Charge RN", md: "MD", admin: "Admin" };

// The top strip and quiet line for the states with no patient to show.
function Bare({ session, onSignOut, children }: { session: Session; onSignOut: () => void; children?: ReactNode }) {
  return (
    <div className={styles.page}>
      <div className={styles.statusStrip}>
        <div className={styles.stripInner}>
          <Link to="/ward" className={styles.back}>&larr; Ward view</Link>
          <div className={styles.stripRight}>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>
      <SimulatedNote />
      <main>{children}</main>
    </div>
  );
}

// --- Main component ---

export function PatientDetail({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const role = session.role;
  const readOnly = role === "admin"; // an admin reads the page: every write control is absent
  const { id } = useParams<{ id: string }>();
  const [saves, setSaves] = useState(0);
  const baseline = useBaseline(id, saves);
  const [patient, setPatient] = useState<PatientDetailType | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [notes, setNotes] = useState<ClinicalNote[]>([]);
  const [noteText, setNoteText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [loadError, setLoadError] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [entering, setEntering] = useState(false);
  const [unranked, setUnranked] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);

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
          setPatient(p);
          setLastGood(Date.now());
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
    // A patient who is now NOT RANKED has no patient read: it would 404 and say "not found".
    if (!id) return;
    setRefreshFailed(false);
    setSaves((n) => n + 1);
    if (recorded.ranked) {
      setUnranked(null);
      void loadPatient().then((ok) => ok === false && setRefreshFailed(true));
    } else setUnranked(`Recorded. ${standing(recorded)}`);
  };

  const toggleDrawer = (d: Drawer) => setDrawer((prev) => prev === d ? null : d);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (notFound) {
    return (
      <Bare session={session} onSignOut={onSignOut}>
        <p>Patient not found.</p>
        <Link to="/ward">Back to ward</Link>
      </Bare>
    );
  }

  if (loadError) {
    return (
      <Bare session={session} onSignOut={onSignOut}>
        <p role="alert">This patient could not be loaded. Check the connection and reload.</p>
        <Link to="/ward">Back to ward</Link>
      </Bare>
    );
  }

  if (!patient) return <Bare session={session} onSignOut={onSignOut} />;

  const hrBand = bandOf(baseline, "heart_rate");
  const rrBand = bandOf(baseline, "respiratory_rate");
  const spo2Band = bandOf(baseline, "spo2");
  const reasons = patient.escalation_reasons;
  const signal = patchInUse(patient.vitals) ? patient.signal_quality : null;

  return (
    <div className={styles.page}>
      {/* Status strip */}
      <div className={styles.statusStrip}>
        <div className={styles.stripInner}>
          <Link to="/ward" className={styles.back}>&larr; Ward view</Link>
          <div className={styles.stripRight}>
            {sim !== null && (
              <span className={styles.stripSignal} role="group" aria-label="Ward clock">
                Ward clock {formatSimClock(sim)}
              </span>
            )}
            <span className={styles.stripSignal}>
              {signal === null ? "No patch signal" : `Signal ${signal}%`}
            </span>
            <span className={styles.stripUpdate}>
              {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
            <span className={styles.stripId}>{patient.id}</span>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>
      <SimulatedNote />

      {/* Summary bar: NEWS2 + escalation reasons (no rank, no tier badge) */}
      <div className={styles.summaryBar}>
        {unranked !== null && (
          <p className={styles.summaryLabel}>No longer ranked. The figures below are from before this entry.</p>
        )}
        {/* Here, not in a panel: the big score must not look current when it is not. */}
        {failedSince !== null && (
          <p className={styles.summaryLabel} role="status">Cannot reach the ward server since {formatClock(failedSince)}.</p>
        )}
        {staleSince !== null && unranked === null && (
          <p className={styles.summaryLabel} role="status">
            No longer on your ranked list. Figures from {formatClock(lastGood ?? staleSince)}.{" "}
            <Link to="/ward">Back to ward</Link>
          </p>
        )}
        <div className={styles.summaryRow}>
          <div className={styles.summaryInner}>
            <div className={styles.summaryBlock} data-band={band(patient)}>
              <span className={styles.summaryLabel}>NEWS2</span>
              <span className={styles.summaryBig}>{patient.score}</span>
            </div>
            {band(patient) !== "low" && (
              <div className={styles.summaryBlock} data-band={band(patient)} data-kind="response">
                <span className={styles.summaryLabel}>Next step</span>
                <span className={styles.responseText}>{response(band(patient), role)}</span>
              </div>
            )}
            {band(patient) === "low" && reasons.some((r) => r.rule === "tier_b_finding") && (
              <div className={styles.summaryBlock} data-band="low" data-kind="response">
                <span className={styles.summaryLabel}>Next step</span>
                <span className={styles.responseText}>{driftResponse(role, patient.score)}</span>
              </div>
            )}
            <div className={styles.summaryBlock} data-signal={signal === null ? "none" : signal > 90 ? "good" : "degraded"}>
              <span className={styles.summaryLabel}>Signal</span>
              <span className={styles.summaryBig}>{signal === null ? "No patch" : `${signal}%`}</span>
            </div>
            <div className={styles.reasonBlock}>
              <span className={styles.summaryLabel}>Why this patient is up</span>
              {reasons.length > 0 ? (
                <div className={styles.reasonList}>
                  {reasons.map((r, i) => (
                    <span key={i} className={styles.reasonTag}>
                      {r.text}
                      {r.pathway && <span className={styles.reasonPathway}> ({r.pathway})</span>}
                    </span>
                  ))}
                </div>
              ) : (
                <span className={styles.reasonNone}>Sorted on NEWS2 only</span>
              )}
            </div>
          </div>
          <div className={styles.drawerButtons}>
            <button
              type="button"
              className={`${styles.drawerBtn} ${drawer === "notes" ? styles.drawerBtnActive : ""}`}
              onClick={() => toggleDrawer("notes")}
            >
              Notes{notes.length > 0 ? ` (${notes.length})` : ""}
            </button>
            <button
              type="button"
              className={`${styles.drawerBtn} ${drawer === "charts" ? styles.drawerBtnActive : ""}`}
              onClick={() => toggleDrawer("charts")}
            >
              Charts
            </button>
          </div>
        </div>
      </div>
      {readOnly && <p className={styles.readOnly}>Read-only. Views are recorded in the audit log.</p>}

      {/* Overlay panels */}
      {drawer && (
        <div className={styles.overlay}>
          <div className={styles.overlayBackdrop} onClick={() => setDrawer(null)} />
          <div className={styles.overlayPanel}>
            <div className={styles.overlayHeader}>
              <span className={styles.overlayTitle}>
                {drawer === "notes" ? `Clinical notes${notes.length > 0 ? ` \u00b7 ${notes.length}` : ""}` : trendTitle(patient.vital_history)}
              </span>
              {failedSince !== null && (
                <span>Cannot reach the ward server since {formatClock(failedSince)}.</span>
              )}
              <button type="button" className={styles.overlayClose} onClick={() => setDrawer(null)}>&times;</button>
            </div>

            {drawer === "notes" && (
              <>
                {!readOnly && <div className={styles.noteInput}>
                  <textarea
                    className={styles.noteTextarea}
                    placeholder="Add a clinical note..."
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    rows={2}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleAddNote();
                    }}
                  />
                  <button
                    className={styles.noteSubmit}
                    onClick={handleAddNote}
                    disabled={!noteText.trim() || submitting}
                  >
                    Add note
                  </button>
                </div>}
                {noteError !== null && <p role="alert">{noteError}</p>}
                {notes.length > 0 && (
                  <div className={styles.notesList}>
                    {notes.map((note) => (
                      <div key={note.id} className={styles.noteItem}>
                        <div className={styles.noteMeta}>
                          <span className={styles.noteAuthor}>{note.author}</span>
                          <span className={styles.noteRole}>{note.role}</span>
                          <span className={styles.noteTime}>
                            {new Date(note.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </div>
                        <p className={styles.noteContent}>{note.content}</p>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {drawer === "charts" && (
              <div className={styles.chartGrid}>
                <TrendChart
                  data={patient.vital_history.heart_rate}
                  label="Heart rate"
                  color="var(--trace-hr)"
                  unit="bpm"
                  warningHigh={100}
                  warningLow={60}
                  band={hrBand}
                />
                <TrendChart
                  data={patient.vital_history.respiratory_rate}
                  label="Respiratory rate"
                  color="var(--trace-rr)"
                  unit="/min"
                  warningHigh={20}
                  warningLow={12}
                  band={rrBand}
                />
                <TrendChart
                  data={patient.vital_history.spo2}
                  label="SpO2"
                  color="var(--trace-spo2)"
                  unit="%"
                  warningLow={94}
                  band={spo2Band}
                />
                {(hrBand || rrBand || spo2Band) && (
                  <p className={styles.pending}>
                    Shaded: this patient's own baseline (synthetic, not validated). Not a normal range.
                  </p>
                )}
                <p className={styles.pending}>
                  Charts show patch data only. Values entered by a nurse are not plotted.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      <div className={styles.layout}>
        {/* Left: identity + tiers — all panels always open (density over progressive disclosure) */}
        <div className={styles.sidebar}>
          <div className={styles.panel}>
            <p className={styles.panelEyebrow}>Patient</p>
            <h1 className={styles.patientName}>{patient.name}</h1>
            <p className={styles.patientSub}>
              {patient.age ?? "--"} {patient.sex ?? "--"} &middot; Bed {patient.bed}
            </p>
            <p className={styles.admitting}>{patient.admitting_context}</p>
          </div>

          <div className={styles.panel}>
            <p className={styles.panelEyebrow}>Tier A &mdash; Reference score</p>
            <div className={styles.scoreBlock}>
              <span className={styles.scoreValue}>{patient.score}</span>
              <span className={styles.scoreLabel}>NEWS2</span>
            </div>
          </div>

          <div className={styles.panel}>
            <p className={styles.panelEyebrow}>Tier B &mdash; Mechanism findings</p>
            <p className={styles.pending}>Other mechanism findings: not yet implemented</p>
            <BaselinePanel baseline={baseline} />
          </div>

        </div>

        {/* Right: vitals + labs (always visible) */}
        <div className={styles.main}>
          <div className={styles.panel}>
            <p className={styles.panelEyebrow}>Current vitals</p>
            <div className={styles.vitalsGrid}>
              <CurrentVitals vitals={patient.vitals} />
            </div>
            {canRecordVitals && !readOnly && (
              <button type="button" className={styles.drawerBtn} aria-expanded={entering} onClick={() => setEntering((open) => !open)}>
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
              <p role="alert">Recorded. The page could not refresh; reload to see the new score.</p>
            )}
            {unranked !== null && (
              <p role="status">
                {unranked} <Link to="/ward">Back to ward</Link>
              </p>
            )}
          </div>

          <div className={styles.panel}>
            <p className={styles.panelEyebrow}>
              Labs
              {patient.labs.freshness_hours !== null &&
                ` \u00b7 ${patient.labs.freshness_hours}h since collection`}
            </p>
            <div className={styles.vitalsGrid}>
              {patient.labs.troponin && <VitalRow label="Troponin" measurement={patient.labs.troponin} unit="ng/mL" />}
              {patient.labs.ck_mb && <VitalRow label="CK-MB" measurement={patient.labs.ck_mb} unit="U/L" />}
              {patient.labs.bnp && <VitalRow label="BNP" measurement={patient.labs.bnp} unit="pg/mL" />}
              {patient.labs.nt_probnp && <VitalRow label="NT-proBNP" measurement={patient.labs.nt_probnp} unit="pg/mL" />}
              {patient.labs.sodium && <VitalRow label="Sodium" measurement={patient.labs.sodium} unit="mEq/L" />}
              {patient.labs.potassium && <VitalRow label="Potassium" measurement={patient.labs.potassium} unit="mEq/L" />}
              {patient.labs.magnesium && <VitalRow label="Magnesium" measurement={patient.labs.magnesium} unit="mg/dL" />}
              {patient.labs.creatinine && <VitalRow label="Creatinine" measurement={patient.labs.creatinine} unit="mg/dL" />}
              {patient.labs.bun && <VitalRow label="BUN" measurement={patient.labs.bun} unit="mg/dL" />}
              {patient.labs.hemoglobin && <VitalRow label="Hemoglobin" measurement={patient.labs.hemoglobin} unit="g/L" />}
              {patient.labs.wbc && <VitalRow label="WBC" measurement={patient.labs.wbc} unit="x10^9/L" />}
              {patient.labs.platelet_count && <VitalRow label="Platelets" measurement={patient.labs.platelet_count} unit="/mm^3" />}
              {patient.labs.lactate && <VitalRow label="Lactate" measurement={patient.labs.lactate} unit="mmol/L" />}
              {patient.labs.sgpt && <VitalRow label="SGPT" measurement={patient.labs.sgpt} unit="U/L" />}
              {patient.labs.sgot && <VitalRow label="SGOT" measurement={patient.labs.sgot} unit="U/L" />}
              {patient.labs.blood_glucose && <VitalRow label="Blood glucose" measurement={patient.labs.blood_glucose} unit="mg/dL" />}
              {patient.labs.inr && <VitalRow label="INR" measurement={patient.labs.inr} unit="" />}
              {!patient.labs.troponin && !patient.labs.bnp && !patient.labs.potassium &&
               !patient.labs.creatinine && !patient.labs.hemoglobin && (
                <p className={styles.pending}>No lab values recorded</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
