import type { ReactNode, Ref } from "react";
import { Link } from "react-router-dom";
import type { Measurement, ScoreRow, SessionRole } from "../types";
import { formatAge, formatBloodPressure, formatClock, nurseEntryText, patchInUse, STALE_AFTER_S } from "./format";
import { Flip } from "./Flip";
import { MeasurementDisplay } from "./MeasurementDisplay";
import { news2Points } from "./news2";
import { band, driftResponse, obsDue, response, type Band } from "./risk";
import { Score } from "./Score";
import { Sparkline } from "./Sparkline";
import styles from "./WardRow.module.css";

interface Props {
  row: ScoreRow;
  role: SessionRole;
  /** Ward time, epoch ms. */
  now: number;
  /** Seconds since the ward was fetched, added to the server's data age. */
  ageOffsetS: number;
  compact?: boolean;
  /** An unacknowledged alert is a state of the row: the emergency edge, the tint, and Acknowledge in place of Open. */
  alert?: boolean;
  /** Offline: the last data stays, its age turns red, Acknowledge waits. */
  offline?: boolean;
  onAcknowledge?: () => void;
  ref?: Ref<HTMLLIElement>;
}

/** The role's line under the name: the NEWS2 response, or on a flagged low row the drift line. Null when there is none. */
export function responseLine(row: ScoreRow, b: Band, role: SessionRole): string | null {
  if (b !== "low") return response(b, role);
  return row.escalation_reasons.some((r) => r.rule === "tier_b_finding") ? driftResponse(role, row.score) : null;
}

/**
 * How old the patch data is ("Stale · " once stale; `red` too while offline), or that there is no patch
 * signal; null when neither applies.
 */
export function dataAge(row: ScoreRow, ageOffsetS: number, offline = false): { text: string; red: boolean } | null {
  // Either figure can be null on a row ranked from nurse entries: never compute with a null.
  const age = row.last_update_s === null ? null : row.last_update_s + ageOffsetS;
  const patch = patchInUse(row.vitals);
  if (patch && age !== null) {
    const stale = age > STALE_AFTER_S;
    return { text: `${stale ? "Stale · " : ""}${formatAge(Math.floor(age))}`, red: stale || offline };
  }
  return !patch || row.signal_quality === null ? { text: "No patch signal", red: false } : null;
}

/** The data age alone, for the Ward's table. */
export function DataAge({ row, ageOffsetS, offline }: { row: ScoreRow; ageOffsetS: number; offline: boolean }) {
  const a = dataAge(row, ageOffsetS, offline);
  return a && <span className={styles.age} data-stale={a.red || undefined}>{a.text}</span>;
}

/** When the next set of observations is due, as the row says it. */
export function obsText(due: number | "continuous", now: number, noPatch: boolean): string {
  if (due === "continuous") return noPatch ? "Continuous monitoring needed: no patch" : "Obs continuous";
  if (due > now) return `Obs due ${formatClock(due)}`;
  return due === -Infinity ? "Obs due now" : `Obs overdue ${formatAge(Math.floor((now - due) / 1000)).replace(" ago", "")}`;
}

/** One of the five vitals (kit hw-ranklist__vital): named for a screen reader, which hears the value and its points. */
function Vital({ name, label, unit, said, points, trend, children }: {
  name: string; label: string; unit: string; said: string | null; points: number | undefined;
  trend?: readonly (number | null)[]; children: ReactNode;
}) {
  const p = points ?? 0;
  const adds = p > 0 ? `, adds ${p} NEWS2 point${p === 1 ? "" : "s"}` : "";
  return (
    <div className="hw-ranklist__vital" role="img" aria-label={said === null ? `${name} not recorded` : `${name} ${said}${unit}${adds}`} data-points={p}>
      {trend && (
        <span className={`hw-ranklist__spark ${styles.spark}`} aria-hidden="true">
          <Sparkline data={trend} width={64} height={24} color="var(--trace-hr)" />
        </span>
      )}
      <span className="hw-ranklist__label">{label}</span>
      {children}
    </div>
  );
}

const value = <T,>(m: Measurement<T> | null | undefined, write: (v: T) => string): string | null =>
  m?.data.present ? write(m.data.value) : null;

/**
 * One ranked patient (kit hw-ranklist__row). Every element is rendered; the kit drops what compact
 * rows and phone cards leave out. The name and the chevron open the patient; nothing else on the row does.
 */
