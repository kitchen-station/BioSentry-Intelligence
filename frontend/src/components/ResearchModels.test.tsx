// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { formatClock } from "./format";
import { ResearchModels } from "./ResearchModels";

afterEach(cleanup);

const START = "2026-09-03T06:30:00Z";
const END = "2026-09-03T06:45:00Z";
const prov = { model_id: "student-1", variant: "v2", file_sha256: "0123456789abcdef0123", run_id: "run-7" };
const sig = (state: string, extra: Record<string, unknown> = {}) => ({
  state, reason: null, bin_start: START, bin_end: END, provenance: [prov], ...extra,
});
const off = (reason: string) => ({ state: "not_computed", reason, bin_start: null, bin_end: null, provenance: [] });
const show = (models: unknown) => render(<ResearchModels models={models} />);
// "Prediction model" is a required name, so the word list holds "predicts", not "predict".
const BAD = /\d\s*%|probab|risk of|predicts|\bAI\b/i;
const items = () => screen.getAllByRole("listitem");

describe("ResearchModels", () => {
  it("shows the heading, the note and the two entries in order", () => {
    show({ prediction: sig("no_flag"), alert_policy: sig("flag") });
    const region = screen.getByRole("region", { name: "Research models" });
    expect(within(region).getByRole("heading", { level: 2 }).textContent).toBe("Research models");
    expect(within(region).getByText("Synthetic, not validated. A flag adds a reason line. It never changes the NEWS2 score or the order.")).toBeTruthy();
    expect(items()).toHaveLength(2);
    expect(items()[0].textContent).toContain("Prediction model · No flag");
    expect(items()[1].textContent).toContain("Alert policy (learned by reward) · Flagged");
  });

  it("words flag, no flag and not sure, and never calls not sure safe", () => {
    show({ prediction: sig("not_sure"), alert_policy: sig("no_flag") });
    expect(items()[0].textContent).toContain("Not sure");
    expect(screen.getByText("The score passed the threshold but is too low to support a flag.")).toBeTruthy();
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
    ["student not loaded", "the model file is not loaded"],
    ["calibration not loaded", "the model file is not loaded"],
    ["policy not loaded", "the model file is not loaded"],
    ["model error", "the model could not run"],
  ])("maps the reason %s", (reason, words) => {
    show({ prediction: off(reason), alert_policy: off(reason) });
    expect(items()[0].textContent).toContain(`Not computed: ${words}`);
    expect(screen.queryByText("Model record")).toBeNull();
  });

  it("shows an unmapped reason as plain text", () => {
    const { container } = show({ prediction: off("<b>x</b>"), alert_policy: off("model error") });
    expect(items()[0].textContent).toContain("Not computed: <b>x</b>");
    expect(container.querySelector("b")).toBeNull();
  });

  it("shows the bin line only when both times are valid", () => {
    const { rerender } = show({ prediction: sig("flag"), alert_policy: sig("flag", { bin_end: null }) });
    const line = `From the 15 minutes ${formatClock(Date.parse(START))} to ${formatClock(Date.parse(END))}.`;
    expect(screen.getAllByText(line)).toHaveLength(1);
    rerender(<ResearchModels models={{ prediction: sig("flag", { bin_start: "nope" }), alert_policy: sig("flag", { bin_end: 5 }) }} />);
    expect(screen.queryByText(/From the 15 minutes/)).toBeNull();
    expect(document.body.textContent).not.toContain("Invalid");
  });

  it("shows the model record only with provenance, cut to 12 characters", () => {
    show({ prediction: sig("flag"), alert_policy: sig("flag", { provenance: [] }) });
    expect(screen.getAllByText("Model record")).toHaveLength(1);
    expect(screen.getByText("Model: student-1 · Variant: v2 · File: 0123456789ab · Run: run-7")).toBeTruthy();
    expect(document.body.textContent).not.toContain("0123456789abc");
  });

  it("skips missing record fields and non-object items", () => {
    show({ prediction: sig("flag", { provenance: [null, { model_id: "m", variant: "", file_sha256: null }] }), alert_policy: sig("flag") });
    expect(screen.getByText("Model: m")).toBeTruthy();
  });

  it("keeps an open record open across a re-render", async () => {
    const make = () => ({ prediction: sig("flag"), alert_policy: sig("no_flag", { provenance: [] }) });
    const { rerender } = show(make());
    const details = document.querySelector("details")!;
    await userEvent.click(screen.getByText("Model record"));
    expect(details.open).toBe(true);
    rerender(<ResearchModels models={make()} />);
    expect(document.querySelector("details")).toBe(details);
    expect(details.open).toBe(true);
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
  ])("says not available for %s", (_, models) => {
    show(models);
    expect(screen.getAllByText(/Not available\./)).toHaveLength(2);
  });

  it("ignores a provenance that is not a list", () => {
    show({ prediction: sig("flag", { provenance: null }), alert_policy: sig("flag", { provenance: "x" }) });
    expect(screen.queryByText("Model record")).toBeNull();
    expect(items()[0].textContent).toContain("Flagged");
  });

  it("holds no percentage and no risk wording in any state", () => {
    for (const state of ["flag", "no_flag", "not_sure"]) {
      show({ prediction: sig(state), alert_policy: sig(state) });
      expect(document.body.textContent).not.toMatch(BAD);
      cleanup();
    }
    show({ prediction: off("model error"), alert_policy: off("no stored bins") });
    expect(document.body.textContent).not.toMatch(BAD);
  });
});
