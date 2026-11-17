import { createContext, useContext } from "react";
import type { ScoreRow, WardResponse } from "../types";
import type { SimStatus } from "./format";

/**
 * The ward as this tab reads it: one read, shared by the Ward page, the bell and the search. The
 * provider (WardFeedProvider) holds the requests; this module holds none, so a page that only shows
 * the bar (the lab pages) imports no api call.
 */
export interface WardFeed {
  readonly data: WardResponse | null;
  /** The ward's own time of the last good read. */
  readonly fetchedAt: number | null;
  /** Browser time of the first failed read since the last good one; null while reads succeed. */
  readonly failedSince: number | null;
  readonly sim: SimStatus | null;
  readonly refresh: () => void;
  readonly pollMs: number;
  /** An administrator: read once and on Refresh, no alerts, no writes. */
  readonly readOnly: boolean;
  /** High-risk rows not acknowledged at their score, and not waiting out an Undo. */
  readonly alerts: readonly ScoreRow[];
  /** Hides the alert at once; saves after the Undo window. */
  readonly acknowledge: (row: ScoreRow) => void;
  /** Saves every acknowledgement still in its Undo window; resolves when the saves have settled. */
  readonly flush: () => Promise<void>;
  readonly notice: string | null;
  readonly setNotice: (notice: string | null) => void;
}

export const WardFeedContext = createContext<WardFeed | null>(null);

/** The feed, or null outside one (an administrator off the Ward page, or a test of a lone component). */
export const useWardFeed = (): WardFeed | null => useContext(WardFeedContext);
