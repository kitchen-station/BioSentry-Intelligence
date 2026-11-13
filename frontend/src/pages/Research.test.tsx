// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page must not touch the API: every export is a spy, and none may be called.
const api = vi.hoisted(() => ({ getStays: vi.fn(), getWard: vi.fn(), restoreSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", () => api);

import { CONFIDENCE_CHECK, LEARNED_RULES, MODEL_FACTS, RESEARCH_PROVENANCE, RESEARCH_RESULTS, RUN_COMPARISON } from "../data/researchResults";
import { Research } from "./Research";

afterEach(cleanup);

const show = (at = "/") =>
  render(
    <MemoryRouter initialEntries={[at]}>
      <Research session={{ clinician_id: "md-santos", ward: "W4", role: "md" }} onSignOut={() => {}} />
    </MemoryRouter>,
  );

const round3 = (x: number) => Number(x.toFixed(3));
const cell = (id: string) => RESEARCH_RESULTS.find((r) => r.id === id)!;

describe("research results data", () => {
  it("pins a subset of cells transcribed from the evaluation output", () => {
    expect(cell("news2_chart_trigger")).toMatchObject({
      episodes: { n: 54, d: 79 }, chance: { n: 2215, d: 4678 }, excess: 0.21, leadMedianMin: 47.2, perShift: 0.414,
    });
    expect(cell("baseline_alone")).toMatchObject({ episodes: { n: 22, d: 79 }, chance: { n: 150, d: 4678 }, excess: 0.246 });
    expect(cell("news2_or_baseline_no_temp")).toMatchObject({
      episodes: { n: 56, d: 79 }, chance: { n: 2263, d: 4678 }, excess: 0.225, leadMedianMin: 44.6,
    });
    expect(cell("baseline_alone_no_temp").leadMedianMin).toBe(-85.3);
  });

  it("pins alerts per shift and lead time with its n for every rule", () => {
    expect(RESEARCH_RESULTS.map((r) => [r.perShift, r.leadMedianMin, r.leadN])).toEqual([
      [0.414, 47.2, 33], [0.34, 45.9, 36], [0.042, -82.5, 24], [0.426, 39.6, 36], [0.032, -85.3, 23], [0.425, 44.6, 35],
    ]);
  });

  it("every displayed rate and excess equals its fraction rounded to 3 decimals", () => {
    for (const r of RESEARCH_RESULTS) {
      expect(r.episodes.rate, r.id).toBe(round3(r.episodes.n / r.episodes.d));
      expect(r.chance.rate, r.id).toBe(round3(r.chance.n / r.chance.d));
      expect(r.excess, r.id).toBe(round3(r.episodes.n / r.episodes.d - r.chance.n / r.chance.d));
    }
  });
});

describe("learned rules data", () => {
  it("pins every row as transcribed from the held-out files", () => {
    expect(LEARNED_RULES.map((r) => [r.name, r.perShift, r.timeInAlert, r.episodes.rate, r.chance.rate, r.excess, r.leadMedianMin])).toEqual([
      ["NEWS2 chart trigger (reference)", 0.414, 0.158, 0.684, 0.473, 0.21, 47.2],
      ["Own baseline alone (reference)", 0.042, 0.012, 0.278, 0.032, 0.246, -82.5],
      ["Teacher model (the larger network)", 0.391, 0.069, 0.709, 0.307, 0.401, 49.6],
      ["Student model", 0.413, 0.064, 0.734, 0.319, 0.415, 59.5],
      ["NEWS2 or student", 0.525, 0.185, 0.823, 0.58, 0.243, 66.0],
      ["Student model with the confidence check", 0.128, 0.026, 0.633, 0.089, 0.544, 20.5],
      ["NEWS2 or student with the confidence check", 0.438, 0.165, 0.759, 0.491, 0.268, 44.6],
      ["Reward-learned policy, generation 0", 0.429, 0.123, 0.785, 0.435, 0.35, 23.0],
      ["Reward-learned policy, final", 0.153, 0.035, 0.633, 0.14, 0.493, -3.8],
      ["NEWS2 or reward-learned policy, generation 0", 0.535, 0.248, 0.861, 0.666, 0.195, 67.9],
      ["NEWS2 or reward-learned policy, final", 0.463, 0.178, 0.772, 0.528, 0.244, 73.2],
      ["Student model without temperature", 0.409, 0.065, 0.747, 0.329, 0.418, 48.8],
      ["NEWS2 or student without temperature", 0.512, 0.183, 0.823, 0.577, 0.246, 66.0],
      ["Student model with the confidence check, without temperature", 0.127, 0.025, 0.633, 0.091, 0.542, 16.1],
      ["NEWS2 or student with the confidence check, without temperature", 0.433, 0.165, 0.759, 0.491, 0.268, 44.6],
      ["Reward-learned policy without temperature, final", 0.131, 0.021, 0.608, 0.091, 0.516, 20.2],
      ["NEWS2 or reward-learned policy without temperature, final", 0.451, 0.166, 0.759, 0.504, 0.256, 56.5],
    ]);
  });

  it("every row has counts, and its rates equal n/d rounded to 3 decimals", () => {
    for (const r of LEARNED_RULES) {
      expect(r.episodes.d, r.id).toBe(79);
      expect(r.chance.d, r.id).toBe(4678);
      expect(r.episodes.rate, r.id).toBe(round3(r.episodes.n! / r.episodes.d!));
      expect(r.chance.rate, r.id).toBe(round3(r.chance.n! / r.chance.d!));
    }
  });

  it("each reference row equals the matching row of the first table", () => {
    for (const [ref, id] of [["ref_news2_chart_trigger", "news2_chart_trigger"], ["ref_baseline_alone", "baseline_alone"]]) {
      const l = LEARNED_RULES.find((r) => r.id === ref)!;
      const r = cell(id);
      expect(l.perShift).toBe(r.perShift);
      expect(l.episodes).toEqual(r.episodes);
      expect(l.chance).toEqual(r.chance);
      expect(l.excess).toBe(r.excess);
      expect(l.leadMedianMin).toBe(r.leadMedianMin);
    }
  });

  it("excess matches the printed rates within rounding", () => {
    for (const r of LEARNED_RULES) expect(Math.abs(r.episodes.rate - r.chance.rate - r.excess), r.id).toBeLessThan(0.0015);
  });
});

describe("Research page", () => {
  it("shows one h1, the banner and the six rules in a captioned table", () => {
    show();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("note").map((n) => n.textContent)).toEqual([
      "Simulation lab: not the ward. Nothing here changes a patient's rank.",
      "Synthetic, not validated. These numbers come from a simulator, judged on the same simulator. They are not evidence about patients.",
    ]);
    expect(screen.getByRole("link", { name: "Ward" })).toBeTruthy(); // the bar link, present on every page
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText(/Alert rules on simulated episodes/)).toBeTruthy();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual([
      "NEWS2 chart trigger",
      "NEWS2 total 5 or more",
      "Own baseline alone",
      "NEWS2 or own baseline",
      "Own baseline alone, without temperature",
      "NEWS2 or own baseline, without temperature",
    ]);
    expect(rows[0].textContent).toContain("0.684 (54/79)");
    expect(rows[0].textContent).toContain("0.473 (2215/4678)");
    expect(rows[0].textContent).toContain("+0.210");
    expect(rows[2].textContent).toContain("0.278 (22/79)");
    expect(rows[2].textContent).toContain("-82.5 (n=24 of 91 evaluated)");
    expect(rows[5].textContent).toContain("0.484 (2263/4678)");
  });

  it("states the findings, the provenance and that NEWS2 is unchanged", () => {
    show();
    expect(screen.getByText(/That rise has been removed, and temperature noise went from 0\.02 to 0\.1 C\./)).toBeTruthy();
    expect(screen.getByText(/No gain beyond chance is shown\./)).toBeTruthy();
    expect(screen.getByText(/only 2 of 27 hypotensive episodes/)).toBeTruthy();
    expect(screen.getByText(/In 17 of 27, no cuff reading reached NEWS2 inside the window/)).toBeTruthy();
    expect(screen.getByText(/simulator commit f7647c5, scenario 95052d02/)).toBeTruthy();
    expect(screen.getByText(/Earlier run: ward-v1, simulator commit 3a15e01/)).toBeTruthy();
    expect(screen.getAllByRole("table")[0].querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\./);
    expect(screen.getByText(/Source: evaluation on 2026-11-13, simulator commit f7647c5, scenario 95052d02\./)).toBeTruthy();
    expect(screen.getByText(/Each model was scored on the held-out seeds once, by procedure\. The same seeds were used earlier for earlier models\. Student model \(run m1-20261113-3d634f3\): trained with MindSpore 2\.11\.0 on ward-v2-ext seeds 61 to 160, chosen on seeds 261 to 300\. Reward-learned policy \(run m2-20261113\): learned on ward-v2-ext seeds from 401, validation seeds 341 to 400\. Confidence check \(run m3-20261113-fbd39f2\): calibrated on ward-v2-ext seeds 301 to 340\./)).toBeTruthy();
    expect(screen.getByText(/NEWS2 itself is never changed/)).toBeTruthy();
    expect(screen.getByText(/k=4.0 p=8 were chosen on seeds 1 to 10/)).toBeTruthy();
  });

  it("shows the learned rules in a second captioned table", () => {
    show();
    const tables = screen.getAllByRole("table");
    expect(tables).toHaveLength(3);
    const table = tables[1];
    expect(table.querySelector("caption")!.textContent).toBe(
      "Learned rules. Synthetic simulator, not validated. The learned rules were trained on simulator trials and judged on other trials of the same simulator.",
    );
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(LEARNED_RULES.map((r) => r.name));
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0)).toEqual(["0.414", "0.158", "0.684 (54/79)", "0.473 (2215/4678)", "+0.210", "47.2"]);
    expect(cells(1)).toEqual(["0.042", "0.012", "0.278 (22/79)", "0.032 (150/4678)", "+0.246", "-82.5"]);
    expect(cells(3)).toEqual(["0.413", "0.064", "0.734 (58/79)", "0.319 (1493/4678)", "+0.415", "59.5"]);
    expect(cells(5)).toEqual(["0.128", "0.026", "0.633 (50/79)", "0.089 (415/4678)", "+0.544", "20.5"]);
    expect(cells(8)).toEqual(["0.153", "0.035", "0.633 (50/79)", "0.140 (656/4678)", "+0.493", "-3.8"]);
    expect(cells(16)).toEqual(["0.451", "0.166", "0.759 (60/79)", "0.504 (2357/4678)", "+0.256", "56.5"]);
    const first = within(tables[0]);
    expect(first.getAllByRole("columnheader")[2].textContent).toBe(within(table).getAllByRole("columnheader")[3].textContent);
    expect(first.getAllByRole("columnheader")[3].textContent).toBe(within(table).getAllByRole("columnheader")[4].textContent);
  });

  it("states the learned-rule findings and the run ids", () => {
    show();
    const text = document.body.textContent!.replace(/\s+/g, " ");
    expect(text).toContain("Added to NEWS2, the student alerts in 11 more of 91 episode windows at 1.27 times NEWS2's alerts. Most of that is what the extra alerts give by chance: the excess over chance is +0.243 against +0.210 for NEWS2 alone, and excess has no interval. No gain beyond chance is shown. With the confidence check it is 6 more of 91 at 1.06 times the alerts, excess +0.268. The reward-learned policy added to NEWS2 is 7 more of 91 at 1.12 times the alerts, excess +0.244.");
    const k = MODEL_FACTS.anyAllCounts;
    expect([k.news2OrStudent - k.news2, k.news2OrStudentChecked - k.news2, k.news2OrRewardFinal - k.news2]).toEqual([11, 6, 7]);
    expect(screen.getByText(/Excess\s+cannot be more than 1 minus the chance rate, so it does not rank these rules\./)).toBeTruthy();
    expect(screen.getByText(/The gains the own baseline showed on ward-v1 went away on ward-v2\. Two things changed \(the temperature rise was removed and temperature noise went from 0\.02 to 0\.1 C\), and this run cannot say which one caused it\. The table above shows both runs\./)).toBeTruthy();
    expect(screen.getByText(/a student model trained with MindSpore that exports as ONNX\. Each model was scored on the held-out seeds once, by procedure\. The same seeds were used earlier for earlier models\. Nothing was\s+tuned on the held-out trials\./)).toBeTruthy();
    expect(screen.queryByText(/not a bigger model/)).toBeNull();
    expect(screen.queryByText(/no usable threshold/)).toBeNull();
    expect(screen.getByText("None of these learned rules is used by the ward list, the rank or any alert.")).toBeTruthy();
    expect(screen.getByText("Time in alert: the share of patient time with an alert open.")).toBeTruthy();
    expect(screen.getByText(/it cannot be\s+more than 1 minus the chance rate\./)).toBeTruthy();
    expect(Object.keys(RESEARCH_PROVENANCE)).not.toContain("studentCommit");
  });

  it("says what the prediction model is, from the data file", () => {
    show();
    const text = document.body.textContent!.replace(/\s+/g, " ");
    expect(text).toContain("The prediction model is a small MindSpore network (one hidden layer, 1,185 parameters) fitted to the outputs of a larger one (79,873 parameters).");
    expect(text).toContain("On validation its log loss (0.018969) is not better than the same small network fitted on the true labels (0.018925), so the larger network bought nothing measurable.");
    expect([MODEL_FACTS.studentParameters, MODEL_FACTS.teacherParameters]).toEqual([1185, 79873]);
    expect(MODEL_FACTS.studentValLogLoss).toBeGreaterThan(MODEL_FACTS.hardLabelValLogLoss);
    expect(text).toContain("the student alerts about as often as NEWS2 (0.413 against 0.414 alerts per patient per 12 hours). It alerts in 58 of 79 episode windows against NEWS2's 54 of 79, and the intervals overlap. Its chance rate is lower (0.319 against 0.473) and so is its time in alert (0.064 against 0.158).");
    // The claims "intervals overlap" hold for the data.
    const I = MODEL_FACTS.anyAllIntervals;
    for (const x of [I.student, I.news2OrStudent, I.news2OrRewardFinal]) expect(x.lo <= I.news2.hi && I.news2.lo <= x.hi).toBe(true);
    expect(I.news2).toEqual({ rate: RUN_COMPARISON[0].v2.anyAll, lo: RUN_COMPARISON[0].v2.lo, hi: RUN_COMPARISON[0].v2.hi });
    const student = LEARNED_RULES.find((r) => r.id === "student")!;
    const news2 = LEARNED_RULES.find((r) => r.id === "ref_news2_chart_trigger")!;
    expect(Math.abs(student.perShift - news2.perShift)).toBeLessThan(0.01);
    expect(student.chance.rate).toBeLessThan(news2.chance.rate);
    expect(student.timeInAlert).toBeLessThan(news2.timeInAlert);
  });

  it("says what the reward-learned policy did, from the data file", () => {
    show();
    const text = document.body.textContent!.replace(/\s+/g, " ");
    expect(text).toContain("searched with 30 candidates per generation. A winner was kept only if it also beat the current policy on a second batch of new trials. 1 step was accepted, and the outer loop stopped after 2 rounds. Alone it is a quiet rule (0.153 alerts per patient per 12 hours), with a median lead time that is negative (-3.8 minutes).");
  });

  it("shows the confidence check: target, result, cost and its limits", () => {
    show();
    expect(CONFIDENCE_CHECK).toMatchObject({
      targetOneIn: 100, achievedPct: 0.65, achievedLoPct: 0.57, achievedHiPct: 0.74, verdict: "MET",
      notSureBins: 27523, studentAlertingBins: 46103, notSureShare: 0.597, checkedAlertingBins: 18580,
      studentNewAlerts: 6247, checkedNewAlerts: 1930, studentAnyAll: 61, lostAny: 8, lostEarly: 13, evaluated: 91,
    });
    // Not sure bins = student alerting bins minus checked ones; the share is rounded from the counts.
    expect(CONFIDENCE_CHECK.studentAlertingBins - CONFIDENCE_CHECK.checkedAlertingBins).toBe(CONFIDENCE_CHECK.notSureBins);
    expect(CONFIDENCE_CHECK.notSureShare).toBe(round3(CONFIDENCE_CHECK.notSureBins / CONFIDENCE_CHECK.studentAlertingBins));
    expect(CONFIDENCE_CHECK.achievedHiPct).toBeLessThan(100 / CONFIDENCE_CHECK.targetOneIn);
    const section = within(screen.getByRole("heading", { level: 2, name: "Confidence check on the student" }).closest("section")!);
    const text = section.getByText(/The check marks a student alert/).closest("section")!.textContent!.replace(/\s+/g, " ");
    expect(text).toContain('A "not sure" bin never alerts.');
    expect(text).toContain("The target was 1 false alert in 100 checks on patients who were not starting to deteriorate, on average over stays.");
    expect(text).toContain("the target was met (MET): the achieved share was 0.65% (95% interval 0.57% to 0.74%).");
    expect(text).toContain("27,523 of the student's 46,103 alerting bins (0.597) became not sure, leaving 18,580. New alerts fell from 6,247 to 1,930.");
    expect(text).toContain("8 of the 61 episodes the student was alerting in are no longer alerting in any bin, and 13 episodes with a new alert before fully in lose it.");
    expect(text).toContain("It does not bound the share of alerts that are true, and most alerts can still be false. It is a statement about simulator trials only. It promises nothing about patients.");
  });

  it("uses none of the banned claims", () => {
    show();
    const text = document.body.textContent!;
    expect(text).not.toMatch(/guarantee/i);
    for (const w of [/reinforcement learning/i, /self-learning/i, /predicts deterioration/i, /fewer false alarms than NEWS2/i]) expect(text).not.toMatch(w);
    expect(text).not.toMatch(/\b(is|are|was|were) validated\b/i);
  });

  it("compares ward-v1 and ward-v2 in a third captioned table", () => {
    show();
    const table = screen.getAllByRole("table")[2];
    expect(table.querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\. Each rule on ward-v1/);
    expect(within(table).getAllByRole("columnheader")[5].textContent).toBe("Excess over chance, 79 episodes not cut short (no interval), ward-v1");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(RUN_COMPARISON.map((r) => r.name));
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0)).toEqual(["0.413", "0.414", "0.648 (0.545 to 0.745)", "0.626 (0.522 to 0.727)", "+0.237", "+0.210"]);
    expect(cells(1)).toEqual(["0.189", "0.042", "0.769 (0.677 to 0.851)", "0.264 (0.174 to 0.360)", "+0.670", "+0.246"]);
    expect(rows).toHaveLength(3);
    expect(screen.getByText(/The student and the reward-learned policy were trained again after this change, so they are not compared across the two simulators here\./)).toBeTruthy();
  });

  it("comparison rows: ward-v2 equals the tables above, intervals contain their rate", () => {
    const cmp = (id: string) => RUN_COMPARISON.find((r) => r.id === id)!;
    expect(cmp("news2_chart_trigger").v2.excess).toBe(cell("news2_chart_trigger").excess);
    expect(cmp("baseline_alone").v2.excess).toBe(cell("baseline_alone").excess);
    expect(cmp("news2_or_baseline").v2.perShift).toBe(cell("news2_or_baseline").perShift);
    for (const r of RUN_COMPARISON) for (const s of [r.v1, r.v2]) expect(s.lo <= s.anyAll && s.anyAll <= s.hi, r.id).toBe(true);
  });

  it("has the research agent section under its own heading", () => {
    show();
    expect(screen.getByRole("heading", { level: 2, name: "Research agent: questions asked on the simulated ward" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("scrolls to the agent section when the address has its hash, and not without one", () => {
    // jsdom has no scrollIntoView.
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy;
    show();
    expect(spy).not.toHaveBeenCalled();
    cleanup();
    show("/lab/research#agent");
    expect(spy).toHaveBeenCalledTimes(1);
    expect((spy.mock.contexts[0] as Element).id).toBe("agent");
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });

  it("makes no API call", () => {
    show();
    for (const fn of Object.values(api)) expect(fn).not.toHaveBeenCalled();
  });
});
