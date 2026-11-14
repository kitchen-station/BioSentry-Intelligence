import { useEffect, useState } from "react";

/**
 * Ward time as the page knows it: the server's `now` of the last good ward or patient read, and
 * the time that has passed in the browser since that read arrived (a difference of two readings
 * of the browser clock, never its absolute value). One anchor for every page, so a page that
 * reads no ward still has the newest one. It keeps advancing while reads fail: ages grow.
 */
let anchor: { server: number; local: number } | null = null;

/** Call on every good ward or patient read, with the answer's `now`. */
export function syncWardNow(served: string): void {
  const server = Date.parse(served);
  // A seeded ward serves one fixed `now`: keep the first anchor so the clock does not jump back to it at each read.
  if (anchor && anchor.server === server) return;
  anchor = { server, local: Date.now() };
}

/** Whether any read has set the clock. */
export function hasWardNow(): boolean {
  return anchor !== null;
}

/** Test only: forget every read. */
export function forgetWardNow(): void {
  anchor = null;
}

const wardNow = (): number => (anchor ? anchor.server + (Date.now() - anchor.local) : 0);

/** Ward time in ms; 0 before any read (a page shows no time before its first read). Ticks each second. */
export function useWardNow(): number {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return wardNow();
}
