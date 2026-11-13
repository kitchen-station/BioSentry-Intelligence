// @vitest-environment jsdom
/** When the next set of observations is due, as the row shows it: ahead, overdue, or now. */

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { ScoreRow } from "../types";
import { enteredByNurse, formatClock } from "./format";
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

describe("a row ranked from nurse entries", () => {
  const hr = row.vitals.heart_rate;
  const nurseRow: ScoreRow = {
    ...row,
    signal_quality: null,
    last_update_s: null,
    vitals: { ...row.vitals, heart_rate: { ...hr, provenance: { ...hr.provenance, source: "nurse", observed_at: "2026-09-03T06:45:00Z" } } },
  };
  const showRow = (r: ScoreRow) =>
    render(
      <MemoryRouter>
        <WardRow row={r} role="rn" now={0} ageOffsetS={0} />
      </MemoryRouter>,
    );

  it("says who entered the values, and that there is no patch signal, with no made-up number", () => {
    showRow(nurseRow);
    expect(screen.getByText(enteredByNurse(Date.parse("2026-09-03T06:45:00Z")))).toBeTruthy();
    expect(screen.getByText("No patch signal")).toBeTruthy();
    expect(screen.queryByText(/Signal \d+%/)).toBeNull();
    expect(screen.queryByText(/ago$/)).toBeNull();
  });

  it("shows the signal figure alone when only the age is null", () => {
    showRow({ ...nurseRow, signal_quality: 0 });
    expect(screen.getByText("Signal 0%")).toBeTruthy();
    expect(screen.queryByText("No patch signal")).toBeNull();
  });

  it("asks for continuous monitoring when a high-risk patient has no patch", () => {
    showRow({ ...nurseRow, score: 9 });
    expect(screen.getByText("Continuous monitoring needed: no patch")).toBeTruthy();
    expect(screen.queryByText("Obs continuous")).toBeNull();
  });

  it("keeps the stale age, and no no-signal text, when the patch was removed", () => {
    showRow({ ...nurseRow, signal_quality: 0, last_update_s: 7200 });
    expect(screen.getByText(/^Stale · /)).toBeTruthy();
    expect(screen.queryByText("No patch signal")).toBeNull();
  });

  it("shows the patch age and the earliest nurse time on a mixed row", () => {
    const v = row.vitals;
    const by = (m: typeof v.heart_rate, source: "nurse" | "device", at: string) => ({
      ...m, provenance: { ...m.provenance, source, observed_at: at },
    });
    showRow({
      ...row,
      signal_quality: 95,
      last_update_s: 30,
      vitals: {
        ...v,
        heart_rate: by(v.heart_rate, "nurse", "2026-09-03T06:45:00Z"),
        temperature: by(v.temperature!, "nurse", "2026-09-03T06:30:00Z"),
        respiratory_rate: by(v.respiratory_rate, "device", "2026-09-03T07:00:00Z"),
        spo2: by(v.spo2, "device", "2026-09-03T07:00:00Z"),
      },
    });
    expect(screen.getByText(enteredByNurse(Date.parse("2026-09-03T06:30:00Z")))).toBeTruthy();
    expect(screen.getByText("30s ago")).toBeTruthy();
  });
});
