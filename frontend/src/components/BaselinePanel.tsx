import { useId, type ReactNode } from "react";
import type { BaselineComputed } from "../types";
import { STALE_AFTER_S, formatAge, formatClock } from "./format";
import type { BaselineState } from "./useBaseline";
import styles from "./BaselinePanel.module.css";

// Fixed here, not read from the response: a changed or empty server string cannot remove it.
const LABEL = "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger.";
const BIN_MIN = 15;
// One missed bin on top of the page's usual stale threshold; age is counted from the bin's end.
const STALE_AGE_S = BIN_MIN * 60 + STALE_AFTER_S;

const NAMES: Record<string, string> = {
  heart_rate: "Heart rate",
  respiratory_rate: "Respiratory rate",
  spo2: "SpO2",
  temperature: "Temperature",
};
// The direction the detector flags for each vital (tier_b/baseline.py `bad`): a rise for heart
// rate, respiratory rate and temperature, a fall for SpO2.
const FLAGS_RISE: Record<string, boolean> = {
  heart_rate: true,
  respiratory_rate: true,
  spo2: false,
  temperature: true,
};
export const REASONS: Record<string, string> = {
  no_history: "no history yet",
  latest_bin_missing: "latest 15-minute set missing",
  patch_not_live: "not counted: no live patch reading",
};

// The response is checked, not trusted: an odd shape becomes a plain line, never a blank page.
export const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null;
export const num = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const time = (x: unknown): x is string => typeof x === "string" && Number.isFinite(Date.parse(x));

export function isComputed(v: Record<string, unknown>): v is BaselineComputed {
  return (
    [v.latest, v.delta, v.baseline_median, v.z, v.run_bins, v.latest_age_s].every(num) &&
    (v.latest_age_s as number) >= 0 && // the server never sends a negative age
    (v.run_bins as number) >= 0 &&
    time(v.latest_at) &&
    time(v.baseline_from) &&
    (v.since === null || time(v.since)) &&
    typeof v.significant === "boolean" &&
    typeof v.unit === "string"
  );
}

// Day and month with every time: a bare clock time reads as today.
const when = (iso: string) => {
  const ms = Date.parse(iso);
  return `${new Date(ms).toLocaleDateString([], { day: "numeric", month: "short" })} ${formatClock(ms)}`;
};

// Display rounding for `latest` (a 15-minute median), the same for every vital: one decimal, half
// away from zero, taken from the number's decimal text (so 72.85 gives 72.9, which toFixed and
// Math.round(x * 10) get wrong in binary). Temperature always shows the decimal. The served value
// is the element's title.
export const shown = (vital: string, x: number) => {
  const a = String(Math.abs(x));
  const r = a.includes("e") ? Number(Math.abs(x).toFixed(1)) : Number(`${Math.round(Number(`${a}e1`))}e-1`);
  const t = vital === "temperature" ? r.toFixed(1) : String(r);
  return x < 0 && r !== 0 ? `-${t}` : t;
};

const span = (bins: number) => {
  const m = bins * BIN_MIN;
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
};

const Num = ({ children, title }: { children: ReactNode; title?: string }) => (
  <span className={styles.num} title={title}>{children}</span>
);

// What the window is anchored on, in words. An unknown anchor is not claimed to be admission.
const anchorText = (v: BaselineComputed, windowBins: number, admissionKnown: boolean) =>
  `Baseline: first ${windowBins} valid 15-minute sets, ${
    v.anchored_at === "admission"
      ? "since admission"
      : admissionKnown
        ? "from the earliest 15-minute set kept (not from admission)"
        : "from the first stored 15-minute set (admission time not known)"
  }, from ${when(v.baseline_from)}`;

