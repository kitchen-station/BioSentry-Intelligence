import { useCallback, useEffect, useState } from "react";
import type { AbstainedRow as AbstainedRowType, Session, VitalsRecorded, WardResponse } from "../types";
import { getWard } from "../api";
import { WardRow } from "../components/WardRow";
import { band } from "../components/risk";
import { SessionMenu } from "../components/SessionMenu";
import { standing } from "../components/vitalsDraft";
import { NotRankedSection } from "./NotRankedSection";
import styles from "./Ward.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

export function Ward({ session, onSignOut }: Props) {
  const [data, setData] = useState<WardResponse | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getWard().then(setData);
  }, []);

  // A recorded value changes a patient's standing, which only the server can compute: say
  // what it said, then fetch the ward again so the ranking on screen is the server's.
  const handleRecorded = useCallback((row: AbstainedRowType, recorded: VitalsRecorded) => {
    setNotice(`Recorded for ${row.name}. ${standing(recorded)}`);
    getWard()
      .then(setData)
      .catch(() => setNotice("Recorded, but the ward could not be refreshed. Reload to see the ranking."));
  }, []);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!data) return null;

  const liveTiers = data.tier_status.filter((t) => t.live);
  const bands = data.ranked.map(band);
  const high = bands.filter((b) => b === "high").length;
  const medium = bands.filter((b) => b === "medium" || b === "low-medium").length;
  const rising = data.ranked.filter((r) => r.trend === "rising").length;

  return (
    <div className={styles.page}>
      {/* Status bar */}
      <div className={styles.statusBar}>
        <div className={styles.statusInner}>
          <div className={styles.statusLeft}>
            <span className={styles.statusItem}>
              <span className={styles.statusLabel}>BioSentry</span>
              <span className={styles.statusValue}>Ward {data.ward}</span>
            </span>
            <span className={styles.statusDivider} />
            <span className={styles.statusItem}>
              <span className={`${styles.liveDot} ${data.receiving_data ? styles.liveDotActive : ""}`} />
              <span className={styles.statusValue}>
                {data.receiving_data
                  ? now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
                  : "No signal"}
              </span>
            </span>
          </div>
          <div className={styles.statusRight}>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>

      {/* Summary cards */}
      <div className={styles.summary}>
        <div className={styles.summaryInner}>
          <div className={styles.summaryCard} data-tone={high > 0 ? "alert" : undefined}>
            <span className={styles.summaryNumber}>{high}</span>
            <span className={styles.summaryLabel}>High risk &middot; NEWS2 7+</span>
          </div>
          <div className={styles.summaryCard} data-tone={medium > 0 ? "alert-soft" : undefined}>
            <span className={styles.summaryNumber}>{medium}</span>
            <span className={styles.summaryLabel}>Medium &middot; 5&ndash;6 / single 3</span>
          </div>
          <div className={styles.summaryCard}>
            <span className={styles.summaryNumber}>{rising}</span>
            <span className={styles.summaryLabel}>Rising</span>
          </div>
          <div className={styles.summaryCard} data-tone={data.abstained.length > 0 ? "secondary" : undefined}>
            <span className={styles.summaryNumber}>{data.abstained.length}</span>
            <span className={styles.summaryLabel}>Not ranked &middot; act</span>
          </div>
          <div className={styles.summaryCard}>
            <span className={styles.summaryNumber}>{data.ranked.length}</span>
            <span className={styles.summaryLabel}>Ranked</span>
          </div>
        </div>
      </div>

      {/* Main content */}
      <main className={styles.container}>
        <div className={styles.sectionHeader}>
          <p className={styles.eyebrow}>Priority queue</p>
          <p className={styles.sortInfo}>
            Sorted on {liveTiers.map((t) => t.label).join(", ") || `Tier ${data.sort_tier}`}
          </p>
        </div>

        {notice !== null && (
          <p className={styles.notice} role="status">
            {notice}{" "}
            <button type="button" className={styles.noticeClose} onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}

        <div className={styles.rowList}>
          {data.ranked.map((row) => (
            <WardRow key={row.id} row={row} role={session.role} />
          ))}
        </div>

        <NotRankedSection
          rows={data.abstained}
          clinicianId={session.clinician_id}
          onRecorded={handleRecorded}
        />
      </main>

      <footer className={styles.footer}>
        Advisory only. BioSentry does not replace a monitor alarm. Every view is
        logged for audit under {session.clinician_id}.
      </footer>
    </div>
  );
}
