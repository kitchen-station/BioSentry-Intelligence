import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { PatientHistory } from "../types";
import { ApiError, getPatientHistory } from "../api";
import { formatClock, formatClockDay } from "./format";
import styles from "./StayPanel.module.css";

const MIN_W = 0.8; // percent of the strip: a run of no length still shows
const LETTER_W = 6; // percent of the strip: a narrower bar has no room for its letter

type Level = "single_3" | "medium" | "high";
type RunState = "alert" | "not_ranked" | "not_rebuilt" | "no_reading";
type Kind = "admitted" | "acknowledged" | "vitals_recorded" | "note_added" | "bp_charted";
interface Run { state: RunState; start: number; end: number; ongoing: boolean; score: number | null; level: Level | null; reasons: string[] }
interface Ev { kind: Kind; at: number; by: string | null; role: string | null; score: number | null }
interface Stay { unavailable: string | null; label: string | null; from: number | null; to: number | null; runs: Run[]; events: Ev[]; cut: boolean; usable: boolean }

const STATES: readonly string[] = ["alert", "not_ranked", "not_rebuilt", "no_reading"];
const KINDS: readonly string[] = ["admitted", "acknowledged", "vitals_recorded", "note_added", "bp_charted"];
const LEVELS: Record<string, string> = { high: "high", medium: "medium", single_3: "one value at 3" };
const ROLES: Record<string, string> = { rn: "RN", md: "MD", charge_rn: "Charge RN" };
const UNAVAILABLE: Record<string, string> = {
  no_bins: "No 15-minute set is stored for this stay yet.",
  past_cap: "This stay is longer than 7 days. Its history cannot be shown yet.",
  not_aligned: "The stored sets of this stay do not line up. Its history cannot be shown.",
};

