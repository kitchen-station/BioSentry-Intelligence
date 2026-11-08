// @vitest-environment jsdom
/** When the next set of observations is due, as the row shows it: ahead, overdue, or now. */

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { ScoreRow } from "../types";
import { formatClock } from "./format";
import { obsDue } from "./risk";
import { WardRow } from "./WardRow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(cleanup);

// A patient below the high band, so observations have a due time rather than "continuous".
const row = (synthetic.ranked as unknown as ScoreRow[]).find((r) => r.score < 7)!;
const due = obsDue(row) as number;
const show = (now: number) =>
  render(
    <MemoryRouter>
      <WardRow row={row} role="rn" now={now} ageOffsetS={0} />
    </MemoryRouter>,
  );

describe("the observations due line", () => {
  it("shows the due time while it is still ahead", () => {
    expect(typeof due).toBe("number");
    show(due - 60_000);
    const line = screen.getByText(`Obs due ${formatClock(due)}`);
    expect(line.hasAttribute("data-overdue")).toBe(false);
  });

  it("is marked overdue once the time has passed", () => {
    show(due + 2 * 3_600_000);
    expect(screen.getByText(/^Obs overdue /).hasAttribute("data-overdue")).toBe(true);
  });
});
