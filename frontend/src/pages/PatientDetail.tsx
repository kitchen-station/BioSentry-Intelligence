import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { closeDialog } from "hw-ui";
import type { ClinicalNote, PatientDetail as PatientDetailType, Session, SessionRole, VitalHistory, VitalsRecorded } from "../types";
import { getPatient, getNotes, addNote, canRecordVitals, hasHistory, hasPatientQuestions, usesServer } from "../api";
import { Flip } from "../components/Flip";
import { AppShell, OfflineBanner, usePhone } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { PatientHistoryArea } from "../components/PatientHistoryArea";
import { StayPanel } from "../components/StayPanel";
import { since, syncWardNow, useWardNow } from "../components/wardNow";
import { BaselinePanel, BaselineRange, bands } from "../components/BaselinePanel";
import { ResearchModels } from "../components/ResearchModels";
import { useBaseline } from "../components/useBaseline";
import { CurrentVitals, VitalRow } from "../components/CurrentVitals";
import { TrendChart, drawable } from "../components/TrendChart";
import { formatAge, formatClock, formatClockSeconds, formatSimClock, patchInUse } from "../components/format";
import { useLivePoll } from "../components/useLivePoll";
import { PressureChart } from "../components/PressureChart";
import { addsPoints, chartLimits, zeroBand, type Charted } from "../components/news2";
import { band, driftResponse, obsDue, response } from "../components/risk";
import { openAssistant } from "../components/roles";
import { BAND_WORD } from "../components/Score";
import { Segmented } from "../components/Segmented";
import { VitalsEntry } from "../components/VitalsEntry";
import { standing } from "../components/vitalsDraft";
import { dataAge, obsText } from "../components/WardRow";
import { useWardFeed } from "../components/wardFeed";
import styles from "./PatientDetail.module.css";

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

// Temperature has no NEWS2 line drawn on its chart here; blood pressure is the PressureChart.
const EXTRA_CHARTS = [{ key: "temperature", label: "Temperature", unit: "°C", range: [35, 41] as [number, number] }] as const;

// The three NEWS2-charted vitals, each on a clinical y-range rather than from zero.
const CHARTS: readonly { key: Charted; label: string; unit: string; range: [number, number] }[] = [
  { key: "heart_rate", label: "Heart rate", unit: "bpm", range: [40, 140] },
  { key: "respiratory_rate", label: "Respiratory rate", unit: "/min", range: [8, 30] },
  { key: "spo2", label: "SpO2", unit: "%", range: [85, 100] },
];

type Span = "1h" | "6h" | "24h" | "stay";
const SPANS: readonly (readonly [Span, string])[] = [["1h", "1 h"], ["6h", "6 h"], ["24h", "24 h"], ["stay", "Stay"]];
const SPAN_MS: Record<Span, number> = { "1h": 3_600_000, "6h": 6 * 3_600_000, "24h": 24 * 3_600_000, stay: Infinity };

/**
 * The stored readings within `ms` of the newest one. Counted from the newest reading, not the clock: the
 * last hour of a patch that stopped still shows, and its times say when.
 */
export function lastSpan<T extends VitalHistory>(points: readonly T[], ms: number): readonly T[] {
  if (ms === Infinity) return points;
  const end = Math.max(...points.map((p) => Date.parse(p.time)).filter(Number.isFinite));
  return points.filter((p) => Date.parse(p.time) > end - ms);
}

type Tab = "now" | "trends" | "labs" | "notes";
const TABS: readonly (readonly [Tab, string])[] = [["now", "Now"], ["trends", "Trends"], ["labs", "Labs"], ["notes", "Notes"]];

const MODEL_RULES = new Set<string>(["prediction_model_flag"]);

const ROLE_LABEL: Record<SessionRole, string> = { rn: "RN", charge_rn: "Charge RN", md: "MD", admin: "Admin" };

// An icon is a span, never <i>, and never carries meaning alone.
const Icon = ({ name, alert }: { name: string; alert?: boolean }) => (
  <span className={`${alert ? "hw-alert__icon " : ""}hw-icon fa-solid fa-${name}`} aria-hidden="true" />
);

