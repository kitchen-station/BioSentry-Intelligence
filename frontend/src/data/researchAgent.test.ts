import { describe, expect, it } from "vitest";
import { ASK_STATUSES, PROPOSAL_STATES, RESEARCH_AGENT as A } from "./researchAgent";

const LAST = "Simulated patients. Not validated. A person decides.";
const finiteOrNull = (t: string | null) => t === null || Number.isFinite(Date.parse(t));
const numbersIn = (lines: readonly string[]) =>
  lines
    .slice(0, -1)
    .join(" ")
    .replace(/NEWS2|12-hour|seeds 1 to 10|per 100 patient-days/g, "")
    .match(/-?[0-9]+(?:\.[0-9]+)?/g) ?? [];

describe("research agent export", () => {
  it("has the exact key lists", () => {
    expect(Object.keys(A)).toEqual(["provenance", "questions", "proposals"]);
    expect(Object.keys(A.provenance)).toEqual(["source", "records", "exportedBy", "date"]);
    for (const q of A.questions) expect(Object.keys(q)).toEqual(["id", "askedAt", "question", "template", "status", "answer", "numbers"]);
    for (const p of A.proposals) expect(Object.keys(p)).toEqual(["id", "action", "step", "conclusion", "state", "approvedBy", "approvedAt"]);
  });

  it("has unique ids in increasing order, known states and finite dates", () => {
    const ids = A.questions.map((q) => q.id);
    expect(ids).toEqual([...ids].sort());
    expect(new Set(ids).size).toBe(ids.length);
    expect(Number.isFinite(Date.parse(A.provenance.date))).toBe(true);
    for (const q of A.questions) {
      expect(ASK_STATUSES).toContain(q.status);
      expect(["news2_threshold", "baseline_vs_news2", "alerts_per_nurse", "none"]).toContain(q.template);
      expect(finiteOrNull(q.askedAt)).toBe(true);
    }
    for (const p of A.proposals) {
      expect(PROPOSAL_STATES).toContain(p.state);
      expect(["set_baseline_params", "adopt_reward_policy"]).toContain(p.action);
      expect(finiteOrNull(p.approvedAt)).toBe(true);
    }
  });

  it("ends every answer with the fixed line and grounds every number", () => {
    for (const q of A.questions) {
      expect(q.answer.at(-1), q.id).toBe(LAST);
      if (q.status === "refused") {
        expect(q.numbers, q.id).toEqual([]);
        expect(q.answer, q.id).toHaveLength(2);
        expect(q.answer[0], q.id).toMatch(/^I can run three things on the simulated ward.*I did not run anything for this question\.$/);
        continue;
      }
      expect(q.answer[1], q.id).toMatch(/^Chance control:/);
      expect(numbersIn(q.answer), q.id).toEqual(q.numbers);
    }
  });

  it("pins the only sign-off on record", () => {
    expect(A.proposals.map((p) => [p.id, p.state, p.approvedBy])).toEqual([["P-0001", "applied", "Unattended demo run - not a person"]]);
  });
});
