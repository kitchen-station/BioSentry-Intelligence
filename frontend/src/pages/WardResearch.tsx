import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ResearchAbstained, ResearchRow, Session, WardResearch as Answer } from "../types";
import { ApiError, getWardResearch, hasLabQuestions } from "../api";
import { REASONS, shown } from "../components/BaselinePanel";
import { formatAge, formatClock } from "../components/format";
import { SimulatedNote } from "../components/LabFrame";
import { ENTRIES, modelWords } from "../components/ResearchModels";
import { SessionMenu } from "../components/SessionMenu";
import overview from "./Overview.module.css";
import research from "./Research.module.css";
import styles from "./Ward.module.css";

const VITALS = [
  ["heart_rate", "Heart rate"],
  ["respiratory_rate", "Breathing rate"],
  ["spo2", "SpO2"],
] as const;
const LEVEL: Record<ResearchRow["level"], string> = {
  low: "Low",
  low_medium: "Low-medium (one parameter at 3)",
  medium: "Medium",
  high: "High",
};

// The server sends every number; these only put it in words. A patient who is not the reader's has the flag only.
function vitalCell(row: ResearchRow, vital: string): string {
  const v = row.baseline.find((b) => b.vital === vital);
  if (v?.status === "computed") {
    const sign = v.delta > 0 ? "+" : "";
    const text = `${shown(vital, v.latest)} vs ${shown(vital, v.baseline_median)} ${v.unit} (${sign}${shown(vital, v.delta)})`;
    const seen = `${text} · ${formatAge(v.latest_age_s)}`; // the age is the server's, never the browser's clock
    return v.significant ? `${seen} · Flagged` : seen;
  }
  if (v?.status === "flag_only") return v.significant ? "Flagged" : "Not flagged";
  if (v?.status === "forming") return `forming (${v.bins_valid} of ${v.bins_needed} 15-minute sets)`;
  if (v?.status === "unavailable" && Object.hasOwn(REASONS, v.reason)) {
    return REASONS[v.reason].replace(/^./, (c) => c.toUpperCase());
  }
  return "No own baseline for this patient now.";
}

function alertCell(row: ResearchRow): string {
  const a = row.alert;
  if (a.status === "unavailable") return "The stay history is not available for this patient now.";
  if (a.bins === 0) return "none";
  const h = Math.floor(a.minutes / 60);
  const m = a.minutes % 60;
  return `${h > 0 ? `${h} h` : ""}${h > 0 && m > 0 ? " " : ""}${m > 0 ? `${m} min` : ""}${a.ongoing ? ", now" : ""}`;
}

function Who({ row }: { row: ResearchRow | ResearchAbstained }) {
  if (row.assigned && row.id !== null && row.name !== null) {
    return <Link to={`/patient/${encodeURIComponent(row.id)}`}>{row.name}</Link>;
  }
  return <>Bed {row.bed} (not your patient)</>;
}

/**
 * The whole ward as one table for a physician or the charge nurse. It reads on open and on Refresh, never on a timer:
 * every read is one audit row. The server decides who is the reader's patient; the page only shows what it sent.
 */
export function WardResearch({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [data, setData] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reading = useRef(false);

  const load = useCallback(() => {
    if (reading.current) return;
    reading.current = true;
    getWardResearch()
      .then((answer) => {
        setData(answer);
        setError(null);
      })
      .catch((failure: unknown) => {
        const status = failure instanceof ApiError ? failure.status : 0;
        setData(null); // nothing stale stays on screen after a failed read
        if (status === 401) onSignOut();
        setError(
          status === 401 ? "Your session has ended. Sign in again."
            : status === 403 ? "Not available for this account."
            : status === 404 ? "The ward was not found."
            : "The table could not be read. Try Refresh.",
        );
      })
      .finally(() => {
        reading.current = false;
      });
  }, [onSignOut]);

  useEffect(load, [load]);

  return (
    <div className={styles.page}>
      <div className={styles.statusBar}>
        <div className={styles.statusInner}>
          <div className={styles.statusLeft}>
            <span className={styles.statusItem}>
              <span className={styles.statusLabel}>BioSentry</span>
              <span className={styles.statusValue}>Ward research &middot; Ward {session.ward}</span>
            </span>
          </div>
          <div className={styles.statusRight}>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>
      <SimulatedNote />
      <main className={research.main}>
        <h1 className={research.title}>Ward research</h1>
        {data && <p>{data.label}</p>}
        {error && <p role="alert" className={overview.warn}>{error}</p>}
        <p>
          {data && <>Read at {formatClock(Date.parse(data.now))} </>}
          <button type="button" className={overview.refresh} onClick={load}>Refresh</button>
        </p>
        {data && data.ranked.length === 0 && data.abstained.length === 0 && <p>No patients on this ward.</p>}
        {data && data.ranked.length > 0 && (
          <div className={research.scroll} tabIndex={0} role="region" aria-label="Ward research table">
            <table className={`${research.table} ${overview.text}`}>
              <caption>Every patient of the ward, in the NEWS2 order</caption>
              <thead>
                <tr>
                  <th scope="col">Rank</th>
                  <th scope="col">Patient</th>
                  <th scope="col">NEWS2</th>
                  {VITALS.map(([key, label]) => <th key={key} scope="col">{label}, latest against own baseline</th>)}
                  <th scope="col">Models</th>
                  <th scope="col">At alert level</th>
                </tr>
              </thead>
              <tbody>
                {data.ranked.map((r) => (
                  <tr key={`${r.rank}-${r.bed}`}>
                    <td>{r.rank}</td>
                    <th scope="row"><Who row={r} /></th>
                    <td>{r.score}, {LEVEL[r.level]}</td>
                    {VITALS.map(([key]) => <td key={key}>{vitalCell(r, key)}</td>)}
                    <td>
                      {ENTRIES.map(([key, name]) => (
                        <div key={key}>{name}: {modelWords(r.models[key])}</div>
                      ))}
                    </td>
                    <td>{alertCell(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.abstained.length > 0 && (
          <section aria-labelledby="not-ranked">
            <h2 id="not-ranked" className={research.heading}>Not ranked</h2>
            <ul>
              {data.abstained.map((a) => (
                <li key={a.bed}>
                  <Who row={a} />: {a.reason}. {a.instruction}
                </li>
              ))}
            </ul>
          </section>
        )}
        <ul>
          <li><Link to="/lab">Simulated compare runs</Link></li>
          {hasLabQuestions && <li><Link to="/ask">Ask the research agent</Link></li>}
        </ul>
      </main>
    </div>
  );
}
