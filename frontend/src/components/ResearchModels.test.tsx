// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { formatClock } from "./format";
import { ResearchModels } from "./ResearchModels";

afterEach(cleanup);

const START = "2026-09-03T06:30:00Z";
const END = "2026-09-03T06:45:00Z";
const sig = (state: string, extra: Record<string, unknown> = {}) => ({
  state, reason: null, bin_start: START, bin_end: END, ...extra,
});
const off = (reason: string) => ({ state: "not_computed", reason, bin_start: null, bin_end: null });
const show = (models: unknown) => render(<ResearchModels models={models} />);
// "Prediction model" is a required name, so the word list holds "predicts", not "predict".
const BAD = /\d\s*%|probab|risk of|predicts|\bAI\b/i;
const items = () => screen.getAllByRole("listitem");

describe("ResearchModels", () => {
  it("shows the heading, the note and the two entries in order", () => {
    show({ prediction: sig("no_flag"), alert_policy: sig("flag") });
    const region = screen.getByRole("region", { name: "Prediction models" });
    expect(within(region).getByRole("heading", { level: 2 }).textContent).toBe("Prediction models");
    expect(within(region).getByText("Synthetic, not validated. A flag adds a reason line. It never changes the NEWS2 score or the order. No flag is not an all-clear: NEWS2 alone sets the next step.")).toBeTruthy();
    expect(items()).toHaveLength(2);
    expect(items()[0].textContent).toContain("Prediction model · No flag");
    expect(items()[1].textContent).toContain("Alert policy · Flagged");
  });

  it("words flag, no flag and not sure, and never calls not sure safe", () => {
    show({ prediction: sig("not_sure"), alert_policy: sig("no_flag") });
    expect(items()[0].textContent).toContain("Not sure");
    expect(screen.getByText("The model's first check was met, but its confidence check could not confirm a flag. This is not an all-clear.")).toBeTruthy();
    expect(items()[0].textContent).not.toMatch(/safe|normal|low risk|no flag/i);
  });

  it.each([
    ["baseline forming", "the patient's own baseline is still forming"],
    ["latest bin not scored", "the last 15-minute set gave no NEWS2 score"],
    ["earlier bin not stored", "an earlier 15-minute set cannot be rebuilt"],
    ["a vital is not counted", "the patch is not streaming, or a value was typed"],
    ["stay past 672 bins", "the stay is longer than 7 days"],
    ["no stored bins", "no 15-minute set is stored yet"],
    ["vital series not on one grid", "the stored sets do not line up"],
    ["impossible value in a lag or baseline bin", "a stored value is not possible"],
    ["impossible value in a baseline bin", "a stored value is not possible"],
    ["feature out of range", "an input is out of range"],
  ])("maps the reason %s", (reason, words) => {
    show({ prediction: off(reason), alert_policy: off(reason) });
    expect(items()[0].textContent).toContain(`Prediction model · No result: ${words}`);
    expect(document.body.textContent).not.toMatch(/\bbins?\b/);
  });

  it.each(["student not loaded", "calibration not loaded", "policy not loaded", "model error"])(
    "a model that is not running (%s) is not listed by itself",
    (reason) => {
      show({ prediction: sig("flag"), alert_policy: off(reason) });
      expect(items()[0].textContent).toContain("Prediction model · Flagged");
      expect(items()[1].textContent).toBe("Alert policy · getting ready");
      expect(document.body.textContent).not.toMatch(/not loaded|model file|could not run|Not computed/);
    },
  );

  it("an unknown reason is a model that ran with no result: not getting ready, and the safety note shows", () => {
    const { container } = show({ prediction: off("<b>x</b>"), alert_policy: off("something new") });
    expect(screen.queryByText("Prediction models: getting ready.")).toBeNull();
    expect(screen.getByText(/Synthetic, not validated\./)).toBeTruthy();
    expect(items().map((li) => li.textContent)).toEqual([
      "Prediction model · No result for this patient now.",
      "Alert policy · No result for this patient now.",
    ]);
    expect(document.body.textContent).not.toContain("getting ready");
    expect(container.querySelector("b")).toBeNull(); // the reason text is never shown
  });

  it("shows only the heading and one quiet line when no model is running", () => {
    show({ prediction: off("student not loaded"), alert_policy: off("model error") });
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Prediction models");
    expect(screen.getByText("Prediction models: getting ready.")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
    expect(screen.queryByText(/Synthetic, not validated/)).toBeNull();
  });

  it("keeps the safety note when one model runs with no result", () => {
    show({ prediction: off("baseline forming"), alert_policy: off("policy not loaded") });
    expect(screen.getByText(/Synthetic, not validated\./)).toBeTruthy();
    expect(items()[1].textContent).toContain("getting ready");
  });

  it("shows the bin line only when both times are valid", () => {
    const { rerender } = show({ prediction: sig("flag"), alert_policy: sig("flag", { bin_end: null }) });
    const line = `From the 15 minutes ${formatClock(Date.parse(START))} to ${formatClock(Date.parse(END))}.`;
    expect(screen.getAllByText(line)).toHaveLength(1);
    rerender(<ResearchModels models={{ prediction: sig("flag", { bin_start: "nope" }), alert_policy: sig("flag", { bin_end: 5 }) }} />);
    expect(screen.queryByText(/From the 15 minutes/)).toBeNull();
    expect(document.body.textContent).not.toContain("Invalid");
  });

  it("renders nothing when models is absent", () => {
    const { container } = show(undefined);
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["null models", null],
    ["a string", "x"],
    ["missing entries", {}],
    ["null entries", { prediction: null, alert_policy: 3 }],
    ["an unknown state", { prediction: sig("maybe"), alert_policy: sig("flag", { state: 7 }) }],
    ["a not computed entry with no reason", { prediction: { state: "not_computed" }, alert_policy: { state: "not_computed", reason: 4 } }],
    ["a not computed entry with an empty reason", { prediction: { state: "not_computed", reason: "" }, alert_policy: null }],
  ])("says getting ready for %s", (_, models) => {
    show(models);
    expect(screen.getByText("Prediction models: getting ready.")).toBeTruthy();
    expect(screen.queryByText(/Not available|From the 15 minutes|Model record|Not computed/)).toBeNull();
  });

  it("gives the confidence sentence only to the model that has the check", () => {
    show({ prediction: sig("not_sure"), alert_policy: sig("not_sure") });
    expect(screen.getAllByText("Not sure", { exact: false })).not.toHaveLength(0);
    expect(screen.getAllByText(/its confidence check could not confirm a flag/)).toHaveLength(1);
  });

  it("holds no percentage and no risk wording in any state", () => {
    for (const state of ["flag", "no_flag", "not_sure"]) {
      show({ prediction: sig(state), alert_policy: sig(state) });
      expect(document.body.textContent).not.toMatch(BAD);
      cleanup();
    }
    show({ prediction: off("baseline forming"), alert_policy: off("no stored bins") });
    expect(document.body.textContent).not.toMatch(BAD);
  });
});