function Computed({ v, k, p, anchor }: { v: BaselineComputed; k: number; p: number; anchor: string | null }) {
  const stale = v.latest_age_s > STALE_AGE_S;
  const bins = `${v.run_bins} 15-minute ${v.run_bins === 1 ? "set" : "sets"}`;
  const flagsRise = FLAGS_RISE[v.vital];
  const direction = typeof v.direction === "string" ? v.direction : null; // a wrong type is absent
  const pathway = typeof v.pathway === "string" ? v.pathway : null;
  // Not flagged is not "normal": say what is true about the latest bin. The flagged direction
  // comes from the vital, not from the sign of z (which the server rounds).
  const wentFlaggedWay = flagsRise ? v.delta > 0 : v.delta < 0;
  const why =
    v.run_bins === 0 && Math.abs(v.z) < k
      ? ""
      : wentFlaggedWay
        ? ` (only ${v.run_bins} of ${p} 15-minute sets so far)`
        : v.delta === 0
          ? ""
          : ` (moved the other way; only a sustained ${flagsRise ? "rise" : "fall"} is flagged)`;
  return (
    <>
      <span className={styles.name}>{NAMES[v.vital]}</span>{" "}
      <Num title={String(v.latest)}>{shown(v.vital, v.latest)} {v.unit}</Num>
      {" · "}
      <Num>{v.delta > 0 ? "+" : ""}{v.delta} {v.unit}</Num> from own baseline <Num>{v.baseline_median} {v.unit}</Num>
      {" · "}
      {v.significant ? (
        <>
          <span className={styles.flag}>Flagged</span>
          {direction && ` ${direction}`} for {bins} ({span(v.run_bins)})
          {v.since !== null && <>, since <Num>{when(v.since)}</Num></>}
        </>
      ) : (
        `not flagged${why}`
      )}
      <span className={styles.secondary}>{" "}z <Num>{v.z}</Num>{pathway !== null && ` · ${pathway}`}</span>
      {/* An old reading is worded and marked, not just dated: it must not pass as current. */}
      <span className={styles.sub} data-stale={stale || undefined}>
        {stale ? "Stale · " : ""}15-minute set <Num>{when(v.latest_at)}</Num> to{" "}
        <Num>{formatClock(Date.parse(v.latest_at) + BIN_MIN * 60_000)}</Num>, ended {formatAge(v.latest_age_s)}
      </span>
      {anchor !== null && <span className={styles.sub}>{anchor}</span>}
    </>
  );
}

function Item({ v, k, p, anchor }: { v: unknown; k: number; p: number; anchor: (v: BaselineComputed) => string | null }) {
  if (!isObj(v) || typeof v.vital !== "string" || !Object.hasOwn(NAMES, v.vital)) return <>Unrecognised finding</>;
  if (v.status === "computed" && isComputed(v)) return <Computed v={v} k={k} p={p} anchor={anchor(v)} />;
  const name = <span className={styles.name}>{NAMES[v.vital]}</span>;
  if (v.status === "forming" && num(v.bins_valid) && num(v.bins_needed)) {
    return <>{name} · Own baseline: getting ready (<Num>{v.bins_valid}</Num> of <Num>{v.bins_needed}</Num> 15-minute sets)</>;
  }
  if (v.status === "unavailable" && typeof v.reason === "string" && Object.hasOwn(REASONS, v.reason)) {
    return <>{name} · {REASONS[v.reason]}</>;
  }
  return <>Unrecognised finding</>;
}

export function BaselinePanel({ baseline }: { baseline: BaselineState }) {
  const heading = useId();
  if (baseline === undefined) return <p className={styles.quiet}>Own baseline: getting ready.</p>;
  if (baseline === null) return <p className={styles.quiet}>Own baseline: not shown in this version.</p>;
  const failed = <p className={styles.quiet}>Baseline could not be loaded.</p>;
  if (baseline === "failed") return failed;
  const b: unknown = baseline;
  if (
    !isObj(b) ||
    !Array.isArray(b.vitals) ||
    b.vitals.length === 0 ||
    !isObj(b.params) ||
    ![b.params.k, b.params.p, b.params.window_bins].every(num)
  ) {
    return failed;
  }
  const { k, p, window_bins } = b.params as { k: number; p: number; window_bins: number };
  const entries: unknown[] = b.vitals;
  const known = b.admission_known === true;
  // One anchor line under the list when every computed vital shares it, else one per vital.
  const lines = entries
    .filter((v): v is BaselineComputed => isObj(v) && v.status === "computed" && isComputed(v))
    .map((v) => anchorText(v, window_bins, known));
  const shared = lines.length > 0 && lines.every((l) => l === lines[0]) ? lines[0] : null;
  const missing = Object.keys(NAMES).filter((name) => !entries.some((v) => isObj(v) && v.vital === name));
  return (
    <section aria-labelledby={heading} className={styles.panel}>
      <h2 id={heading} className={styles.heading}>Change from own baseline</h2>
      <p className={styles.label}>{LABEL}</p>
      {/* role: list-style: none drops the list semantics in VoiceOver */}
      <ul role="list" className={styles.list}>
        {entries.map((v, i) => (
          <li key={i} className={styles.item} data-significant={(isObj(v) && isComputed(v) && v.significant === true) || undefined}>
            <Item v={v} k={k} p={p} anchor={(c) => (shared === null ? anchorText(c, window_bins, known) : null)} />
          </li>
        ))}
        {missing.map((name) => (
          <li key={name} className={styles.item}>{NAMES[name]} · no finding returned</li>
        ))}
      </ul>
      {shared !== null && <p className={styles.quiet}>{shared}</p>}
    </section>
  );
}
