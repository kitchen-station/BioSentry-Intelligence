import type { ReactNode, Ref } from "react";
import { Link } from "react-router-dom";
import type { ScoreRow, SessionRole } from "../types";
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
  /** An unacknowledged alert is a state of the row: the emergency bar, the tint, and Acknowledge in place of Open. */
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

function ObsDue({ due, now, noPatch }: { due: number | "continuous"; now: number; noPatch: boolean }) {
  if (due === "continuous") {
    return <div className={styles.obsDue}>{noPatch ? "Continuous monitoring needed: no patch" : "Obs continuous"}</div>;
  }
  if (due > now) return <div className={styles.obsDue}>Obs due {formatClock(due)}</div>;
  return (
    <div className={styles.obsDue} data-overdue>
      {due === -Infinity ? "Obs due now" : `Obs overdue ${formatAge(Math.floor((now - due) / 1000)).replace(" ago", "")}`}
    </div>
  );
}

/** How old the patch data is (red once stale, and while offline), or that there is no patch signal. */
export function DataAge({ row, ageOffsetS, offline = false }: { row: ScoreRow; ageOffsetS: number; offline?: boolean }) {
  // Either figure can be null on a row ranked from nurse entries: never compute with a null.
  const age = row.last_update_s === null ? null : row.last_update_s + ageOffsetS;
  const patch = patchInUse(row.vitals);
  if (patch && age !== null) {
    const stale = age > STALE_AFTER_S;
    return (
      <div className={styles.age} data-stale={stale || offline || undefined}>
        {stale ? "Stale · " : ""}{formatAge(Math.floor(age))}
      </div>
    );
  }
  return !patch || row.signal_quality === null ? <div className={styles.age}>No patch signal</div> : null;
}

/** One vital: its name for a screen reader (the column heads are hidden), a sparkline when comfortable, the value red when it adds points. */
function Vital({ label, points, trend, compact, children }: { label: string; points: number | undefined; trend: readonly (number | null)[]; compact: boolean; children: ReactNode }) {
  return (
    <div className={styles.vital} data-points={points}>
      <span className="hw-visually-hidden">{label} </span>
      {!compact && <Sparkline data={trend} width={64} height={24} color="var(--trace-hr)" />}
      <span className={styles.value}>{children}</span>
    </div>
  );
}

/** One ranked patient in the Ward list (kit hw-ranklist__row). The name and the chevron open the patient; nothing else on the row does. */
export function WardRow({ row, role, now, ageOffsetS, compact = false, alert = false, offline = false, onAcknowledge, ref }: Props) {
  const b = band(row);
  const p = news2Points(row.vitals);
  const nurse = nurseEntryText(row.vitals, now);
  const patch = patchInUse(row.vitals);
  const line = responseLine(row, b, role);
  const to = `/patient/${row.id}`;

  return (
    <li ref={ref} className="hw-ranklist__row" data-alert={alert || undefined} data-band={b}>
      <span className={styles.rank}><Flip>{row.rank}</Flip></span>
      <Score score={row.score} band={b} rising={row.trend === "rising"} small={compact} />

      <div className={styles.main}>
        <div className={styles.nameRow}>
          <Link to={to} className={styles.name}>{row.name}</Link>
          <span className={styles.detail}>
            {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
          </span>
        </div>
        {line !== null && <div className={styles.response}>{line}</div>}
        {line === null && !compact && row.admitting_context && <div className={styles.context}>{row.admitting_context}</div>}
        {!compact && row.escalation_reasons.length > 0 && (
          <div className={styles.reasons}>
            {row.escalation_reasons.map((r) => <span key={r.text} className="hw-tag">{r.text}</span>)}
          </div>
        )}
      </div>

      <Vital label="Heart rate" points={p.heart_rate} trend={row.vital_trends.heart_rate} compact={compact}>
        <MeasurementDisplay now={now} measurement={row.vitals.heart_rate} />
      </Vital>
      <Vital label="Respiratory rate" points={p.respiratory_rate} trend={row.vital_trends.respiratory_rate} compact={compact}>
        <MeasurementDisplay now={now} measurement={row.vitals.respiratory_rate} />
      </Vital>
      <Vital label="SpO2" points={p.spo2} trend={row.vital_trends.spo2} compact={compact}>
        <MeasurementDisplay now={now} measurement={row.vitals.spo2} format={(v) => `${v}%`} />
      </Vital>
      <div className={styles.vital} data-points={p.blood_pressure}>
        <span className="hw-visually-hidden">Blood pressure </span>
        <span className={styles.value}>
          <MeasurementDisplay now={now} measurement={row.vitals.blood_pressure} format={formatBloodPressure} />
        </span>
      </div>
      <div className={styles.vital} data-points={p.consciousness}>
        <span className="hw-visually-hidden">AVPU </span>
        <span className={styles.value}><MeasurementDisplay now={now} measurement={row.vitals.consciousness} /></span>
      </div>

      <div className={styles.data}>
        <DataAge row={row} ageOffsetS={ageOffsetS} offline={offline} />
        {!compact && (
          <>
            {nurse !== null && <div>{nurse}</div>}
            {patch && row.signal_quality !== null && (
              <div data-weak={row.signal_quality <= 90 || undefined}>Signal {row.signal_quality}%</div>
            )}
            <ObsDue due={obsDue(row)} now={now} noPatch={row.last_update_s === null || !patch} />
          </>
        )}
      </div>

      <div className={styles.action}>
        {alert && onAcknowledge ? (
          <button
            type="button"
            className="hw-button hw-button--primary hw-button--sm"
            disabled={offline}
            aria-label={`Acknowledge the alert for ${row.name}`}
            onClick={onAcknowledge}
          >
            <span className="hw-icon fa-solid fa-bell" aria-hidden="true" />
            Acknowledge
          </button>
        ) : (
          <Link to={to} className="hw-button hw-button--ghost hw-button--icon hw-button--sm" aria-label={`Open ${row.name}`}>
            <span className="hw-icon fa-solid fa-chevron-right" aria-hidden="true" />
          </Link>
        )}
      </div>
    </li>
  );
}
