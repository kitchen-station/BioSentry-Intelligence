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
interface Stay { unavailable: string | null; from: number | null; to: number | null; runs: Run[]; events: Ev[]; cut: boolean; broken: boolean; dropped: boolean }

const STATES: readonly string[] = ["alert", "not_ranked", "not_rebuilt", "no_reading"];
const KINDS: readonly string[] = ["admitted", "acknowledged", "vitals_recorded", "note_added", "bp_charted"];
// The server's own label is not shown: this sentence must not depend on the answer.
const FIXED = "Rebuilt from stored 15-minute sets. It can differ from the score shown at the time.";
const WINDOW_MS = 30 * 60_000; // an acknowledgement just after a run ended still belongs to it
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
  let dropped = false;
  for (const r of Array.isArray(h.runs) ? h.runs : []) {
    const o = rec(r);
    const start = time(o.start);
    const end = time(o.end);
    const state = str(o.state);
    if (start === null || end === null || end < start || state === null || !STATES.includes(state)) {
      dropped = true;
      continue;
    }
    // An alert whose score or level is unusable is kept: it is shown without them.
    const level = str(o.peak_level);
    const ok = typeof o.peak_score === "number" && level !== null && level in LEVELS;
    const reasons = Array.isArray(o.reasons) ? o.reasons.filter((x): x is string => typeof x === "string") : [];
    runs.push({ state: state as RunState, start, end, ongoing: o.ongoing === true, score: ok ? (o.peak_score as number) : null, level: ok ? (level as Level) : null, reasons });
  }
  const events: Ev[] = [];
  for (const e of Array.isArray(h.events) ? h.events : []) {
    const o = rec(e);
    const at = time(o.at);
    const kind = str(o.kind);
    if (at === null || kind === null || !KINDS.includes(kind)) {
      dropped = true;
      continue;
    }
    const role = str(o.role);
    events.push({ kind: kind as Kind, at, by: str(o.by), role: role !== null && role in ROLES ? role : null, score: typeof o.score === "number" ? o.score : null });
  }
  const all = [...runs.flatMap((r) => [r.start, r.end]), ...events.map((e) => e.at)];
  const gf = time(h.grid_from);
  const gt = time(h.grid_to);
  return {
    unavailable: str(h.unavailable),
    from: all.length || gf !== null ? Math.min(...(gf !== null ? [gf] : []), ...all) : null,
    to: all.length || gt !== null ? Math.max(...(gt !== null ? [gt] : []), ...all) : null,
    runs,
    events,
    cut: h.runs_cut === true || h.events_cut === true,
    broken: !Array.isArray(h.runs) || !Array.isArray(h.events) || (dropped && runs.length === 0 && events.length === 0),
    dropped,
  };
}

const day = (ms: number) => new Date(ms).toDateString();

function runLine(r: Run): string {
  const range =
    day(r.start) === day(r.end) ? `${formatClockDay(r.start)} to ${formatClock(r.end)}` : `${formatClockDay(r.start, 0)} to ${formatClockDay(r.end, 0)}`;
  const head = r.ongoing ? `${range} (last stored set)` : range;
  const why = r.reasons.join(" \u00b7 ");
  if (r.state === "alert") {
    const level = r.level === null ? " (details not available)" : `, up to ${r.score} (${LEVELS[r.level]})`;
    return [head, `NEWS2 at an alert level${level}`, ...(why ? [why] : [])].join(" \u00b7 ");
  }
  if (r.state === "not_ranked") return [head, "Not ranked", ...(why ? [why] : [])].join(" \u00b7 ");
  if (r.state === "not_rebuilt") return `${head} \u00b7 Not rebuilt \u00b7 the score of that time cannot be rebuilt from the stored values`;
  return `${head} \u00b7 No heart rate reading`;
}

