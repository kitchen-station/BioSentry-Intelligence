import { useCallback, useEffect, useRef, useState } from "react";
import { getLiveWard, usesServer } from "../api";
import { simStatus } from "./format";
import type { SimStatus } from "./format";

/**
 * How often to read, from the ward's speed: the runner publishes its view every 2 s, so 60x in
 * front of the person is read at that rate; everything else with the API stays at 5 s, and a
 * hidden tab is not paused (the alert sound and the title count need it) but reads at 5 s.
 */
export function pollEvery(sim: SimStatus | null, hidden: boolean): number {
  if (!usesServer) return 15_000;
  return sim && sim.speed === 60 && !sim.ended && !hidden ? 2_000 : 5_000;
}

/**
 * One round is `load` plus, with the API and `enabled`, the ward's live view (the speed and the
 * clock). Rounds repeat at `pollEvery`, one in flight at a time. `enabled` false (an admin) runs a
 * round on mount and on `refresh()` only, and never asks for the live view. `paused` stops the timer.
 * `load` must handle its own errors.
 */
export function useLivePoll(load: () => Promise<unknown>, enabled: boolean, paused = false) {
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [hidden, setHidden] = useState(() => document.visibilityState === "hidden");
  const busy = useRef(false);

  const refresh = useCallback(() => {
    busy.current = true;
    const live =
      usesServer && enabled
        ? getLiveWard().then(
            (l) => setSim(simStatus(l?.shared)),
            () => setSim(null),
          )
        : Promise.resolve();
    void Promise.allSettled([load(), live]).then(() => {
      busy.current = false;
    });
  }, [load, enabled]);

  useEffect(() => {
    const onChange = () => setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  useEffect(() => refresh(), [refresh]);

  const pollMs = pollEvery(sim, hidden);
  useEffect(() => {
    if (!enabled || paused) return;
    const t = setInterval(() => {
      if (!busy.current) refresh();
    }, pollMs);
    return () => clearInterval(t);
  }, [refresh, pollMs, enabled, paused]);

  return { sim, refresh, pollMs };
}
