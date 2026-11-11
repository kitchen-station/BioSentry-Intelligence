// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page must not touch the API: every export is a spy, and none may be called.
const api = vi.hoisted(() => ({ getStays: vi.fn(), getWard: vi.fn(), restoreSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", () => api);

import { LEARNED_RULES, RESEARCH_PROVENANCE, RESEARCH_RESULTS } from "../data/researchResults";
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
      episodes: { n: 56, d: 79 }, chance: { n: 2208, d: 4678 }, excess: 0.237, leadMedianMin: 45.9, perShift: 0.413,
    });
    expect(cell("baseline_alone")).toMatchObject({ episodes: { n: 67, d: 79 }, chance: { n: 831, d: 4678 }, excess: 0.67 });
    expect(cell("news2_or_baseline_no_temp")).toMatchObject({
      episodes: { n: 59, d: 79 }, chance: { n: 2495, d: 4678 }, excess: 0.213, leadMedianMin: 59.7,
    });
    expect(cell("baseline_alone_no_temp").leadMedianMin).toBe(-14.1);
  });

  it("pins alerts per shift and lead time with its n for every rule", () => {
    expect(RESEARCH_RESULTS.map((r) => [r.perShift, r.leadMedianMin, r.leadN])).toEqual([
      [0.413, 45.9, 34], [0.338, 34.6, 39], [0.189, -18.8, 64], [0.473, 20.5, 47], [0.139, -14.1, 44], [0.462, 59.7, 37],
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
      ["NEWS2 chart trigger (reference)", 0.413, 0.159, 0.709, 0.472, 0.237, 45.9],
      ["Own baseline alone (reference)", 0.189, 0.047, 0.848, 0.178, 0.67, -18.8],
      ["Student model", 0.226, 0.036, 0.696, 0.157, 0.539, 20.5],
      ["NEWS2 or student", 0.457, 0.173, 0.81, 0.509, 0.301, 44.6],
      ["Reward-learned policy, generation 0", 0.428, 0.127, 0.886, 0.436, 0.45, 23.0],
      ["Reward-learned policy, final", 0.179, 0.033, 0.835, 0.135, 0.7, 19.3],
      ["NEWS2 or reward-learned policy, generation 0", 0.533, 0.251, 0.937, 0.665, 0.271, 56.5],
      ["NEWS2 or reward-learned policy, final", 0.462, 0.177, 0.937, 0.526, 0.41, 33.8],
      ["Reward-learned policy without temperature, final", 0.061, 0.017, 0.519, 0.05, 0.469, -38.7],
      ["NEWS2 or reward-learned policy without temperature, final", 0.433, 0.166, 0.734, 0.491, 0.243, 58.1],
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
    expect(rows[0].textContent).toContain("0.709 (56/79)");
    expect(rows[0].textContent).toContain("0.472 (2208/4678)");
    expect(rows[0].textContent).toContain("+0.237");
    expect(rows[2].textContent).toContain("0.848 (67/79)");
    expect(rows[2].textContent).toContain("-18.8 (n=64)");
    expect(rows[5].textContent).toContain("0.533 (2495/4678)");
  });

  it("states the findings, the provenance and that NEWS2 is unchanged", () => {
    show();
    expect(screen.getByText(/Much of the own-baseline rule's gain depends on a fixed temperature rise/)).toBeTruthy();
    expect(screen.getByText(/gives no excess over NEWS2 alone \(the excess has no interval\)/)).toBeTruthy();
    expect(screen.getByText(/only 3 of 27 hypotensive episodes/)).toBeTruthy();
    expect(screen.getByText(/In 17 of 27, no cuff reading reached NEWS2 inside the window/)).toBeTruthy();
    expect(screen.getByText(/simulator commit 3a15e01, scenario 95052d02/)).toBeTruthy();
    expect(screen.getAllByRole("table")[0].querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\./);
    expect(screen.getByText(/commit 46f9017, 2026-11-12/)).toBeTruthy();
    expect(screen.getByText(/NEWS2 itself is never changed/)).toBeTruthy();
    expect(screen.getByText(/k=3.0 p=4 were chosen on seeds 1 to 10/)).toBeTruthy();
  });

  it("shows the learned rules in a second captioned table", () => {
    show();
    const tables = screen.getAllByRole("table");
    expect(tables).toHaveLength(2);
    const table = tables[1];
    expect(table.querySelector("caption")!.textContent).toBe(
      "Learned rules. Synthetic simulator, not validated. The learned rules were trained on simulator trials and judged on other trials of the same simulator.",
    );
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(LEARNED_RULES.map((r) => r.name));
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(0)).toEqual(["0.413", "0.159", "0.709 (56/79)", "0.472 (2208/4678)", "+0.237", "45.9"]);
    expect(cells(1)).toEqual(["0.189", "0.047", "0.848 (67/79)", "0.178 (831/4678)", "+0.670", "-18.8"]);
    expect(cells(2)).toEqual(["0.226", "0.036", "0.696", "0.157", "+0.539", "20.5"]);
    expect(cells(3)).toEqual(["0.457", "0.173", "0.810", "0.509", "+0.301", "44.6"]);
    expect(cells(4)).toEqual(["0.428", "0.127", "0.886", "0.436", "+0.450", "23.0"]);
    expect(cells(5)).toEqual(["0.179", "0.033", "0.835", "0.135", "+0.700", "19.3"]);
    expect(cells(6)).toEqual(["0.533", "0.251", "0.937", "0.665", "+0.271", "56.5"]);
    expect(cells(7)).toEqual(["0.462", "0.177", "0.937", "0.526", "+0.410", "33.8"]);
    expect(cells(8)).toEqual(["0.061", "0.017", "0.519", "0.050", "+0.469", "-38.7"]);
    expect(cells(9)).toEqual(["0.433", "0.166", "0.734", "0.491", "+0.243", "58.1"]);
    const first = within(tables[0]);
    expect(first.getAllByRole("columnheader")[2].textContent).toBe(within(table).getAllByRole("columnheader")[3].textContent);
    expect(first.getAllByRole("columnheader")[3].textContent).toBe(within(table).getAllByRole("columnheader")[4].textContent);
    expect(screen.getByText(
      "The student model without temperature, alone or with NEWS2, has no usable threshold: its alert stays open about half the time, so it is not a working rule.",
    )).toBeTruthy();
  });

  it("states the learned-rule findings and their commits", () => {
    show();
    expect(screen.getByText(/With temperature, the small student model has more excess over chance than NEWS2 alone \(\+0\.539\s+against \+0\.237\)/)).toBeTruthy();
    expect(screen.getByText(/own-baseline rule does better than the student; the student is ahead\s+only on lead time\./)).toBeTruthy();
    expect(screen.getByText(/did not beat the untrained own-baseline rule\. Its final numbers are within\s+noise of that rule/)).toBeTruthy();
    expect(screen.getByText(/rewritten as a weighted sum, the starting\s+point of learning; it alerts more than twice as often, so most of the later fall in alerts undoes\s+that translation\. The path between generations is mostly noise\./)).toBeTruthy();
    expect(screen.getByText("Without temperature, no learned rule adds anything measurable to NEWS2 on this simulator.")).toBeTruthy();
    expect(screen.getByText(/Every gain so far depends on a fixed temperature rise the simulator adds to every episode, a value\s+with no cited source\./)).toBeTruthy();
    expect(screen.queryByText(/not a bigger model/)).toBeNull();
    expect(screen.getByText(/What exists and works: features with no look-ahead, clean splits between training and held-out\s+trials, and a student model that exports as ONNX, the form Huawei's converter reads \(conversion\s+not yet done\)\. The no-temperature runs show how much of each gain depends on the temperature\s+artefact\./)).toBeTruthy();
    expect(screen.getByText("None of these learned rules is used by the ward list, the rank or any alert.")).toBeTruthy();
    expect(screen.getByText("Time in alert: the share of patient time with an alert open.")).toBeTruthy();
    expect(RESEARCH_PROVENANCE.studentCommit).toBe("5641d92");
    expect(RESEARCH_PROVENANCE.rewardCommit).toBe("cce584b");
    expect(screen.getByText(/Student model numbers: commit 5641d92\. Reward-learned policy numbers: commit cce584b\./)).toBeTruthy();
  });

  it("makes no API call", () => {
    show();
    for (const fn of Object.values(api)) expect(fn).not.toHaveBeenCalled();
  });
});
