import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LabRunDetail, LabRunItem, SessionRole } from "../types";
import { ApiError, getLabRun, getLabRuns, hasLabRuns, requestLabRun } from "../api";
import research from "./Research.module.css";
import overview from "./Overview.module.css";
import styles from "./LabRuns.module.css";

// The API's lists are the authority and refuse anything else; these only fill the selects (contract-d7, section 3).
const W4_RULES = [...Array.from({ length: 20 }, (_, i) => `news2_total_ge_${i + 1}`), "news2_chart_trigger"];
const STAFFING = ["staffing", "nurses", "doctors"];
const HOSPITAL: Record<string, string[]> = {
  typical: STAFFING.map((g) => `${g}=doh-standard`),
  surge: STAFFING.map((g) => `${g}=doh-standard`),
  "doh-standard": STAFFING.map((g) => `${g}=typical`),
};
const DAYS = [1, 3, 7, 14, 30];
const SEED_MAX = 2147483647;

const when = (iso: unknown) => `${String(iso).slice(0, 16).replace("T", " ")} UTC`;
const request = (r: LabRunItem) =>
  r.mode === "w4"
    ? `W4, seed ${r.seed}, ${r.days} days: ${r.baseline} against ${r.change}`
    : `Hospital, seed ${r.seed}, ${r.days} days: ${r.baseline}, change ${r.change}`;
const state = (r: LabRunItem) =>
  ({
    queued: "Waiting for the lab worker",
    running: r.claimed_at == null ? "Running" : `Running since ${String(r.claimed_at).slice(11, 16)} UTC`,
    done: "Done",
    failed: `Failed: ${r.reason ?? "no reason given"}`,
    refused: `Refused by the simulator: ${r.reason ?? "no reason given"}`,
  })[r.state] ?? String(r.state);

// The stored report is the simulator's own output and its keys change, so every read here tolerates a missing or odd key.
const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string => (v == null ? "-" : typeof v === "object" ? JSON.stringify(v) : String(v));

function header(report: unknown): [string, string[]][] {
  const r = rec(report);
  const change = rec(r.change);
  const fields = Array.isArray(change.fields) ? change.fields.map((f) => rec(f)) : [];
  return [
    ["mode", [str(r.mode)]],
    ["seed", [str(r.seed)]],
    ["days", [str(r.days)]],
    ["baseline", [typeof r.baseline === "object" ? str(rec(r.baseline).name) : str(r.baseline)]],
    [
      "change",
      r.change === undefined
        ? ["-"]
        : [`${str(change.kind)} ${str(change.name)}`, ...fields.map((f) => `${str(f.path)}: ${str(f.baseline)} -> ${str(f.change)}`)],
    ],
    ["pairing.status", [str(rec(r.pairing).status)]],
    ["same_person_at_each_arrival", [str(rec(r.pairing).same_person_at_each_arrival)]],
  ];
}

/**
 * Simulated runs: a physician or the charge nurse asks, everyone on the ward reads. It reads on load, on Refresh and after a
 * request, never on a timer: every read writes a row to the audit log.
 */
