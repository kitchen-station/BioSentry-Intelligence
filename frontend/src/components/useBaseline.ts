import { useEffect, useState } from "react";
import { getBaseline } from "../api";
import type { BaselineResponse } from "../types";

/** undefined: loading. null: this build has no finding to show (fixture mode). */
export type BaselineState = BaselineResponse | null | "failed" | undefined;

const REFRESH_MS = 60_000;

/**
 * The patient's own-baseline finding, fetched for the id and again every minute while the page is
 * open (the age in it is the server's, so only a new answer moves it). A failure, including a
 * failed refetch, or an answer about another patient, is "failed" and never throws. The answer is
 * kept with the id it was asked for, so one that arrives after the page has moved to another
 * patient is never shown. A change of `refresh` fetches again at once: the page passes its count of
 * saves, because a recorded value can change what counts.
 */
export function useBaseline(id: string | undefined, refresh = 0): BaselineState {
  const [got, setGot] = useState<{ id: string; value: BaselineState }>();
  useEffect(() => {
    if (!id) return;
    let current = true; // false once the page has moved on: a late answer must not replace a newer one
    const load = () =>
      getBaseline(id)
        .then((b) => current && setGot({ id, value: b === null || b.patient_id === id ? b : "failed" }))
        .catch(() => current && setGot({ id, value: "failed" }));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [id, refresh]);
  return got && got.id === id ? got.value : undefined;
}
