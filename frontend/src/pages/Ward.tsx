import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { AbstainedRow as AbstainedRowType, Session, VitalsRecorded, WardResponse } from "../types";
import {
  acknowledge as saveAcknowledgement,
  getAcknowledgements,
  getWard,
  usesServer,
} from "../api";
import { Flip, useMoveOnReorder } from "../components/Flip";
import { WardRow } from "../components/WardRow";
import { band, obsDue, unacknowledged } from "../components/risk";
import { formatClock, formatSimClock } from "../components/format";
import { useLivePoll } from "../components/useLivePoll";
import { since, syncWardNow, useWardNow } from "../components/wardNow";
import { SimulatedNote } from "../components/LabFrame";
import { AppShell } from "../components/AppShell";
import { standing } from "../components/vitalsDraft";
import { NotRankedSection } from "./NotRankedSection";
import styles from "./Ward.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

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
  // An admin reads the ward: loaded once and on Refresh, no timer, no alerts, no write control.
  const readOnly = session.role === "admin";
  const [data, setData] = useState<WardResponse | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [failedSince, setFailedSince] = useState<number | null>(null);
  const now = useWardNow(); // ward time as served at the last good read
  const [notice, setNotice] = useState<string | null>(null);
  // null until the server's acknowledgements arrive, so an alert already acknowledged never sounds.
  const [acked, setAcked] = useState<ReadonlyMap<string, number> | null>(null);
  const alerted = useRef(new Set<string>());

  // Keeps the last good ward on screen when a fetch fails, and says since when. Each request takes
  // the next number and an answer counts only if no later request was answered first: a slow old
  // answer never brings back an older ward.
  const issued = useRef(0);
  const applied = useRef(0);
  const load = useCallback(async () => {
    const n = ++issued.current;
    try {
      const w = await getWard();
      if (n > applied.current) {
        applied.current = n;
        syncWardNow(w.now, !usesServer);
        setData(w);
        setFetchedAt(Date.parse(w.now)); // the ward's own time of the read
        setFailedSince(null);
      }
      return true;
    } catch {
      if (n > applied.current) setFailedSince((t) => t ?? Date.now());
      return false;
    }
  }, []);
  // The speed comes from the live view read in the same round: see useLivePoll.
  const { sim, refresh, pollMs } = useLivePoll(load, !readOnly);

  // A recorded value changes a patient's standing, which only the server can compute: say
  // what it said, then fetch the ward again so the ranking on screen is the server's.
  const handleRecorded = useCallback((row: AbstainedRowType, recorded: VitalsRecorded) => {
    setNotice(`Recorded for ${row.name}. ${standing(recorded)}`);
    refresh();
  }, [refresh]);

  // If they cannot be loaded every alert stays open: showing one twice is safer than hiding one.
  useEffect(() => {
    if (readOnly) return;
    getAcknowledgements().then(
      (list) => setAcked(new Map(list.map((a) => [a.patient_id, a.score]))),
      () => {
        setAcked(new Map());
        setNotice("Could not load your acknowledgements. Alerts you already acknowledged may show again.");
      },
    );
  }, [readOnly]);

  const moveRef = useMoveOnReorder(data ? data.ranked.map((r) => r.id) : []);

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

  const retry = readOnly ? "Use Refresh to try again." : `Retrying every ${pollMs / 1000} seconds.`;

  if (!data) {
    return (
      <AppShell session={session} onSignOut={onSignOut}>
        <SimulatedNote />
        <div className={styles.container}>
          <p role={failedSince ? "alert" : "status"} className={failedSince ? "hw-alert hw-alert--danger" : styles.loading}>
            {failedSince
              ? `The ward server cannot be reached. ${retry}`
              : "Loading ward…"}
          </p>
          {readOnly && failedSince && <button type="button" className="hw-button hw-button--sm" onClick={refresh}>Refresh</button>}
        </div>
      </AppShell>
    );
  }

  const ageOffsetS = Math.max(0, Math.floor((now - Date.parse(data.now)) / 1000)); // time since the read, on the ward's base
  const overdue = data.ranked.filter((r) => {
    const due = obsDue(r);
    return due !== "continuous" && due <= now;
  }).length;

  const liveTiers = data.tier_status.filter((t) => t.live);
  const bands = data.ranked.map(band);
  const high = bands.filter((b) => b === "high").length;
  const medium = bands.filter((b) => b === "medium" || b === "low-medium").length;
  const rising = data.ranked.filter((r) => r.trend === "rising").length;

  const status = (
    <>
      <span className={styles.statusItem} data-tone={!data.receiving_data || failedSince ? "warn" : undefined}>
        <span className="hw-icon fa-solid fa-signal" aria-hidden="true" />
        {!data.receiving_data
          ? "No signal"
          : failedSince
            ? `Offline since ${formatClock(since(failedSince))}`
            : `Updated ${fetchedAt ? formatClock(fetchedAt) : "--"}`}
      </span>
      {sim !== null && (
        <span className={styles.statusItem} role="group" aria-label="Ward clock">
          <span className={styles.statusLabel}>Ward clock</span>
          <span className={styles.statusValue}><Flip>{formatSimClock(sim)}</Flip></span>
        </span>
      )}
    </>
  );

  return (
    <AppShell session={session} onSignOut={onSignOut} title={`Ward ${data.ward}`} status={status}>
      {failedSince && (
        <p className={`hw-alert hw-alert--warning ${styles.offline}`} role="status">
          <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
          <span className="hw-alert__body">
            Cannot reach the ward server since {formatClock(since(failedSince))}. Showing data from{" "}
            {fetchedAt ? formatClock(fetchedAt) : "--"}; ages below stand at that time. {retry}
          </span>
        </p>
      )}
      <SimulatedNote />
      <div className={styles.container}>

        {/* Summary: one stat per question a charge nurse asks on arrival. */}
        <div className={styles.summary}>
          <div className={`hw-stat ${styles.stat}`} data-tone={high > 0 ? "alert" : undefined}>
            <span className="hw-stat__value"><Flip>{high}</Flip></span>
            <span className="hw-stat__label">High risk &middot; NEWS2 7+</span>
          </div>
          <div className={`hw-stat ${styles.stat}`} data-tone={medium > 0 ? "urgent" : undefined}>
            <span className="hw-stat__value"><Flip>{medium}</Flip></span>
            <span className="hw-stat__label">Medium &middot; 5&ndash;6 / single 3</span>
          </div>
          <div className={`hw-stat ${styles.stat}`}>
            <span className="hw-stat__value"><Flip>{rising}</Flip></span>
            <span className="hw-stat__label">Rising</span>
          </div>
          <div className={`hw-stat ${styles.stat}`} data-tone={data.abstained.length > 0 ? "act" : undefined}>
            <span className="hw-stat__value"><Flip>{data.abstained.length}</Flip></span>
            <span className="hw-stat__label">Not ranked &middot; act</span>
          </div>
          <div className={`hw-stat ${styles.stat}`} data-tone={overdue > 0 ? "urgent" : undefined}>
            <span className="hw-stat__value"><Flip>{overdue}</Flip></span>
            <span className="hw-stat__label">Obs overdue</span>
          </div>
        </div>

        <div className={styles.sectionHeader}>
          <h1 className={styles.eyebrow}>Priority queue</h1>
          <p className={styles.sortInfo}>
            Sorted on {liveTiers.map((t) => t.label).join(", ") || "NEWS2"}
          </p>
        </div>

        {readOnly && (
          <p className={`hw-alert ${styles.block}`}>
            <span className="hw-alert__body">Read-only. Views are recorded in the audit log.</span>{" "}
            <button type="button" className="hw-button hw-button--sm" onClick={refresh}>Refresh</button>
          </p>
        )}

        {alerts.length > 0 && (
          <div className={styles.alerts}>
            {alerts.map((a) => (
              <div key={a.id} className={`hw-alert hw-alert--danger ${styles.alert}`} role="alert">
                <span className="hw-alert__icon hw-icon fa-solid fa-bell" aria-hidden="true" />
                <span className="hw-alert__body">
                  <strong>NEWS2 {a.score}</strong> &middot; Bed {a.bed} &middot;{" "}
                  <Link to={`/patient/${a.id}`}>{a.name}</Link>
                </span>
                <button type="button" className="hw-button hw-button--primary hw-button--sm" onClick={() => acknowledge(a.id, a.score)}>
                  Acknowledge
                </button>
              </div>
            ))}
          </div>
        )}

        {notice !== null && (
          <p className={`hw-alert ${styles.block}`} role="status">
            <span className="hw-alert__body">{notice}</span>{" "}
            <button type="button" className="hw-button hw-button--ghost hw-button--sm" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}

        <div className={styles.rowList}>
          {data.ranked.map((row) => (
            <div key={row.id} ref={moveRef(row.id)}>
              <WardRow row={row} role={session.role} now={now} ageOffsetS={ageOffsetS} />
            </div>
          ))}
        </div>

        <NotRankedSection
          rows={data.abstained}
          clinicianId={session.clinician_id}
          readOnly={readOnly}
          onRecorded={handleRecorded}
        />

        <p className={styles.footer}>
          Advisory only. BioSentry does not replace a monitor alarm. Views are
          logged for audit under {session.clinician_id}.
        </p>
      </div>
    </AppShell>
  );
}
