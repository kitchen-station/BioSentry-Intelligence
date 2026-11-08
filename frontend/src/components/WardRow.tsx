import { Link } from "react-router-dom";
import type { ScoreRow, SessionRole } from "../types";
import { formatAge, formatBloodPressure, STALE_AFTER_S } from "./format";
import { MeasurementDisplay } from "./MeasurementDisplay";
import { band, response } from "./risk";
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
}

export function WardRow({ row, role }: Props) {
  const b = band(row);
  return (
    <Link to={`/patient/${row.id}`} className={styles.link}>
      <div className={styles.row} data-band={b}>
        {/* Rank + score */}
        <div className={styles.rankCol}>
          <span className={styles.rank}>{row.rank}</span>
          <div className={styles.scoreBlock}>
            <span className={styles.score} aria-label={`NEWS2 ${row.score}, ${b} risk`}>{row.score}</span>
            <span className={styles.trend} data-trend={row.trend}>{TREND_CHAR[row.trend]}</span>
          </div>
        </div>

        {/* Patient info */}
        <div className={styles.infoCol}>
          <div className={styles.nameRow}>
            <span className={styles.signalDot} data-quality={row.signal_quality > 90 ? "good" : "degraded"} />
            <span className={styles.name}>{row.name}</span>
            <span className={styles.detail}>
              {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
            </span>
            <span className={styles.updateTag} data-stale={row.last_update_s > STALE_AFTER_S || undefined}>
              {row.last_update_s > STALE_AFTER_S ? "Stale · " : ""}{formatAge(row.last_update_s)}
            </span>
          </div>
          <div className={styles.context}>{row.admitting_context}</div>
          {b !== "low" && <div className={styles.response}>{response(b, role)}</div>}
          {row.escalation_reasons.length > 0 && (
            <div className={styles.reasonLine}>
              {row.escalation_reasons.map((r) => r.text).join(" · ")}
            </div>
          )}
        </div>

        {/* Sparklines */}
        <div className={styles.sparkCol}>
          <div className={styles.sparkRow}>
            <span className={styles.sparkLabel}>HR</span>
            <Sparkline data={row.vital_trends.heart_rate} width={72} height={20} fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.heart_rate} />
            </span>
          </div>
          <div className={styles.sparkRow}>
            <span className={styles.sparkLabel}>RR</span>
            <Sparkline data={row.vital_trends.respiratory_rate} width={72} height={20} fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.respiratory_rate} />
            </span>
          </div>
          <div className={styles.sparkRow}>
            <span className={styles.sparkLabel}>SpO2</span>
            <Sparkline data={row.vital_trends.spo2} width={72} height={20} fill />
            <span className={styles.sparkVal}>
              <MeasurementDisplay measurement={row.vitals.spo2} format={(v) => `${v}%`} />
            </span>
          </div>
        </div>

        {/* Nurse-entered + meta */}
        <div className={styles.metaCol}>
          <div className={styles.nursePair}>
            <span className={styles.nurseLabel}>BP</span>
            <MeasurementDisplay
              measurement={row.vitals.blood_pressure}
              format={formatBloodPressure}
            />
          </div>
          <div className={styles.nursePair}>
            <span className={styles.nurseLabel}>AVPU</span>
            <MeasurementDisplay measurement={row.vitals.consciousness} />
          </div>
          <div className={styles.labLine}>
            {row.labs.freshness_hours !== null
              ? `Labs ${row.labs.freshness_hours}h`
              : "No labs"}
          </div>
        </div>
      </div>
    </Link>
  );
}
