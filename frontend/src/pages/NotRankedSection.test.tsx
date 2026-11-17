import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AbstainedRow } from "../types";
import { NotRankedSection } from "./NotRankedSection";

// The build's capability, flipped per test. The section reads it from the API module.
const build = vi.hoisted(() => ({ canRecordVitals: false }));
vi.mock("../api", () => ({
  get canRecordVitals() {
    return build.canRecordVitals;
  },
  recordVitals: vi.fn(),
}));

const row = (id: string, name: string): AbstainedRow => ({
  id,
  name,
  age: 70,
  sex: "M",
  bed: "4B",
  admitting_context: "Heart failure",
  reason: "Systolic BP missing",
  instruction: "Record a blood pressure",
  synthetic: true,
});
const rows = [row("p-004", "Flores, Ramon"), row("p-005", "de Leon, Isabel")];
const html = (list: readonly AbstainedRow[] = rows) =>
  renderToStaticMarkup(
    <MemoryRouter>
      <NotRankedSection rows={list} clinicianId="rn-delacruz" onRecorded={() => undefined} />
    </MemoryRouter>,
  );

beforeEach(() => {
  build.canRecordVitals = false;
});

describe("the NOT RANKED section", () => {
  it("lists every row with its reason and instruction", () => {
    const out = html();
    expect(out).toContain("Not ranked (2)");
    expect(out).toContain("Flores, Ramon");
    expect(out).toContain("de Leon, Isabel");
    expect(out).toContain("Record a blood pressure");
  });

  it("offers no entry in a fixture build: the one button folds the strip", () => {
    build.canRecordVitals = false;
    const out = html();
    expect(out).not.toContain("Record vitals");
    expect(out.match(/<button/g)).toHaveLength(1);
    expect(out).toMatch(/<button[^>]*aria-expanded="true"[^>]*>Hide<\/button>/);
  });

  it("says why the patients are not ranked, and that it is not low risk", () => {
    expect(html()).toContain("Action required to enable scoring. Not ranked does not mean low risk.");
  });

  it("offers one entry per row in an API build", () => {
    build.canRecordVitals = true;
    const out = html();
    expect(out.match(/Record vitals/g)).toHaveLength(rows.length);
    expect(out.match(/aria-expanded="false"/g)).toHaveLength(rows.length);
  });

  it("renders nothing when every patient is ranked", () => {
    build.canRecordVitals = true;
    expect(html([])).toBe("");
  });
});
