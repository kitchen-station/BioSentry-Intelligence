import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "hw-ui";
import type { ScoreRow, Session, WardResponse } from "../types";
import { acknowledge as saveAcknowledgement, getAcknowledgements, getWard, usesServer } from "../api";
import { unacknowledged } from "./risk";
import { useLivePoll } from "./useLivePoll";
import { syncWardNow } from "./wardNow";
import { WardFeedContext } from "./wardFeed";

/** How long an acknowledgement can be undone before it is saved. There is no un-acknowledge on the server. */
export const UNDO_MS = 5_000;

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
    // No audio available: the bell and the row still show.
  }
}

interface Waiting {
  readonly score: number;
  readonly timer: ReturnType<typeof setTimeout>;
  readonly dismiss: () => void;
}

/**
 * Reads the ward for every page of a signed-in clinician (App) or for the administrator's Ward page
 * (read only: once and on Refresh, no alerts). The Ward rows and the bell read this one feed.
 */
export function WardFeedProvider({ session, children }: { session: Session; children: ReactNode }) {
  const readOnly = session.role === "admin";
  const [data, setData] = useState<WardResponse | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [failedSince, setFailedSince] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // null until the server's acknowledgements arrive, so an alert already acknowledged never sounds.
  const [acked, setAcked] = useState<ReadonlyMap<string, number> | null>(null);
  // Acknowledged on screen, not yet saved: hidden while the Undo window is open and the save is in flight.
  const [hidden, setHidden] = useState<ReadonlyMap<string, number>>(() => new Map());
  const waiting = useRef(new Map<string, Waiting>());
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

  const alerts = data && acked ? unacknowledged(data.ranked, acked).filter((r) => r.score > (hidden.get(r.id) ?? -1)) : [];
  const alertKey = alerts.map((a) => `${a.id}:${a.score}`).join(",");

  // Sound once per new alert, and show the count in the tab title so a background tab is seen.
  useEffect(() => {
    const fresh = alertKey.split(",").filter((k) => k && !alerted.current.has(k));
    if (fresh.length > 0) beep();
    fresh.forEach((k) => alerted.current.add(k));
    const n = alertKey ? alertKey.split(",").length : 0;
    document.title = n > 0 ? `(${n}) BioSentry` : "BioSentry";
    return () => {
      document.title = "BioSentry";
    };
  }, [alertKey]);

  const show = useCallback((id: string) => {
    setHidden((m) => {
      const next = new Map(m);
      next.delete(id);
      return next;
    });
  }, []);

  // Saves one acknowledgement out of its Undo window. The alert stays hidden until the server
  // answers; a refusal brings it back and says so.
  const commit = useCallback((id: string): Promise<void> => {
    const w = waiting.current.get(id);
    if (!w) return Promise.resolve();
    waiting.current.delete(id);
    clearTimeout(w.timer);
    w.dismiss();
    return saveAcknowledgement(id, w.score).then(
      () => {
        setAcked((prev) => new Map(prev).set(id, w.score));
        show(id);
      },
      () => {
        show(id);
        setNotice("Could not save the acknowledgement. The alert stays open: try again.");
      },
    );
  }, [show]);

  const undo = useCallback((id: string) => {
    const w = waiting.current.get(id);
    if (!w) return;
    waiting.current.delete(id);
    clearTimeout(w.timer);
    show(id);
  }, [show]);

  const acknowledge = useCallback((row: ScoreRow) => {
    if (waiting.current.has(row.id)) return;
    setHidden((m) => new Map(m).set(row.id, row.score));
    // One clock: the toast stays until the save starts (timeout 0), so Undo is never offered after it.
    const dismiss = toast({
      message: `Alert acknowledged for ${row.name}.`,
      timeout: 0,
      action: { label: "Undo", onClick: () => undo(row.id) },
    });
    waiting.current.set(row.id, { score: row.score, dismiss, timer: setTimeout(() => void commit(row.id), UNDO_MS) });
  }, [commit, undo]);

  const flush = useCallback(
    () => Promise.all([...waiting.current.keys()].map(commit)).then(() => undefined),
    [commit],
  );

  // Leaving the page or this feed (sign-out, unmount) saves what is waiting: an acknowledgement is never lost to a timer.
  useEffect(() => {
    const onHide = () => void flush();
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      void flush();
    };
  }, [flush]);

  return (
    <WardFeedContext.Provider
      value={{ data, fetchedAt, failedSince, sim, refresh, pollMs, readOnly, alerts, acknowledge, flush, notice, setNotice }}
    >
      {children}
    </WardFeedContext.Provider>
  );
}
