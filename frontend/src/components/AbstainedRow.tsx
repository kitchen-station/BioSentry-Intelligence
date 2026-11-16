import { useState } from "react";
import type { AbstainedRow as AbstainedRowType, VitalsRecorded } from "../types";
import styles from "./AbstainedRow.module.css";
import { formatAge, formatBloodPressure, formatClockDay } from "./format";
import { useWardNow } from "./wardNow";
import { VitalsEntry } from "./VitalsEntry";

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
    <div className={styles.seen}>
      Last seen: {items.length > 0 ? items.join(" · ") : "no value stored"}
    </div>
  );
}

interface Props {
  row: AbstainedRowType;
  /** HTTP mode only: the fixture provider cannot re-rank, so it offers no entry (D4). */
  canRecord?: boolean;
  /** Who is recording (shown in the echo of what will be stored). */
  clinicianId?: string;
  onRecorded?: (row: AbstainedRowType, recorded: VitalsRecorded) => void;
}

export function AbstainedRow({ row, canRecord = false, clinicianId = "", onRecorded }: Props) {
  const [entering, setEntering] = useState(false);
  const now = useWardNow();
  return (
    <div className={styles.row}>
      <div className={styles.tagCol}>
        <span className={styles.tag}>NOT RANKED</span>
      </div>
      <div className={styles.infoCol}>
        <div className={styles.nameRow}>
          <span className={styles.name}>{row.name}</span>
          <span className={styles.detail}>
            {row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""} &middot; Bed {row.bed}
          </span>
        </div>
        <div className={styles.context}>{row.admitting_context}</div>
      </div>
      <div className={styles.reasonCol}>
        <div className={styles.reason}>{row.reason}</div>
        <div className={styles.instruction}>{row.instruction}</div>
        {row.last_seen && <LastSeen seen={row.last_seen} now={now} />}
        {canRecord && (
          <button
            type="button"
            className={styles.action}
            aria-expanded={entering}
            onClick={() => setEntering((open) => !open)}
          >
            {entering ? "Close" : "Record vitals"}
          </button>
        )}
      </div>
      {canRecord && entering && (
        <div className={styles.entry}>
          <VitalsEntry
            patientId={row.id}
            patientName={row.name}
            clinicianId={clinicianId}
            onRecorded={(recorded) => onRecorded?.(row, recorded)}
            onClose={() => setEntering(false)}
          />
        </div>
      )}
    </div>
  );
}