export function WardRow({ row, role, now, ageOffsetS, compact = false, alert = false, offline = false, onAcknowledge, ref }: Props) {
  const b = band(row);
  const p = news2Points(row.vitals);
  const v = row.vitals;
  const nurse = nurseEntryText(v, now);
  const patch = patchInUse(v);
  const line = responseLine(row, b, role);
  const age = dataAge(row, ageOffsetS, offline);
  const due = obsDue(row);
  const to = `/patient/${row.id}`;

  return (
    <li ref={ref} className="hw-ranklist__row" data-alert={alert || undefined} data-band={b}>
      <span className="hw-ranklist__rank"><Flip>{row.rank}</Flip></span>
      <Score score={row.score} band={b} rising={row.trend === "rising"} small={compact} />

      <div className="hw-ranklist__who">
        <div className="hw-ranklist__id">
          <Link to={to} className="hw-ranklist__name">{row.name}</Link>
          <span className="hw-ranklist__meta">
            {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
          </span>
        </div>
        {line !== null
          ? <p className="hw-ranklist__response">{line}</p>
          : row.admitting_context && <p className="hw-ranklist__context">{row.admitting_context}</p>}
        {row.escalation_reasons.length > 0 && (
          <ul className="hw-ranklist__reasons">
            {row.escalation_reasons.map((r) => <li key={r.text} className="hw-tag">{r.text}</li>)}
          </ul>
        )}
      </div>

      <div className="hw-ranklist__vitals">
        <Vital name="Heart rate" label="HR" unit=" per minute" said={value(v.heart_rate, String)} points={p.heart_rate} trend={row.vital_trends.heart_rate}>
          <span className="hw-ranklist__value"><MeasurementDisplay now={now} measurement={v.heart_rate} /></span>
        </Vital>
        <Vital name="Respiratory rate" label="RR" unit=" per minute" said={value(v.respiratory_rate, String)} points={p.respiratory_rate} trend={row.vital_trends.respiratory_rate}>
          <span className="hw-ranklist__value"><MeasurementDisplay now={now} measurement={v.respiratory_rate} /></span>
        </Vital>
        <Vital name="SpO2" label="SpO2" unit=" percent" said={value(v.spo2, String)} points={p.spo2} trend={row.vital_trends.spo2}>
          <span className="hw-ranklist__value"><MeasurementDisplay now={now} measurement={v.spo2} format={(n) => `${n}%`} /></span>
        </Vital>
        <Vital name="Blood pressure" label="BP" unit=" mmHg" said={value(v.blood_pressure, formatBloodPressure)} points={p.blood_pressure}>
          <span className="hw-ranklist__value">
            <MeasurementDisplay now={now} measurement={v.blood_pressure} format={formatBloodPressure} />
          </span>
          {v.blood_pressure.data.present && v.blood_pressure.provenance.source === "nurse" && <span className="hw-ranklist__by">by nurse</span>}
        </Vital>
        <Vital name="ACVPU" label="ACVPU" unit="" said={value(v.consciousness, String)} points={p.consciousness}>
          <span className="hw-ranklist__value"><MeasurementDisplay now={now} measurement={v.consciousness} /></span>
        </Vital>
      </div>

      <div className="hw-ranklist__data">
        <span className="hw-ranklist__age" data-stale={age?.red || undefined}>
          {age?.text}
        </span>
        {patch && row.signal_quality !== null && (
          <span className="hw-ranklist__signal" data-weak={row.signal_quality <= 90 || undefined}>Signal {row.signal_quality}%</span>
        )}
        {nurse !== null && <span className="hw-ranklist__signal">{nurse}</span>}
        <span className={`hw-ranklist__obs ${styles.obs}`} data-overdue={(due !== "continuous" && due <= now) || undefined}>
          {obsText(due, now, row.last_update_s === null || !patch)}
        </span>
      </div>

      <div className="hw-ranklist__action">
        {alert && onAcknowledge ? (
          <button
            type="button"
            className="hw-button hw-button--accent"
            disabled={offline}
            aria-label={`Acknowledge the alert for ${row.name}`}
            onClick={onAcknowledge}
          >
            <span className="hw-icon fa-solid fa-bell" aria-hidden="true" />
            Acknowledge
          </button>
        ) : (
          <Link to={to} className="hw-button hw-button--ghost hw-button--icon hw-ranklist__open" aria-label={`Open ${row.name}`}>
            <span className="hw-icon fa-solid fa-chevron-right" aria-hidden="true" />
          </Link>
        )}
      </div>
    </li>
  );
}