function eventLine(e: Ev): string {
  const t = formatClockDay(e.at);
  const by = e.by === null ? "" : ` by ${e.by}`;
  if (e.kind === "admitted") return `Admitted at ${t}`;
  if (e.kind === "acknowledged") return `Acknowledged${by} at ${t}${e.score === null ? "" : ` (NEWS2 ${e.score})`}`;
  if (e.kind === "vitals_recorded") return `Vitals recorded${by} at ${t}`;
  if (e.kind === "note_added") return `Note added at ${t}${e.role === null ? "" : ` (${ROLES[e.role]})`}`;
  return `Blood pressure charted at ${t}`;
}

function Strip({ stay, from, to }: { stay: Stay; from: number; to: number }) {
  const id = useId();
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
        // The bar height also tells the level: high is tallest. The states that are not an alert stay low.
        const h = r.level === "high" ? 20 : r.level === "medium" ? 14 : 9;
        const low = { y: 14, h: 6 };
        const bar =
          r.state === "alert"
            ? { kind: r.level === "high" ? "H" : r.level === "medium" ? "M" : r.level === "single_3" ? "3" : "alert", cls: r.level === "high" ? styles.high : r.level === "medium" ? styles.medium : styles.single, y: 20 - h, h }
            : r.state === "not_ranked"
              ? { kind: "NR", cls: "", ...low }
              : r.state === "not_rebuilt"
                ? { kind: "?", cls: "", ...low }
                : { kind: "dashed", cls: styles.dashed, ...low };
        const fill = r.state === "not_ranked" ? `url(#${id}-nr)` : r.state === "not_rebuilt" ? `url(#${id}-nb)` : undefined;
        return (
          <g key={i}>
            <rect data-kind={bar.kind} className={bar.cls} style={fill ? { fill } : undefined} x={`${x}%`} y={bar.y} width={`${w}%`} height={bar.h} />
            {w >= LETTER_W && bar.kind !== "dashed" && bar.kind !== "alert" && (
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
      const inside = stay.events.filter((e) => !used.has(e) && e.at >= r.start && (r.ongoing || e.at <= r.end + WINDOW_MS));
      inside.forEach((e) => used.add(e));
      return { at: r.start, run: r, inside };
    }),
  ];
  const loose = stay.events.filter((e) => !used.has(e)).map((e) => ({ at: e.at, event: e }));
  const rows = [...items, ...loose].sort((a, b) => b.at - a.at);
  if (rows.length === 0) return null;
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
    setError(null);
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
  const end = (ms: number) => (crosses ? formatClockDay(ms, 0) : formatClockDay(ms));
  const body = stay !== null && stay.unavailable === null;

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
        <p role="status" className={error ? styles.error : styles.quiet}>{loading ? "Loading the stay\u2026" : (error ?? "")}</p>
        {stay && stay.unavailable !== null && <p className={styles.quiet}>{UNAVAILABLE[stay.unavailable] ?? "The stay could not be shown."}</p>}
        {stay && body && (
          <>
            {stay.from !== null && stay.to !== null && (
              <>
                <Strip stay={stay} from={stay.from} to={stay.to} />
                <p className={styles.ends}>
                  <span>{end(stay.from)}</span>
                  <span>{end(stay.to)}</span>
                </p>
              </>
            )}
            {stay.broken && <p className={styles.quiet}>The stay could not be shown.</p>}
            {!stay.broken && !stay.dropped && stay.runs.length === 0 && <p className={styles.quiet}>No alert level in the stored sets.</p>}
            <Entries stay={stay} />
            {stay.dropped && <p className={styles.quiet}>Some entries could not be shown.</p>}
            {stay.cut && <p className={styles.quiet}>Older entries are not shown.</p>}
          </>
        )}
        {stay && !loading && <p className={`${styles.quiet} ${styles.foot}`}>{FIXED} Synthetic, not validated.</p>}
        {asked && (
          <p className={styles.quiet}>
            <button type="button" className={styles.button} aria-disabled={loading} onClick={read}>Refresh</button>
            {readAt && ` Read at ${readAt}.`}
          </p>
        )}
      </div>
    </details>
  );
}