export function LabRuns({ role, onSignOut }: { role: SessionRole; onSignOut: () => void }) {
  const clinician = role !== "admin";
  const [runs, setRuns] = useState<LabRunItem[] | null>(null);
  const [detail, setDetail] = useState<LabRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const [sending, setSending] = useState(false);
  const [mode, setMode] = useState<"w4" | "hospital">("w4");
  const [seed, setSeed] = useState("1");
  const [days, setDays] = useState(3);
  const [baseline, setBaseline] = useState("");
  const [change, setChange] = useState("");

  const baselines = mode === "w4" ? W4_RULES : Object.keys(HOSPITAL);
  const base = baselines.includes(baseline) ? baseline : baselines[0];
  const changes = mode === "w4" ? W4_RULES.filter((r) => r !== base) : HOSPITAL[base];
  const chosen = changes.includes(change) ? change : changes[0];
  const seedOk = /^\d{1,10}$/.test(seed) && Number(seed) <= SEED_MAX;

  const json = useMemo(() => JSON.stringify(detail?.report, null, 2), [detail]);
  useEffect(() => {
    if (detail) heading.current?.focus();
  }, [detail]);

  // A 401 means the session ended: show it and sign out, as the ward overview does.
  const fail = useCallback(
    (failure: unknown, fallback: string) => {
      const status = failure instanceof ApiError ? failure.status : 0;
      if (status === 401) onSignOut();
      setError(status === 403 ? "Not available for this account." : status === 401 ? "Your session has ended. Sign in again." : fallback);
    },
    [onSignOut],
  );

  const load = useCallback((fallback: string) => {
    return getLabRuns()
      .then((rows) => {
        if (!Array.isArray(rows)) throw new Error("not a list");
        setRuns(rows);
        setError(null);
      })
      .catch((failure: unknown) => {
        setRuns(null); // nothing stale stays on screen after a failed read
        fail(failure, fallback);
      });
  }, [fail]);

  useEffect(() => {
    if (hasLabRuns && clinician) void load("The runs could not be read. Try Refresh.");
  }, [load, clinician]);

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    requestLabRun({ kind: "compare", mode, seed: Number(seed), days, baseline: base, change: chosen })
      .then(() => load("The request was sent. The list could not be read. Try Refresh."))
      .catch((failure: unknown) => {
        if (failure instanceof ApiError && failure.status === 429) {
          setError("A limit is reached: one open request for each clinician, 10 in 24 hours, 5 waiting on the ward. Try again later.");
        } else fail(failure, "The request was not sent. Try again.");
      })
      .finally(() => setSending(false));
  };

  const show = (id: number) => {
    setDetail(null);
    setOpen(false);
    return getLabRun(id)
      .then((d) => {
        setDetail(d);
        setError(null);
      })
      .catch((failure: unknown) => {
        setDetail(null);
        fail(failure, "The result could not be read. Try again.");
      });
  };

  const select = (label: string, value: string | number, options: readonly (string | number)[], set: (v: string) => void, text: (o: string | number) => string = String) => (
    <label className={styles.field}>
      {label}
      <select value={value} onChange={(e) => set(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{text(o)}</option>)}
      </select>
    </label>
  );

  return (
    <section className={research.card} aria-labelledby="runs">
      <h2 id="runs" className={research.heading}>Simulated runs</h2>
      {!hasLabRuns ? (
        <p>Simulated runs need the server.</p>
      ) : !clinician ? (
        <p>Simulated runs are for the ward&apos;s clinicians.</p>
      ) : (
        <>
          <p>
            Ask the simulator to run one simulated ward twice from the same seed, with one change, and read what differs. On W4
            both arms have the same patients; in hospital mode they are partly paired, and the result says how far. It never
            touches the ward that is running now. Synthetic, not validated.
          </p>
          <p>A lab worker beside the ward runs each request. If none is running, a request fails after an hour.</p>

          {role === "rn" ? (
            <p>A physician or the charge nurse can ask for a simulated run.</p>
          ) : (
            <form onSubmit={send}>
              <div className={styles.form}>
                {select("Mode", mode, ["w4", "hospital"], (v) => setMode(v as "w4" | "hospital"), (o) =>
                  o === "w4" ? "Ward W4: two warning rules" : "Hospital: one group of settings")}
                <div className={styles.field}>
                  <label htmlFor="seed">Seed</label>
                  <input id="seed" type="number" min={0} max={SEED_MAX} step={1} value={seed} aria-describedby="seed-hint" onChange={(e) => setSeed(e.target.value)} />
                  <span id="seed-hint" className={seedOk ? undefined : overview.warn}>
                    A whole number from 0 to 2147483647. The same seed gives the same patients.
                  </span>
                </div>
                {select("Days", days, DAYS, (v) => setDays(Number(v)))}
                {select("Baseline", base, baselines, setBaseline)}
                {select("Change", chosen, changes, setChange)}
              </div>
              <button type="submit" className={overview.refresh} disabled={sending || !seedOk}>Ask for a simulated run</button>
              <p className={research.small}>One at a time for each clinician, 10 in 24 hours, 5 waiting on the ward.</p>
            </form>
          )}

          {error && <p role="alert" className={overview.warn}>{error}</p>}
          <button type="button" className={overview.refresh} onClick={() => void load("The runs could not be read. Try Refresh.")}>
            Refresh
          </button>
          {runs && runs.length === 0 && <p>No simulated runs yet.</p>}
          {runs && runs.length > 0 && (
            <div className={research.scroll} tabIndex={0} role="region" aria-label="Simulated runs">
              <table className={`${research.table} ${overview.text}`}>
                <thead>
                  <tr><th scope="col">Asked by</th><th scope="col">When</th><th scope="col">Request</th><th scope="col">State</th></tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id}>
                      <td>{r.requested_by}</td>
                      <td>{when(r.requested_at)}</td>
                      <td>{request(r)}</td>
                      <td>
                        {state(r)}
                        {r.state === "done" && <> <button type="button" className={overview.refresh} onClick={() => void show(r.id)}>Show result</button></>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detail && (
            <div>
              <h3 ref={heading} tabIndex={-1} className={research.heading}>Result of run {detail.id}</h3>
              <p role="note">Synthetic, not validated. One seed is one run: it shows how the comparison works. Use several seeds before reading anything into a difference.</p>
              <dl className={styles.dl}>
                {header(detail.report).map(([term, lines]) => (
                  <div key={term} className={styles.row}>
                    <dt>{term}</dt>
                    <dd>{lines.map((l, i) => <div key={i}>{l}</div>)}</dd>
                  </div>
                ))}
              </dl>
              {typeof detail.report_text === "string" && (
                <pre className={styles.pre} tabIndex={0} role="region" aria-label="compare.txt">{detail.report_text}</pre>
              )}
              {detail.report != null && (
                <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
                  <summary>compare.json, as stored</summary>
                  {open && <pre className={styles.pre} tabIndex={0} role="region" aria-label="compare.json">{json}</pre>}
                </details>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
