import { Link } from "react-router-dom";
import type { AbstainedRow as AbstainedRowType } from "../types";
import styles from "./AbstainedRow.module.css";
import { formatAge, formatBloodPressure, formatClockDay } from "./format";
import { useWardNow } from "./wardNow";
import { sentence } from "./vitalsDraft";

const SEEN = [
  ["heart_rate", "HR"],
  ["respiratory_rate", "RR"],
  ["spo2", "SpO2"],
  ["temperature", "Temp"],
  ["blood_pressure", "BP"],
] as const;

/** The last stored values, each with where it came from and when. Shown only: no score reads them (I1). */
function LastSeen({ seen, now }: { seen: NonNullable<AbstainedRowType["last_seen"]>; now: number }) {
  const items = SEEN.flatMap(([code, label]) => {
    const m = seen[code];
    if (!m.data.present) return [];
    const at = Date.parse(m.provenance.observed_at);
    const value = typeof m.data.value === "number" ? m.data.value : formatBloodPressure(m.data.value);
    const age = now > 0 ? `${formatAge(Math.max(0, Math.floor((now - at) / 1000)))}, ` : "";
    const quality = m.provenance.quality === "ok" ? "" : `${m.provenance.quality}, `;
    return [`${label} ${value} (${m.provenance.source}, ${quality}${age}${formatClockDay(at, now)})`];
  });
  return (
    <p className={styles.seen}>
      Last seen: {items.length > 0 ? items.join(" · ") : "no value stored"}
    </p>
  );
}

interface Props {
  row: AbstainedRowType;
  /** HTTP mode only: the fixture provider cannot re-rank, so it offers no entry (D4). */
  canRecord?: boolean;
  /** Opens the Record vitals drawer for this row (the page holds it, so it outlives the row). */
  onRecord?: (row: AbstainedRowType) => void;
  /** Offline: Record vitals waits until the connection is back. */
  disabled?: boolean;
}

export function AbstainedRow({ row, canRecord = false, onRecord, disabled = false }: Props) {
  const now = useWardNow();
  return (
    <div className="hw-notice__item">
      <div className="hw-notice__main">
        <Link to={`/patient/${row.id}`} className="hw-notice__name">{row.name}</Link>
        <span className="hw-notice__meta">
          {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
        </span>
        <p className="hw-notice__reason"><strong>{sentence(row.reason)}</strong> {sentence(row.instruction)}</p>
        {row.last_seen && <LastSeen seen={row.last_seen} now={now} />}
      </div>
      {canRecord && (
        <button type="button" className="hw-button hw-button--accent" aria-haspopup="dialog" disabled={disabled} onClick={() => onRecord?.(row)}>
          Record vitals
        </button>
      )}
    </div>
  );
}