const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {});
const time = (v: unknown): number | null => (typeof v === "string" && Number.isFinite(Date.parse(v)) ? Date.parse(v) : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** Percent of the strip for a time span. The strip is a picture: this is the only arithmetic on the times. */
export function place(start: number, end: number, from: number, to: number): { x: number; w: number } {
  const span = Math.max(to - from, 1);
  const w = Math.min(Math.max(((end - start) / span) * 100, MIN_W), 100);
  const x = Math.min(Math.max(((start - from) / span) * 100, 0), 100 - w);
  return { x, w };
}

// An answer of an odd shape loses its bad items and keeps the rest.
function clean(raw: PatientHistory): Stay {
  const h = rec(raw);
  const runs: Run[] = [];
  for (const r of Array.isArray(h.runs) ? h.runs : []) {
    const o = rec(r);
    const start = time(o.start);
    const end = time(o.end);
    const state = str(o.state);
    if (start === null || end === null || end < start || state === null || !STATES.includes(state)) continue;
    const score = typeof o.peak_score === "number" ? o.peak_score : null;
    const level = str(o.peak_level);
    if (state === "alert" && (score === null || level === null || !(level in LEVELS))) continue;
    const reasons = Array.isArray(o.reasons) ? o.reasons.filter((x): x is string => typeof x === "string") : [];
    runs.push({ state: state as RunState, start, end, ongoing: o.ongoing === true, score, level: level as Level | null, reasons });
  }
  const events: Ev[] = [];
  for (const e of Array.isArray(h.events) ? h.events : []) {
    const o = rec(e);
    const at = time(o.at);
    const kind = str(o.kind);
    if (at === null || kind === null || !KINDS.includes(kind)) continue;
    const role = str(o.role);
    events.push({ kind: kind as Kind, at, by: str(o.by), role: role !== null && role in ROLES ? role : null, score: typeof o.score === "number" ? o.score : null });
  }
  const all = [...runs.flatMap((r) => [r.start, r.end]), ...events.map((e) => e.at)];
  const gf = time(h.grid_from);
  const gt = time(h.grid_to);
  const u = str(h.unavailable);
  return {
    unavailable: u,
    label: str(h.label),
    from: all.length || gf !== null ? Math.min(...(gf !== null ? [gf] : []), ...all) : null,
    to: all.length || gt !== null ? Math.max(...(gt !== null ? [gt] : []), ...all) : null,
    runs,
    events,
    cut: h.runs_cut === true || h.events_cut === true,
    usable: u !== null || runs.length > 0 || events.length > 0 || (Array.isArray(h.runs) && Array.isArray(h.events) && h.runs.length === 0 && h.events.length === 0),
  };
}

const day = (ms: number) => new Date(ms).toDateString();

function runLine(r: Run): string {
  const to = r.ongoing ? "now" : formatClockDay(r.end);
  const head = `${formatClockDay(r.start)} to ${to}`;
  const why = r.reasons.join(" · ");
  if (r.state === "alert") return [head, "Alert", `NEWS2 up to ${r.score} (${LEVELS[r.level!]})`, ...(why ? [why] : [])].join(" · ");
  if (r.state === "not_ranked") return [head, "Not ranked", ...(why ? [why] : [])].join(" · ");
  if (r.state === "not_rebuilt") return `${head} · Not rebuilt · the stored values do not give the score of that time`;
  return `${head} · No heart rate reading`;
}

function eventLine(e: Ev): string {
  const t = formatClockDay(e.at);
  const by = e.by === null ? "" : ` by ${e.by}`;
  if (e.kind === "admitted") return `Admitted ${t}`;
  if (e.kind === "acknowledged") return `Acknowledged${by} at ${t}${e.score === null ? "" : ` (NEWS2 ${e.score})`}`;
  if (e.kind === "vitals_recorded") return `Vitals recorded${by} at ${t}`;
  if (e.kind === "note_added") return `Note added at ${t}${e.role === null ? "" : ` (${ROLES[e.role]})`}`;
  return `Blood pressure charted at ${t}`;
}

function Strip({ stay, from, to }: { stay: Stay; from: number; to: number }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg className={styles.strip} aria-hidden="true" focusable="false">
      <defs>
        <pattern id={`${id}-nr`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" className={styles.hatch} />
        </pattern>
        <pattern id={`${id}-nb`} width="6" height="6" patternUnits="userSpaceOnUse">
          <path d="M0 0H6M0 0V6" className={styles.hatchBox} />
        </pattern>
      </defs>
      {stay.runs.map((r, i) => {
        const { x, w } = place(r.start, r.end, from, to);
        // The bar height also tells the level: high is tallest, one value at 3 is shortest.
        const h = r.level === "high" ? 20 : r.level === "medium" ? 14 : 9;
        const bar =
          r.state === "alert"
            ? { kind: r.level === "high" ? "H" : r.level === "medium" ? "M" : "3", cls: r.level === "high" ? styles.high : r.level === "medium" ? styles.medium : styles.single, y: 20 - h, h }
            : r.state === "not_ranked"
              ? { kind: "NR", cls: "", y: 0, h: 20 }
              : r.state === "not_rebuilt"
                ? { kind: "?", cls: "", y: 0, h: 20 }
                : { kind: "dashed", cls: styles.dashed, y: 0, h: 20 };
        const fill = r.state === "not_ranked" ? `url(#${id}-nr)` : r.state === "not_rebuilt" ? `url(#${id}-nb)` : undefined;
        return (
          <g key={i}>
            <rect data-kind={bar.kind} className={bar.cls} style={fill ? { fill } : undefined} x={`${x}%`} y={bar.y} width={`${w}%`} height={bar.h} />
            {w >= LETTER_W && bar.kind !== "dashed" && (
              <text className={`${styles.letter} ${bar.kind === "H" ? styles.letterHigh : ""}`} x={`${x + w / 2}%`} y={bar.y + bar.h / 2}>{bar.kind}</text>
            )}
          </g>
        );
      })}
      {stay.events.map((e, i) => {
        const { x, w } = place(e.at, e.at, from, to);
        return <rect key={i} data-tick={e.kind} className={styles.tick} x={`${x}%`} y={22} width={`${w}%`} height={6} />;
      })}
    </svg>
  );
}

function Entries({ stay }: { stay: Stay }) {
  const used = new Set<Ev>();
  const items = [
    ...stay.runs.map((r) => {
      const inside = stay.events.filter((e) => !used.has(e) && e.at >= r.start && e.at <= r.end);
      inside.forEach((e) => used.add(e));
      return { at: r.start, run: r, inside };
    }),
  ];
  const loose = stay.events.filter((e) => !used.has(e)).map((e) => ({ at: e.at, event: e }));
  const rows = [...items, ...loose].sort((a, b) => b.at - a.at);
  return (
    <ul className={styles.list}>
      {rows.map((row, i) => (
        <li key={i} className={styles.item}>
          {"run" in row ? (
            <>
              {runLine(row.run)}
              {row.inside.length > 0 && (
                <ul className={styles.nested}>
                  {[...row.inside].sort((a, b) => b.at - a.at).map((e, j) => <li key={j}>{eventLine(e)}</li>)}
                </ul>
              )}
            </>
          ) : (
            eventLine(row.event)
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The stay of one patient as a short history. It reads once when first opened and again only on Refresh:
 * each read is one audit row on the server, so no timer asks again. Closing and opening reads nothing.
 */
export function StayPanel({ patientId, onSignOut }: { patientId: string; onSignOut: () => void }) {
  const [stay, setStay] = useState<Stay | null>(null);
  const [asked, setAsked] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readAt, setReadAt] = useState<string | null>(null);
  const reading = useRef(false);
  const signOut = useRef(onSignOut);
  useEffect(() => {
    signOut.current = onSignOut;
  }, [onSignOut]);

  const read = useCallback(() => {
    if (reading.current) return;
    reading.current = true;
    setLoading(true);
    getPatientHistory(patientId)
      .then((raw) => {
        setStay(clean(raw));
        setReadAt(formatClock(Date.now()));
        setError(null);
      })
      .catch((failure: unknown) => {
        const status = failure instanceof ApiError ? failure.status : 0;
        if (status === 401) signOut.current();
        setError(
          { 401: "Your session has ended. Sign in again.", 404: "This patient is no longer on your ranked list." }[status] ??
            "Cannot reach the ward server.",
        );
      })
      .finally(() => {
        reading.current = false;
        setLoading(false);
      });
  }, [patientId]);

  const crosses = stay !== null && stay.from !== null && stay.to !== null && day(stay.from) !== day(stay.to);
  const ongoing = stay?.runs.some((r) => r.ongoing) ?? false;
  const end = (ms: number) => (crosses ? formatClockDay(ms, 0) : formatClockDay(ms));

  return (
    <details
      className={styles.panel}
      onToggle={(e) => {
        if (e.currentTarget.open && !asked) {
          setAsked(true);
          read();
        }
      }}
    >
      <summary className={styles.summary}>Stay</summary>
      <div className={styles.body}>
        {loading && <p role="status" className={styles.quiet}>Loading the stay&hellip;</p>}
        {error && <p role="status" className={styles.error}>{error}</p>}
        {stay && !stay.usable && <p className={styles.quiet}>The stay could not be shown.</p>}
        {stay?.usable && stay.unavailable !== null && (
          <p className={styles.quiet}>{UNAVAILABLE[stay.unavailable] ?? "The stay could not be shown."}</p>
        )}
        {stay?.usable && stay.unavailable === null && (
          <>
            {stay.from !== null && stay.to !== null && (
              <>
                <Strip stay={stay} from={stay.from} to={stay.to} />
                <p className={styles.ends}>
                  <span>{end(stay.from)}</span>
                  <span>{ongoing ? "now" : end(stay.to)}</span>
                </p>
              </>
            )}
            {stay.runs.length === 0 && <p className={styles.quiet}>No alert in this stay.</p>}
            <Entries stay={stay} />
            {stay.cut && <p className={styles.quiet}>Older entries are not shown.</p>}
          </>
        )}
        {asked && (
          <p className={`${styles.quiet} ${styles.foot}`}>
            {stay?.label ? `${stay.label} ` : ""}Synthetic, not validated.
          </p>
        )}
        {asked && (
          <p className={styles.quiet}>
            <button type="button" className={styles.button} disabled={loading} onClick={read}>Refresh</button>
            {readAt && ` Read at ${readAt}.`}
          </p>
        )}
      </div>
    </details>
  );
}
