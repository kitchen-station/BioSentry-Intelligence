import { useId, useState } from "react";
import { canRecordVitals } from "../api";
import { AbstainedRow } from "../components/AbstainedRow";
import type { AbstainedRow as AbstainedRowType, VitalsRecorded } from "../types";
import styles from "./Ward.module.css";

interface Props {
  readonly rows: readonly AbstainedRowType[];
  readonly clinicianId: string;
  readonly readOnly?: boolean;
  /** Offline: Record vitals waits until the connection is back. */
  readonly offline?: boolean;
  readonly onRecorded: (row: AbstainedRowType, recorded: VitalsRecorded) => void;
}

/**
 * The NOT RANKED strip above the ranked list (kit hw-notice--dashed: dashed means incomplete).
 * Whether a row offers vitals entry is decided here, once, from the build's capability: only the
 * API can re-rank, so the fixture build offers none (D4).
 */
export function NotRankedSection({ rows, clinicianId, readOnly = false, offline = false, onRecorded }: Props) {
  const [open, setOpen] = useState(true);
  const heading = useId();
  const list = useId();
  if (rows.length === 0) return null;
  return (
    <section className={`hw-notice hw-notice--dashed ${styles.strip}`} aria-labelledby={heading}>
      <div className="hw-notice__head">
        <h2 id={heading} className="hw-notice__title">Not ranked ({rows.length})</h2>
        <p className="hw-notice__text">Action required to enable scoring. Not ranked does not mean low risk.</p>
        <button
          type="button"
          className="hw-notice__toggle"
          aria-expanded={open}
          aria-controls={list}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide" : "Show"}
        </button>
      </div>
      <div id={list} className="hw-notice__body" hidden={!open}>
        {rows.map((row) => (
          <AbstainedRow
            key={row.id}
            row={row}
            canRecord={canRecordVitals && !readOnly}
            clinicianId={clinicianId}
            onRecorded={onRecorded}
            disabled={offline}
          />
        ))}
      </div>
    </section>
  );
}
