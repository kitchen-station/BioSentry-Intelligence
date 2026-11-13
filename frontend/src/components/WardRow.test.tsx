// @vitest-environment jsdom
/** When the next set of observations is due, as the row shows it: ahead, overdue, or now. */

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { ScoreRow } from "../types";
import { enteredByNurse, formatClock } from "./format";
import { obsDue, response } from "./risk";
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

describe("an own-baseline drift reason on the row", () => {
  const drift = { rule: "tier_b_finding" as const, text: "Drifting from own baseline: heart rate, 3 h (synthetic, not validated)", pathway: "circulatory" };
  const showFlag = (score: number, role: "rn" | "md" | "charge_rn", reasons: ScoreRow["escalation_reasons"] = [drift]) =>
    render(
      <MemoryRouter>
        <WardRow row={{ ...row, score, escalation_reasons: reasons }} role={role} now={0} ageOffsetS={0} />
      </MemoryRouter>,
    );

  it("shows the reason and the role line on a low row", () => {
    showFlag(2, "rn");
    expect(screen.getByText(drift.text)).toBeTruthy();
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: nurse assessment")).toBeTruthy();
  });

  it("words the line for a physician and a charge nurse", () => {
    showFlag(2, "md");
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: no medical review triggered")).toBeTruthy();
    cleanup();
    showFlag(2, "charge_rn");
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: nurse assessment")).toBeTruthy();
  });

  it("words the line for score 0 as routine monitoring, and for a physician as no review", () => {
    showFlag(0, "rn");
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: routine monitoring")).toBeTruthy();
    cleanup();
    showFlag(0, "charge_rn");
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: routine monitoring")).toBeTruthy();
    cleanup();
    showFlag(0, "md");
    expect(screen.getByText("Not a NEWS2 trigger. NEWS2 response: no medical review triggered")).toBeTruthy();
  });

  it("keeps the band response and no role line on a flagged low-medium row", () => {
    const single = { rule: "tier_a_threshold" as const, text: "Single parameter at 3: heart rate", pathway: null };
    showFlag(3, "rn", [single, drift]);
    expect(screen.getByText(response("low-medium", "rn"))).toBeTruthy();
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
  });

  it("shows both texts when there are two drift reasons", () => {
    const second = { ...drift, text: "Drifting from own baseline: SpO2, 2 h (synthetic, not validated)" };
    showFlag(2, "rn", [drift, second]);
    expect(screen.getByText(`${drift.text} · ${second.text}`)).toBeTruthy();
  });

  it("keeps the NEWS2 response and no role line on a medium row", () => {
    showFlag(6, "rn");
    expect(screen.getByText("Inform the medical team now")).toBeTruthy();
    expect(screen.getByText(drift.text)).toBeTruthy();
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
  });

  it("shows no role line on a low row with a NEWS2 trigger reason only", () => {
    showFlag(3, "rn", [{ rule: "tier_a_threshold", text: "Single parameter at 3: heart rate", pathway: null }]);
    expect(screen.queryByText(/^Not a NEWS2 trigger/)).toBeNull();
  });
});

describe("a stored signal with no patch value in use", () => {
  const nurseAt = (m: typeof row.vitals.temperature) => ({ ...m, provenance: { ...m.provenance, source: "nurse" as const } });
  const v = row.vitals;
  const allNurse = { ...v, heart_rate: nurseAt(v.heart_rate), respiratory_rate: nurseAt(v.respiratory_rate), spo2: nurseAt(v.spo2), temperature: nurseAt(v.temperature) };
  const showSignal = (r: ScoreRow) =>
    render(
      <MemoryRouter>
        <WardRow row={r} role="rn" now={0} ageOffsetS={0} />
      </MemoryRouter>,
    );

  it("shows no signal figure and no age when all four values are nurse-typed", () => {
    showSignal({ ...row, signal_quality: 40, last_update_s: 30, vitals: allNurse });
    expect(screen.getByText("No patch signal")).toBeTruthy();
    expect(screen.queryByText(/Signal \d+%/)).toBeNull();
    expect(screen.queryByText(/ago$/)).toBeNull();
    expect(screen.getByText(/^Entered by a nurse at /)).toBeTruthy();
  });

  it("asks for continuous monitoring with no patch on a high row", () => {
    showSignal({ ...row, score: 9, signal_quality: 40, last_update_s: 30, vitals: allNurse });
    expect(screen.getByText("Continuous monitoring needed: no patch")).toBeTruthy();
  });

  it("shows the figure and age when one device value is present", () => {
    showSignal({ ...row, signal_quality: 40, last_update_s: 30, vitals: { ...allNurse, spo2: v.spo2 } });
    expect(screen.getByText("Signal 40%")).toBeTruthy();
    expect(screen.getByText("30s ago")).toBeTruthy();
    expect(screen.queryByText("No patch signal")).toBeNull();
  });
});
