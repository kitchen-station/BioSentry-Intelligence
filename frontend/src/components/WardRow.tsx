import { Link } from "react-router-dom";
import type { ScoreRow, SessionRole } from "../types";
import { formatAge, formatBloodPressure, formatClock, nurseEntryText, patchInUse, STALE_AFTER_S } from "./format";
import { Flip } from "./Flip";
import { MeasurementDisplay } from "./MeasurementDisplay";
import { news2Points } from "./news2";
import { band, driftResponse, obsDue, response } from "./risk";
import { Sparkline } from "./Sparkline";
import styles from "./WardRow.module.css";

const TREND_CHAR: Record<string, string> = {
  rising: "\u2191",
  falling: "\u2193",
  stable: "\u2192",
  insufficient: "--",
};

interface Props {
  row: ScoreRow;
  role: SessionRole;
  /** Wall clock, epoch ms. */
  now: number;
  /** Seconds since the ward was fetched, added to the server's data age so it keeps ticking. */
  ageOffsetS: number;
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

export function WardRow({ row, role, now, ageOffsetS }: Props) {
  const b = band(row);
  const p = news2Points(row.vitals);
  // Either figure can be null on a row ranked from nurse entries: never compute with a null.
  const age = row.last_update_s === null ? null : row.last_update_s + ageOffsetS;
  const nurse = nurseEntryText(row.vitals);
  const patch = patchInUse(row.vitals);
  return (
    <Link to={`/patient/${row.id}`} className={styles.link}>
      <div className={styles.row} data-band={b}>
        {/* Rank + score */}
        <div className={styles.rankCol}>
          <span className={styles.rank}><Flip>{row.rank}</Flip></span>
          <div className={styles.scoreBlock}>
            <span className={styles.score} aria-label={`NEWS2 ${row.score}, ${b} risk`}><Flip>{row.score}</Flip></span>
            <span className={styles.trend} data-trend={row.trend}>{TREND_CHAR[row.trend]}</span>
          </div>
        </div>

        {/* Patient info */}
        <div className={styles.infoCol}>
          <div className={styles.nameRow}>
            <span className={styles.name}>{row.name}</span>
            <span className={styles.detail}>
              {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
            </span>
            {nurse !== null && <span className={styles.updateTag}>{nurse}</span>}
            {patch && row.signal_quality !== null && row.signal_quality <= 90 && (
              <span className={styles.signalLow}>Signal {row.signal_quality}%</span>
            )}
            {patch && age !== null ? (
              <span className={styles.updateTag} data-stale={age > STALE_AFTER_S || undefined}>
                {age > STALE_AFTER_S ? "Stale · " : ""}{formatAge(Math.floor(age))}
              </span>
            ) : (
              (!patch || row.signal_quality === null) && <span className={styles.updateTag}>No patch signal</span>
            )}
          </div>
          <div className={styles.context}>{row.admitting_context}</div>
          {b !== "low" && <div className={styles.response}>{response(b, role)}</div>}
          {b === "low" && row.escalation_reasons.some((r) => r.rule === "tier_b_finding") && (
            <div className={styles.response}>{driftResponse(role, row.score)}</div>
          )}
          {row.escalation_reasons.length > 0 && (
            <div className={styles.reasonLine}>
              {row.escalation_reasons.map((r) => r.text).join(" · ")}
            </div>
          )}
        </div>

        {/* Sparklines */}
        <div className={styles.sparkCol}>
          <div className={styles.sparkRow} data-points={p.heart_rate}>
            <span className={styles.sparkLabel}>HR</span>
            <Sparkline data={row.vital_trends.heart_rate} width={72} height={20} color="var(--trace-hr)" fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.heart_rate} />
            </span>
          </div>
          <div className={styles.sparkRow} data-points={p.respiratory_rate}>
            <span className={styles.sparkLabel}>RR</span>
            <Sparkline data={row.vital_trends.respiratory_rate} width={72} height={20} color="var(--trace-rr)" fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.respiratory_rate} />
            </span>
          </div>
          <div className={styles.sparkRow} data-points={p.spo2}>
            <span className={styles.sparkLabel}>SpO2</span>
            <Sparkline data={row.vital_trends.spo2} width={72} height={20} color="var(--trace-spo2)" fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.spo2} format={(v) => `${v}%`} />
            </span>
          </div>
        </div>

        {/* Nurse-entered + meta */}
        <div className={styles.metaCol}>
          <div className={styles.nursePair} data-points={p.blood_pressure}>
            <span className={styles.nurseLabel}>BP</span>
            <MeasurementDisplay
              measurement={row.vitals.blood_pressure}
              format={formatBloodPressure}
            />
          </div>
          <div className={styles.nursePair} data-points={p.consciousness}>
            <span className={styles.nurseLabel}>AVPU</span>
            <MeasurementDisplay measurement={row.vitals.consciousness} />
          </div>
          <div className={styles.labLine}>
            {row.labs.freshness_hours !== null
              ? `Labs ${row.labs.freshness_hours}h`
              : "No labs"}
          </div>
          <ObsDue due={obsDue(row)} now={now} noPatch={age === null || !patch} />
        </div>
      </div>
    </Link>
  );
}
