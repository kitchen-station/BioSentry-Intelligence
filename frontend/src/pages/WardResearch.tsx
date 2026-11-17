import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ResearchAbstained, ResearchRow, Session, WardResearch as Answer } from "../types";
import { ApiError, getWardResearch, hasLabQuestions } from "../api";
import { REASONS, shown } from "../components/BaselinePanel";
import { formatAge, formatClock } from "../components/format";
import { LabFrame } from "../components/LabFrame";
import { ENTRIES, modelWords } from "../components/ResearchModels";
import research from "./Research.module.css";

// Each with the clinical y-range the Patient page's trends use, for the drawing of the value against its own band.
const VITALS = [
  ["heart_rate", "Heart rate", [40, 140]],
  ["respiratory_rate", "Breathing rate", [8, 30]],
  ["spo2", "SpO2", [85, 100]],
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

/**
 * The latest value drawn against the patient's own band on the vital's clinical range: a shaded band and a dot, filled in
 * the highlight when it sits outside the band. For the eye only: the words under it say the same numbers.
 */
function Band({ row, vital, range: [lo, hi] }: { row: ResearchRow; vital: string; range: readonly [number, number] }) {
  const v = row.baseline.find((b) => b.vital === vital);
  if (v?.status !== "computed") return null;
  const at = (x: number) => Math.max(0, Math.min(100, ((x - lo) / (hi - lo)) * 100));
  return (
    <svg className={`hw-graph__svg ${research.band}`} aria-hidden="true">
      <line className="hw-graph__gridline" x1="0" x2="100%" y1="8" y2="8" />
      <rect className="hw-graph__band" x={`${at(v.band_low)}%`} width={`${at(v.band_high) - at(v.band_low)}%`} y="2" height="12" rx="3" />
      <circle className="hw-graph__dot" data-on={v.latest < v.band_low || v.latest > v.band_high ? "" : undefined} cx={`${at(v.latest)}%`} cy="8" r="6" />
    </svg>
  );
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
    <LabFrame session={session} onSignOut={onSignOut} title={<>Ward research &middot; Ward {session.ward}</>}>
      <div className={research.main}>
        <h1 className="hw-heading hw-heading--3">Ward research</h1>
        {data && <p className="hw-text-muted">{data.label}</p>}
        {error && (
          <p role="alert" className="hw-alert hw-alert--danger">
            <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
            {error}
          </p>
        )}
        <p className={`${research.toolbar} hw-caption`}>
          {data && <>Read at {formatClock(Date.parse(data.now))} </>}
          <button type="button" className="hw-button hw-button--sm" onClick={load}>
            <span className="hw-icon fa-solid fa-rotate" aria-hidden="true" />
            Refresh
          </button>
        </p>
        {data && data.ranked.length === 0 && data.abstained.length === 0 && <p className="hw-text-muted">No patients on this ward.</p>}
        {data && data.ranked.length > 0 && (
          <div className={`hw-card hw-card--outlined hw-table-wrap ${research.tableCard}`} tabIndex={0} role="region" aria-label="Ward research table">
            <table className={`hw-table ${research.nowrap}`}>
              <caption className={`hw-caption ${research.caption}`}>Every patient of the ward, in the NEWS2 order</caption>
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
                    <td className="hw-table__num">{r.rank}</td>
                    <th scope="row"><Who row={r} /></th>
                    <td className={research.level} data-level={r.level}>{r.score}, {LEVEL[r.level]}</td>
                    {VITALS.map(([key, , range]) => <td key={key}><Band row={r} vital={key} range={range} />{vitalCell(r, key)}</td>)}
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
        {data && data.ranked.length > 0 && (
          <p className="hw-caption">Shaded: the patient&apos;s own baseline band, on the vital&apos;s clinical range. Dot: the latest value, filled when it sits outside the band.</p>
        )}
        {data && data.abstained.length > 0 && (
          <section aria-labelledby="not-ranked" className={`hw-card hw-card--outlined ${research.card}`}>
            <h2 id="not-ranked" className="hw-heading hw-heading--4">Not ranked</h2>
            <ul>
              {data.abstained.map((a) => (
                <li key={a.bed}>
                  <Who row={a} />: {a.reason}. {a.instruction}
                </li>
              ))}
            </ul>
          </section>
        )}
        <ul className={research.links}>
          <li><Link to="/lab">Simulated compare runs</Link></li>
          {hasLabQuestions && <li><Link to="/ask">Ask the research agent</Link></li>}
        </ul>
      </div>
    </LabFrame>
  );
}
