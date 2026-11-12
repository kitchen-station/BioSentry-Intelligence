// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page must not touch the API: every export is a spy, and none may be called.
const api = vi.hoisted(() => ({ getStays: vi.fn(), getWard: vi.fn(), restoreSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", () => api);

import { LEARNED_RULES, RESEARCH_PROVENANCE, RESEARCH_RESULTS, RUN_COMPARISON } from "../data/researchResults";
import { Research } from "./Research";

afterEach(cleanup);

const show = () =>
  render(
    <MemoryRouter>
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
  it("pins every row as transcribed from the cards", () => {
    expect(LEARNED_RULES.map((r) => [r.name, r.perShift, r.timeInAlert, r.episodes.rate, r.chance.rate, r.excess, r.leadMedianMin])).toEqual([
      ["NEWS2 chart trigger (reference)", 0.414, 0.158, 0.684, 0.473, 0.21, 47.2],
      ["Own baseline alone (reference)", 0.042, 0.012, 0.278, 0.032, 0.246, -82.5],
      ["Student model", 0.088, 0.019, 0.367, 0.058, 0.309, 5.2],
      ["NEWS2 or student", 0.429, 0.165, 0.709, 0.486, 0.223, 47.2],
      ["Reward-learned policy, generation 0", 0.429, 0.123, 0.785, 0.435, 0.35, 23.0],
      ["Reward-learned policy, final", 0.15, 0.029, 0.646, 0.12, 0.525, -11.2],
      ["NEWS2 or reward-learned policy, generation 0", 0.535, 0.248, 0.861, 0.666, 0.195, 67.9],
      ["NEWS2 or reward-learned policy, final", 0.465, 0.173, 0.772, 0.516, 0.256, 61.5],
      ["Reward-learned policy without temperature, final", 0.061, 0.017, 0.519, 0.05, 0.469, -38.7],
      ["NEWS2 or reward-learned policy without temperature, final", 0.434, 0.166, 0.722, 0.492, 0.229, 60.9],
    ]);
  });

  it("counts exist only for the reference rows; their rate = n/d rounded to 3 decimals", () => {
    for (const r of LEARNED_RULES) {
      const ref = r.id.startsWith("ref_");
      expect(r.episodes.n !== undefined, r.id).toBe(ref);
      expect(r.chance.n !== undefined, r.id).toBe(ref);
      if (ref) {
        expect(r.episodes.rate, r.id).toBe(round3(r.episodes.n! / r.episodes.d!));
        expect(r.chance.rate, r.id).toBe(round3(r.chance.n! / r.chance.d!));
      }
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
    expect(screen.getByRole("note").textContent).toBe(
      "Synthetic, not validated. These numbers come from a simulator, judged on the same simulator. They are not evidence about patients.",
    );
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
    expect(rows[2].textContent).toContain("-82.5 (n=24)");
    expect(rows[5].textContent).toContain("0.484 (2263/4678)");
  });

  it("states the findings, the provenance and that NEWS2 is unchanged", () => {
    show();
    expect(screen.getByText(/That rise has been removed\. These numbers come from trials made again without it \(ward-v2\)/)).toBeTruthy();
    expect(screen.getByText(/Without the rise, no rule here adds anything measurable to NEWS2\./)).toBeTruthy();
    expect(screen.getByText(/only 2 of 27 hypotensive episodes/)).toBeTruthy();
    expect(screen.getByText(/In 17 of 27, no cuff reading reached NEWS2 inside the window/)).toBeTruthy();
    expect(screen.getByText(/simulator commit f7647c5, scenario 95052d02/)).toBeTruthy();
    expect(screen.getByText(/Earlier run: ward-v1, simulator commit 3a15e01/)).toBeTruthy();
    expect(screen.getAllByRole("table")[0].querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\./);
    expect(screen.getByText(/commit a9a7c36, 2026-11-12/)).toBeTruthy();
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
    expect(cells(2)).toEqual(["0.088", "0.019", "0.367", "0.058", "+0.309", "5.2"]);
    expect(cells(3)).toEqual(["0.429", "0.165", "0.709", "0.486", "+0.223", "47.2"]);
    expect(cells(4)).toEqual(["0.429", "0.123", "0.785", "0.435", "+0.350", "23.0"]);
    expect(cells(5)).toEqual(["0.150", "0.029", "0.646", "0.120", "+0.525", "-11.2"]);
    expect(cells(6)).toEqual(["0.535", "0.248", "0.861", "0.666", "+0.195", "67.9"]);
    expect(cells(7)).toEqual(["0.465", "0.173", "0.772", "0.516", "+0.256", "61.5"]);
    expect(cells(8)).toEqual(["0.061", "0.017", "0.519", "0.050", "+0.469", "-38.7"]);
    expect(cells(9)).toEqual(["0.434", "0.166", "0.722", "0.492", "+0.229", "60.9"]);
    const first = within(tables[0]);
    expect(first.getAllByRole("columnheader")[2].textContent).toBe(within(table).getAllByRole("columnheader")[3].textContent);
    expect(first.getAllByRole("columnheader")[3].textContent).toBe(within(table).getAllByRole("columnheader")[4].textContent);
    expect(screen.getByText(
      "The student model without temperature, alone or with NEWS2, has no usable threshold: its alert stays open about 80% of the time, so it is not a working rule.",
    )).toBeTruthy();
  });

  it("states the learned-rule findings and their commits", () => {
    show();
    expect(screen.getByText(/Added to NEWS2, the own baseline,\s+the student and the reward-learned policy each catch a few more episodes, and every interval overlaps\s+NEWS2's\./)).toBeTruthy();
    expect(screen.getByText(/Alone, the own baseline and the student catch fewer episodes than NEWS2, with far fewer alerts\./)).toBeTruthy();
    expect(screen.getByText(/reward-learned policy alone catches about as many as NEWS2 with about a third of the alerts, but\s+later: its median lead time is negative\./)).toBeTruthy();
    expect(screen.getByText(/Excess\s+cannot be more than 1 minus the chance rate, so it does not rank these rules\./)).toBeTruthy();
    expect(screen.getByText("The gains seen before the fix (ward-v1) came from the temperature rise. The table above shows both runs.")).toBeTruthy();
    expect(screen.getByText(/What exists and works: features with no look-ahead, clean splits between training and held-out\s+trials, and a student model that exports as ONNX, the form Huawei's converter reads \(conversion\s+not yet done\)\./)).toBeTruthy();
    expect(screen.getByText(/nothing was tuned\s+to rescue a result\./)).toBeTruthy();
    expect(screen.queryByText(/not a bigger model/)).toBeNull();
    expect(screen.getByText("None of these learned rules is used by the ward list, the rank or any alert.")).toBeTruthy();
    expect(screen.getByText("Time in alert: the share of patient time with an alert open.")).toBeTruthy();
    expect(screen.getByText(/it cannot be\s+more than 1 minus the chance rate\./)).toBeTruthy();
    expect(RESEARCH_PROVENANCE.studentCommit).toBe("a9a7c36");
    expect(RESEARCH_PROVENANCE.rewardCommit).toBe("a9a7c36");
    expect(screen.getByText(/Student model numbers: commit a9a7c36\. Reward-learned policy numbers: commit a9a7c36\./)).toBeTruthy();
  });

  it("compares ward-v1 and ward-v2 in a third captioned table", () => {
    show();
    const table = screen.getAllByRole("table")[2];
    expect(table.querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\. Each rule on ward-v1/);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(RUN_COMPARISON.map((r) => r.name));
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0)).toEqual(["0.413", "0.414", "0.648 (0.545 to 0.745)", "0.626 (0.522 to 0.727)", "+0.237", "+0.210"]);
    expect(cells(3)).toEqual(["0.226", "0.088", "0.637 (0.527 to 0.731)", "0.352 (0.250 to 0.452)", "+0.539", "+0.309"]);
    expect(cells(5)).toEqual(["0.179", "0.150", "0.769 (0.676 to 0.852)", "0.582 (0.464 to 0.678)", "+0.700", "+0.525"]);
  });

  it("comparison rows: ward-v2 equals the tables above, intervals contain their rate", () => {
    const cmp = (id: string) => RUN_COMPARISON.find((r) => r.id === id)!;
    expect(cmp("news2_chart_trigger").v2.excess).toBe(cell("news2_chart_trigger").excess);
    expect(cmp("baseline_alone").v2.excess).toBe(cell("baseline_alone").excess);
    expect(cmp("student").v2.excess).toBe(LEARNED_RULES.find((r) => r.id === "student")!.excess);
    expect(cmp("reward_final").v2.perShift).toBe(LEARNED_RULES.find((r) => r.id === "reward_final")!.perShift);
    for (const r of RUN_COMPARISON) for (const s of [r.v1, r.v2]) expect(s.lo <= s.anyAll && s.anyAll <= s.hi, r.id).toBe(true);
  });

  it("makes no API call", () => {
    show();
    for (const fn of Object.values(api)) expect(fn).not.toHaveBeenCalled();
  });
});
