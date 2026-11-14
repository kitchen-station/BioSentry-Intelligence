import { useEffect, useState } from "react";

/**
 * Ward time as the page knows it: the server's `now` of the last good ward or patient read, as
 * served. It never moves by the browser's seconds: the ward's own clock stands still when the
 * simulator is paused and runs faster than real time when it is not, so only a read moves it.
 * One value for every page, so a page that reads no ward still has the newest one.
 *
 * The one exception is the demo with no server: its ward time IS the browser's clock (the
 * fixtures say so), so there it advances by the browser's seconds since the read.
 */
let served = 0;
let readAt = 0;
let paced = false;

/** Call on every good ward or patient read, with the answer's `now`; `demo` is true with no server. */
export function syncWardNow(now: string, demo = false): void {
  served = Date.parse(now);
  readAt = Date.now();
  paced = demo;
}

/** Whether any read has set the clock. */
export function hasWardNow(): boolean {
  return served > 0;
}

/** Test only: forget every read. */
export function forgetWardNow(): void {
  served = 0;
  paced = false;
}

/** Ward time in ms as of now; 0 before any read (a page shows no time before its first read). */
export const wardNowMs = (): number => (served > 0 && paced ? served + (Date.now() - readAt) : served);

/** Ward time for a component to read on each render; a read of the ward re-renders it, and the demo ticks each second. */
export function useWardNow(): number {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => paced && tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return wardNowMs();
}

/**
 * The time to print for something that began at browser time `marker` (a failed read): the ward's
 * time of the last good read when there is one. The browser's clock only marks that it began, and
 * stands in only before any read, when the ward has no time to give.
 */
export const since = (marker: number): number => (served > 0 ? wardNowMs() : marker);
