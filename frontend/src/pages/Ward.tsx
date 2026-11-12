import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { AbstainedRow as AbstainedRowType, Session, VitalsRecorded, WardResponse } from "../types";
import {
  acknowledge as saveAcknowledgement,
  getAcknowledgements,
  getLiveWard,
  getWard,
  usesServer,
} from "../api";
import { WardRow } from "../components/WardRow";
import { band, obsDue, unacknowledged } from "../components/risk";
import { formatClock, formatSimClock, simStatus } from "../components/format";
import type { SimStatus } from "../components/format";
import { SessionMenu } from "../components/SessionMenu";
import { standing } from "../components/vitalsDraft";
import { NotRankedSection } from "./NotRankedSection";
import styles from "./Ward.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

const SIM_POLL_MS = 2_000;

function beep(): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    osc.frequency.value = 880;
    osc.connect(ctx.destination);
    osc.onended = () => void ctx.close();
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
  } catch {
    // No audio available: the banner still shows.
  }
}

function setTitle(title: string): void {
  document.title = title;
}

export function Ward({ session, onSignOut }: Props) {
  const [data, setData] = useState<WardResponse | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [failedSince, setFailedSince] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [notice, setNotice] = useState<string | null>(null);
  // null until the server's acknowledgements arrive, so an alert already acknowledged never sounds.
  const [acked, setAcked] = useState<ReadonlyMap<string, number> | null>(null);
  const alerted = useRef(new Set<string>());
  // Null until the simulator's status arrives, and whenever there is none to show.
  const [sim, setSim] = useState<SimStatus | null>(null);
  // The API changes as the simulator runs: ask it more often than the fixture, which never changes.
  const pollMs = usesServer ? 5_000 : 15_000;

  // Keeps the last good ward on screen when a fetch fails, and says since when.
  const refresh = useCallback(() => {
    getWard()
      .then((w) => {
        setData(w);
        setFetchedAt(Date.now());
        setFailedSince(null);
      })
      .catch(() => setFailedSince((t) => t ?? Date.now()));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, pollMs);
    return () => clearInterval(t);
  }, [refresh, pollMs]);

  // The simulator's status line: no line when the fetch fails or `shared` is not what it publishes.
  useEffect(() => {
    const poll = () =>
      getLiveWard().then(
        (live) => setSim(simStatus(live?.shared)),
        () => setSim(null),
      );
    void poll();
    const t = setInterval(poll, SIM_POLL_MS);
    return () => clearInterval(t);
  }, []);

  // A recorded value changes a patient's standing, which only the server can compute: say
  // what it said, then fetch the ward again so the ranking on screen is the server's.
  const handleRecorded = useCallback((row: AbstainedRowType, recorded: VitalsRecorded) => {
    setNotice(`Recorded for ${row.name}. ${standing(recorded)}`);
    refresh();
  }, [refresh]);

  // If they cannot be loaded every alert stays open: showing one twice is safer than hiding one.
  useEffect(() => {
    getAcknowledgements().then(
      (list) => setAcked(new Map(list.map((a) => [a.patient_id, a.score]))),
      () => {
        setAcked(new Map());
        setNotice("Could not load your acknowledgements. Alerts you already acknowledged may show again.");
      },
    );
  }, []);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const alerts = data && acked ? unacknowledged(data.ranked, acked) : [];
  const alertKey = alerts.map((a) => `${a.id}:${a.score}`).join(",");

  // Sound once per new alert, and show the count in the tab title so a background tab is seen.
  useEffect(() => {
    const fresh = alertKey.split(",").filter((k) => k && !alerted.current.has(k));
    if (fresh.length > 0) beep();
    fresh.forEach((k) => alerted.current.add(k));
    const n = alertKey ? alertKey.split(",").length : 0;
    setTitle(n > 0 ? `(${n}) BioSentry` : "BioSentry");
    return () => setTitle("BioSentry");
  }, [alertKey]);

  // The alert closes only once the server has recorded the acknowledgement.
  const acknowledge = (id: string, score: number) => {
    saveAcknowledgement(id, score).then(
      () => setAcked((prev) => new Map(prev).set(id, score)),
      () => setNotice("Could not save the acknowledgement. The alert stays open: try again."),
    );
  };

  if (!data) {
    return (
      <main className={styles.container}>
        <p role={failedSince ? "alert" : "status"}>
          {failedSince
            ? `The ward server cannot be reached. Retrying every ${pollMs / 1000} seconds.`
            : "Loading ward…"}
        </p>
      </main>
    );
  }

  const ageOffsetS = fetchedAt ? Math.max(0, Math.floor((now - fetchedAt) / 1000)) : 0;
  const overdue = data.ranked.filter((r) => {
    const due = obsDue(r);
    return due !== "continuous" && due <= now;
  }).length;

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
              <span className={styles.statusValue}>
                {!data.receiving_data
                  ? "No signal"
                  : failedSince
                    ? `Offline since ${formatClock(failedSince)}`
                    : `Updated ${fetchedAt ? formatClock(fetchedAt) : "--"}`}
              </span>
            </span>
          </div>
          <div className={styles.statusRight}>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>

      {failedSince && (
        <p className={styles.offline} role="status">
          Cannot reach the ward server since {formatClock(failedSince)}. Showing data from{" "}
          {fetchedAt ? formatClock(fetchedAt) : "--"}; ages below keep counting. Retrying every {pollMs / 1000} seconds.
        </p>
      )}

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
          <div className={styles.summaryCard} data-tone={overdue > 0 ? "alert-soft" : undefined}>
            <span className={styles.summaryNumber}>{overdue}</span>
            <span className={styles.summaryLabel}>Obs overdue</span>
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

        {alerts.length > 0 && (
          <div className={styles.alerts}>
            {alerts.map((a) => (
              <div key={a.id} className={styles.alert} role="alert">
                <span>
                  <strong>NEWS2 {a.score}</strong> &middot; Bed {a.bed} &middot;{" "}
                  <Link to={`/patient/${a.id}`}>{a.name}</Link>
                </span>
                <button type="button" className={styles.alertAck} onClick={() => acknowledge(a.id, a.score)}>
                  Acknowledge
                </button>
              </div>
            ))}
          </div>
        )}

        {notice !== null && (
          <p className={styles.notice} role="status">
            {notice}{" "}
            <button type="button" className={styles.noticeClose} onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}

        {sim !== null && (
          <p className={styles.notice} role="status">
            Simulator: {formatSimClock(sim)} &middot; {sim.counts.received} received &middot;{" "}
            {sim.counts.rejected} rejected &middot; {sim.counts.abstained} abstained
          </p>
        )}

        <div className={styles.rowList}>
          {data.ranked.map((row) => (
            <WardRow key={row.id} row={row} role={session.role} now={now} ageOffsetS={ageOffsetS} />
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
