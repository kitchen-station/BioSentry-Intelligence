// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page must not touch the API: every export is a spy, and none may be called.
const api = vi.hoisted(() => ({ getStays: vi.fn(), getWard: vi.fn(), restoreSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", () => api);

import { RESEARCH_RESULTS } from "../data/researchResults";
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

describe("Research page", () => {
  it("shows one h1, the banner and the six rules in a captioned table", () => {
    show();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("note").textContent).toBe(
      "Synthetic, not validated. These numbers come from a simulator, judged on the same simulator. They are not evidence about patients.",
    );
    const table = screen.getByRole("table");
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
    expect(screen.getByRole("table").querySelector("caption")!.textContent).toMatch(/^Synthetic simulator, not validated\./);
    expect(screen.getByText(/commit 46f9017, 2026-11-12/)).toBeTruthy();
    expect(screen.getByText(/NEWS2 itself is never changed/)).toBeTruthy();
    expect(screen.getByText(/k=3.0 p=4 were chosen on seeds 1 to 10/)).toBeTruthy();
  });

  it("makes no API call", () => {
    show();
    for (const fn of Object.values(api)) expect(fn).not.toHaveBeenCalled();
  });
});