// The way back to the ward, above the content.
function Crumbs({ ward, current }: { ward: string; current: string }) {
  return (
    <nav aria-label="Breadcrumb" className={styles.crumbs}>
      <ol className="hw-breadcrumb">
        <li><Link to="/ward">Ward {ward}</Link></li>
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
        <Crumbs ward={session.ward} current="Patient" />
        {children}
      </div>
    </AppShell>
  );
}

type Props = { session: Session; onSignOut: () => void };

/** The patient page. Each patient is its own mount: Next and Previous start the next one clean. */
export function PatientDetail(props: Props) {
  const { id } = useParams<{ id: string }>();
  return <Patient key={id} id={id} {...props} />;
}

function Patient({ id, session, onSignOut }: Props & { id: string | undefined }) {
  const role = session.role;
  const readOnly = role === "admin"; // an admin reads the page: every write control is absent
  const [saves, setSaves] = useState(0);
  const baseline = useBaseline(id, saves);
  const [patient, setPatient] = useState<PatientDetailType | null>(null);
  const [notFound, setNotFound] = useState(false);
  const now = useWardNow(); // ward time as served at the last good read
  const [notes, setNotes] = useState<ClinicalNote[]>([]);
  const [noteText, setNoteText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [unranked, setUnranked] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [span, setSpan] = useState<Span>("6h");
  const [tab, setTab] = useState<Tab>("now");
  const phone = usePhone();
  const feed = useWardFeed();
  const navigate = useNavigate();
  const dialog = useRef<HTMLDialogElement>(null);
  const drawerTitle = useId();
  const ids = useId();

  // The first read decides "not found" or "could not be loaded". A timed read only replaces the
  // patient: it never touches the drawers or the note box, and says what went wrong in one line.
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
  const { sim, pollMs } = useLivePoll(loadPatient, usesServer && !readOnly, staleSince !== null || unranked !== null);

  useEffect(() => {
    if (id && !readOnly) getNotes(id).then(setNotes).catch(() => setNoteError("Notes could not be loaded."));
  }, [id, readOnly]);

  // An admin's page reads the notes only when they are opened: one audited read per real look.
  const notesShown = notesOpen || (phone && tab === "notes");
  useEffect(() => {
    if (!readOnly || !notesShown || !id) return;
    getNotes(id).then(setNotes).catch(() => setNoteError("Notes could not be loaded."));
  }, [readOnly, notesShown, id]);

  // The notes drawer is a native modal <dialog>: focus is held inside it and Esc closes it.
  useEffect(() => {
    if (notesOpen && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [notesOpen]);

  // Previous and Next walk the ward's own ranked order. Without the ward's read (an administrator) there is no order to walk.
  const order = feed?.data?.ranked ?? null;
  const at = order ? order.findIndex((r) => r.id === id) : -1;
  const prevId = order && at > 0 ? order[at - 1].id : null;
  const nextId = order && at >= 0 && at < order.length - 1 ? order[at + 1].id : null;
  const go = useCallback((to: string | null) => void (to && navigate(`/patient/${to}`)), [navigate]);
  // K and J, as on the ward's other lists, wherever the focus is not in a field or a dialog.
  useEffect(() => {
    if (!order || !patient) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      if ((e.target as Element).closest?.("input, textarea, select, [contenteditable], dialog, [role=dialog]")) return;
      const key = e.key.toLowerCase();
      if (key === "j") go(nextId);
      else if (key === "k") go(prevId);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [order, patient, prevId, nextId, go]);

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
    setRefreshFailed(false);
    setSaves((n) => n + 1);
    if (recorded.ranked) {
      setUnranked(null);
      void loadPatient().then((ok) => ok === false && setRefreshFailed(true));
    } else setUnranked(`Recorded. ${standing(recorded)}`);
  };

  const onTabKey = (e: KeyboardEvent) => {
    const i = TABS.findIndex(([t]) => t === tab);
    const n = TABS.length;
    const to = { ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    setTab(TABS[to][0]);
    document.getElementById(`${ids}-${TABS[to][0]}`)?.focus();
  };

  if (notFound) {
    return (
      <Bare session={session} onSignOut={onSignOut}>
        <p className={styles.bare}>Patient not found.</p>
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

  // The patient's own band for each charted vital, from the answer the page already loaded.
  const own = bands(baseline);
  const bandOf = (vital: string) => {
    const v = own.find((x) => x.vital === vital);
    return v && { low: v.band_low, high: v.band_high, flagged: v.significant };
  };
  // A model flag is a reason line on the row, but it does not put the patient where they are: the page
  // lists it apart, and "Sorted on NEWS2 only" stays true when no other reason exists.
  const reasons = patient.escalation_reasons.filter((r) => !MODEL_RULES.has(r.rule));
  const modelReasons = patient.escalation_reasons.filter((r) => MODEL_RULES.has(r.rule));
  const signal = patchInUse(patient.vitals) ? patient.signal_quality : null;
  const level = band(patient);
  const step = level !== "low" ? response(level, role) : reasons.some((r) => r.rule === "tier_b_finding") ? driftResponse(role, patient.score) : null;
  const anyLab = LAB_ROWS.some(([key]) => patient.labs[key]);
  // Offline: this page's own reads fail, or the ward's do. The last data stays and the writes wait.
  const offSince = failedSince ?? feed?.failedSince ?? null;
  const offline = offSince !== null;
  const canRecord = canRecordVitals && !readOnly;
  const alertRow = readOnly ? undefined : feed?.alerts.find((a) => a.id === patient.id);
  const age = dataAge(patient, Math.max(0, Math.floor((now - Date.parse(patient.now)) / 1000)), offline);
  const scale = patient.vitals.spo2_scale.data.present ? patient.vitals.spo2_scale.data.value : 1;
  const onO2 = patient.vitals.supplemental_o2.data.present && patient.vitals.supplemental_o2.data.value;
  const latest = notes.length > 0 ? notes.reduce((a, b) => (Date.parse(b.timestamp) > Date.parse(a.timestamp) ? b : a)) : null;
  const counted = notes.length > 0 ? ` (${notes.length})` : "";

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

  const pager = order && (
    <div className="hw-pager" role="group" aria-label="Move through patients in rank order">
      <button type="button" className="hw-button" disabled={!prevId} onClick={() => go(prevId)}>
        <Icon name="arrow-left" />
        Previous <kbd className="hw-pager__key" aria-hidden="true">K</kbd>
      </button>
      <span className="hw-pager__position">{at >= 0 ? `${at + 1} of ${order.length} in rank order` : "Not in the ranked order"}</span>
      <button type="button" className="hw-button" disabled={!nextId} onClick={() => go(nextId)}>
        <kbd className="hw-pager__key" aria-hidden="true">J</kbd> Next
        <Icon name="arrow-right" />
      </button>
    </div>
  );

  const record = canRecord && (
    <button type="button" className="hw-button hw-button--accent" aria-haspopup="dialog" disabled={offline} onClick={() => setRecording(true)}>
      <Icon name="heart-pulse" />
      Record vitals
    </button>
  );

  // The decision strip (kit hw-decision): the score and its band, the role's next step, why, and the signal. The read
  // carries no earlier score, so no change since an earlier time is shown: the trend's arrow only.
  const decision = (
    <section className={`hw-decision ${styles.decision}`} aria-label="Decision" data-band={level === "low-medium" ? "medium" : level}>
      <div className="hw-decision__cell">
        <p className="hw-decision__label">NEWS2</p>
        <div className="hw-decision__reading">
          <span className="hw-decision__score"><Flip>{patient.score}</Flip></span>
          <span className="hw-decision__meta">
            <span className="hw-decision__band">
              {BAND_WORD[level]}
              {patient.trend === "rising" && <><Icon name="arrow-up" /><span className="hw-visually-hidden">, rising</span></>}
            </span>
          </span>
        </div>
      </div>
      <div className="hw-decision__cell">
        <p className="hw-decision__label">Next step</p>
        {step !== null ? (
          <p className="hw-decision__step">{step}</p>
        ) : (
          // Low with no trigger: no step, and when the next observations are due, as the ward row says it.
          <>
            <p className="hw-decision__step">&mdash;</p>
            <p className="hw-decision__note">{obsText(obsDue(patient), now, patient.last_update_s === null || signal === null)}</p>
          </>
        )}
      </div>
      <div className="hw-decision__cell">
        <p className="hw-decision__label">Why this patient is up</p>
        {reasons.length > 0 ? (
          <ul className="hw-decision__reasons">
            {reasons.map((r, i) => (
              <li key={i} className={`hw-tag ${styles.reason}`}>
                {r.text}
                {r.pathway && <span className={styles.pathway}> ({r.pathway})</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="hw-decision__text">Sorted on NEWS2 only</p>
        )}
        {modelReasons.length > 0 && (
          <p className="hw-decision__model">
            <Icon name="flask" />
            <span>
              <strong>Prediction models &middot; rank unchanged.</strong> <span>{modelReasons.map((r) => r.text).join(" · ")}</span>
            </span>
          </p>
        )}
      </div>
      <div className="hw-decision__cell" data-signal={signal === null ? "none" : signal > 90 ? "good" : "degraded"}>
        <p className="hw-decision__label">Signal</p>
        <p className="hw-decision__signal">{signal === null ? "No patch" : `${signal}%`}</p>
        {signal !== null && age && <p className="hw-decision__note" data-stale={age.red || undefined}>{age.text}</p>}
      </div>
    </section>
  );

  const trends = (
    <section className={`hw-card hw-card--outlined ${styles.trends}`} aria-labelledby={`${ids}-trends`}>
      <div className={styles.cardHead}>
        <h2 id={`${ids}-trends`} className={styles.h2}>Trends</h2>
        <Segmented label="Time range" value={span} onChange={setSpan} options={SPANS} />
      </div>
      {CHARTS.map(({ key, label, unit, range }) => {
        const data = lastSpan(patient.vital_history[key], SPAN_MS[span]);
        const [lo, hi] = zeroBand(key, scale, onO2);
        return drawable(data) ? (
          <TrendChart
            key={key}
            data={data}
            label={label}
            unit={unit}
            warningHigh={hi ?? undefined}
            warningLow={lo ?? undefined}
            band={bandOf(key)}
            range={range}
            limits={chartLimits(key, scale, onO2)}
            highlightWhere={addsPoints(key, scale, onO2)}
          />
        ) : (
          <p key={key} className={styles.quiet}>{label}: no stored readings in this range.</p>
        );
      })}
      {EXTRA_CHARTS.map(({ key, label, unit, range }) => {
        const data = lastSpan(patient.vital_history[key], SPAN_MS[span]);
        return drawable(data) ? (
          <TrendChart key={key} data={data} label={label} unit={unit} range={range} />
        ) : (
          <p key={key} className={styles.quiet}>{label}: no stored readings in this range.</p>
        );
      })}
      {(() => {
        const sys = lastSpan(patient.vital_history.systolic, SPAN_MS[span]);
        const keep = new Set(sys.map((p) => p.time));
        return sys.length >= 2 ? (
          <PressureChart systolic={sys} diastolic={patient.vital_history.diastolic.filter((p) => keep.has(p.time))} />
        ) : (
          <p className={styles.quiet}>Blood pressure: no stored readings in this range.</p>
        );
      })()}
      {CHARTS.some(({ key }) => bandOf(key)) && (
        <p className="hw-caption">Own baseline: this patient's own baseline (synthetic, not validated). Not a normal range.</p>
      )}
      <p className="hw-caption">Heart rate, respiratory rate, SpO2 and temperature are patch data. Blood pressure shows cuff and nurse readings, each labelled with its source.</p>
    </section>
  );

  const stay = hasHistory && (
    <div className="hw-card hw-card--outlined">
      <StayPanel key={patient.id} patientId={patient.id} onSignOut={onSignOut} now={now} />
    </div>
  );

  const vitals = (
    <section className="hw-card hw-card--outlined" aria-labelledby={`${ids}-vitals`}>
      <h2 id={`${ids}-vitals`} className={styles.h2}>Current vitals</h2>
      <dl className={styles.vitalsGrid}>
        <CurrentVitals vitals={patient.vitals} now={now} />
      </dl>
    </section>
  );

  const ownBaseline = (
    <section className="hw-card hw-card--outlined" aria-labelledby={`${ids}-own`}>
      <h2 id={`${ids}-own`} className={styles.h2}>Against own baseline</h2>
      <BaselineRange baseline={baseline} />
      <BaselinePanel baseline={baseline} />
      <ResearchModels models={patient.models} />
    </section>
  );

  const labs = (
    <section className="hw-card hw-card--outlined" aria-labelledby={`${ids}-labs`}>
      <div className={styles.cardHead}>
        <h2 id={`${ids}-labs`} className={styles.h2}>Labs</h2>
        {patient.labs.freshness_hours !== null && (
          <span className={styles.quiet}>Collected {formatAge(Math.round(patient.labs.freshness_hours * 3600))}</span>
        )}
      </div>
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
    </section>
  );

  const notesList = (
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
          <button type="button" className="hw-button hw-button--primary" onClick={handleAddNote} disabled={!noteText.trim() || submitting}>
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
  );

  // The newest note on the page; the drawer holds the full list and the note box.
  const latestNote = (
    <section id="notes" className="hw-card hw-card--outlined" aria-labelledby={`${ids}-note`}>
      <h2 id={`${ids}-note`} className={styles.h2}>Latest note</h2>
      {latest ? (
        <>
          <p className={styles.noteMeta}>
            {formatClock(Date.parse(latest.timestamp))} &middot; <span className={styles.noteAuthor}>{latest.author}</span>
            <span className="hw-tag">{latest.role}</span>
          </p>
          <p className={styles.noteContent}>{latest.content}</p>
        </>
      ) : (
        !readOnly && noteError === null && <p className={styles.quiet}>No notes yet for this patient.</p>
      )}
      <button type="button" className={`hw-button hw-button--sm ${styles.after}`} aria-haspopup="dialog" onClick={() => setNotesOpen(true)}>
        All notes{counted}
      </button>
    </section>
  );

  const offlineLine = offline && (
    <p className="hw-alert hw-alert--warning">
      <Icon name="triangle-exclamation" alert />
      <span className="hw-alert__body">Offline since {formatClock(since(offSince))}. Cannot reach the ward server.</span>
    </p>
  );

  return (
    <AppShell session={session} onSignOut={onSignOut} title={patient.name} status={status} detail>
      {offline && (
        <OfflineBanner className={styles.offline} since={offSince} from={lastGood} retry={`Retrying every ${pollMs / 1000} seconds.`} acts={!readOnly} />
      )}
      <SimulatedNote />
      <div className={styles.container}>
        <div className={styles.top}>
          <Crumbs ward={session.ward} current={patient.name} />
          {!phone && pager}
        </div>
        <div className={styles.head}>
          <div className={styles.identity}>
            {/* On a phone the app bar shows the name: the heading stays for a screen reader. */}
            <h1 className={phone ? "hw-visually-hidden" : styles.name}>{patient.name}</h1>
            <p className={styles.meta}>
              {patient.age ?? "--"} {patient.sex ?? "--"} &middot; Bed {patient.bed}
              {patient.admitting_context && ` · ${patient.admitting_context}`}
            </p>
          </div>
          <div className={styles.actions}>
            {alertRow && (
              <button type="button" className="hw-button hw-button--accent" disabled={offline} onClick={() => feed?.acknowledge(alertRow)}>
                <Icon name="bell" />
                Acknowledge alert
              </button>
            )}
            {!phone && <a href="#notes" className="hw-button">Notes{counted}</a>}
            {/* The assistant's panel (the chat bubble) opens on this patient's scope. Not for an admin, who cannot ask. */}
            {hasPatientQuestions && !readOnly && !phone && (
              <button type="button" className="hw-button" onClick={(e) => openAssistant(e.currentTarget)}>
                <Icon name="comments" />
                Ask the assistant
              </button>
            )}
            {!phone && record}
          </div>
        </div>

        <div className={styles.notices}>
          {readOnly && (
            <p className="hw-alert"><span className="hw-alert__body">Read-only. Views are recorded in the audit log.</span></p>
          )}
          {unranked !== null && (
            <>
              <p className="hw-alert"><span className="hw-alert__body">No longer ranked. The figures below are from before this entry.</span></p>
              <p role="status" className="hw-alert">
                <span className="hw-alert__body">{unranked} <Link to="/ward">Back to ward</Link></span>
              </p>
            </>
          )}
          {refreshFailed && (
            <p role="alert" className="hw-alert hw-alert--danger">
              <span className="hw-alert__body">Recorded. The page could not refresh; reload to see the new score.</span>
            </p>
          )}
          {staleSince !== null && unranked === null && (
            <p className="hw-alert hw-alert--warning" role="status">
              <Icon name="triangle-exclamation" alert />
              <span className="hw-alert__body">
                No longer on your ranked list. Figures from {formatClock(lastGood ?? since(staleSince))}.{" "}
                <Link to="/ward">Back to ward</Link>
              </span>
            </p>
          )}
        </div>

        {decision}

        {phone ? (
          <>
            <div className={`hw-tabs ${styles.tabs}`}>
              <div className="hw-tabs__list" role="tablist" aria-label="Patient sections" onKeyDown={onTabKey}>
                {TABS.map(([t, label]) => (
                  <button
                    key={t}
                    id={`${ids}-${t}`}
                    type="button"
                    role="tab"
                    className="hw-tabs__tab"
                    aria-selected={tab === t}
                    aria-controls={`${ids}-panel`}
                    tabIndex={tab === t ? 0 : -1}
                    onClick={() => setTab(t)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div id={`${ids}-panel`} role="tabpanel" className={`hw-tabs__panel ${styles.column}`} aria-labelledby={`${ids}-${tab}`}>
                {tab === "now" && <>{vitals}{ownBaseline}</>}
                {tab === "trends" && <>{trends}{stay}</>}
                {tab === "labs" && labs}
                {tab === "notes" && <section className="hw-card hw-card--outlined" aria-label="Notes">{notesList}</section>}
              </div>
            </div>
            <p className={styles.footer}>
              Advisory only. BioSentry does not replace a monitor alarm. Views are logged for audit under {session.clinician_id}.
            </p>
            <div className={`hw-actionbar hw-only-mobile ${styles.actionbar}`}>
              {record}
              <button type="button" className="hw-button" onClick={() => setTab("notes")}>Notes</button>
              {order && (
                <button type="button" className="hw-button" disabled={!nextId} onClick={() => go(nextId)}>
                  Next
                  <Icon name="arrow-right" />
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <div className={styles.layout}>
              <div className={styles.column}>
                {trends}
                {stay}
              </div>
              <div className={styles.column}>
                {vitals}
                {ownBaseline}
                {labs}
                {latestNote}
              </div>
            </div>
            <p className={styles.footer}>
              Advisory only. BioSentry does not replace a monitor alarm. Views are logged for audit under {session.clinician_id}.
            </p>
          </>
        )}

        <PatientHistoryArea key={`${patient.id}-${session.clinician_id}`} patientId={patient.id} onSignOut={onSignOut} />

        {/* The full list of notes and the note box: a drawer from the right (full screen on a phone) */}
        {notesOpen && (
          <dialog
            ref={dialog}
            className="hw-dialog hw-dialog--drawer"
            aria-labelledby={drawerTitle}
            onClose={() => setNotesOpen(false)}
            onCancel={(e) => {
              e.preventDefault(); // Esc: animate out, then close
              void closeDialog(e.currentTarget);
            }}
            onClick={(e) => {
              if (e.target === e.currentTarget) void closeDialog(e.currentTarget); // the backdrop
            }}
          >
            <div className="hw-dialog__header">
              <h2 id={drawerTitle} className="hw-dialog__title">Clinical notes{notes.length > 0 ? ` · ${notes.length}` : ""}</h2>
              <button type="button" className="hw-close" aria-label="Close notes" onClick={(e) => void closeDialog(e.currentTarget.closest("dialog"))}>
                <Icon name="xmark" />
              </button>
            </div>
            <div className={`hw-dialog__body ${styles.drawerBody}`}>
              {offlineLine}
              {notesList}
            </div>
          </dialog>
        )}

        {recording && canRecord && (
          <VitalsEntry
            patient={patient}
            clinicianId={session.clinician_id}
            role={role}
            offline={offline}
            onRecorded={handleRecorded}
            onClose={() => setRecording(false)}
          />
        )}
      </div>
    </AppShell>
  );
}
