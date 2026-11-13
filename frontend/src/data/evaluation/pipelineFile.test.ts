import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PIPELINE, STAGE_STATES, type Stage } from "../pipeline";
import { pipelineFile, serialize } from "./pipelineFile";

const RAW = readFileSync(new URL("./pipeline.json", import.meta.url), "utf8");
const FILE = JSON.parse(RAW);
const OK = { date: "2026-11-13", commit: "a".repeat(40) };
const stage = (o: Partial<Stage> = {}): Stage => ({ ...PIPELINE[0], ...o });
const strings = (x: unknown): string[] =>
  typeof x === "string" ? [x] : Array.isArray(x) ? x.flatMap(strings) : x && typeof x === "object" ? Object.entries(x).flatMap(([k, v]) => [k, ...strings(v)]) : [];

describe("pipeline.json", () => {
  it("equals a fresh export with its own date and commit, byte for byte", () => {
    const { date, commit } = FILE.provenance;
    expect(FILE.notes.length).toBeLessThanOrEqual(1);
    expect(serialize(pipelineFile({ date, commit, note: FILE.notes[0] }))).toBe(RAW);
  });

  it("has the envelope and one entry per stage, in order, with six keys", () => {
    expect(FILE.format).toBe("biosentry-evaluation/3");
    expect(FILE.section).toBe("pipeline");
    expect(FILE.provenance.synthetic).toBe(true);
    expect(FILE.provenance.validated).toBe(false);
    expect(FILE.body.stages.map((s: { id: string }) => s.id)).toEqual(PIPELINE.map((s) => s.id));
    const links: Record<string, string | null> = { news2: "models", baseline: "models", prediction: "models", reward: "models", confidence: "models", agent: "research-agent", quality: null, arbiter: null };
    for (const s of FILE.body.stages) {
      expect(Object.keys(s).sort()).toEqual(["id", "in", "links_to", "name", "out", "state"]);
      expect(STAGE_STATES).toContain(s.state);
      expect(s.links_to).toBe(links[s.id]);
    }
  });

  it("holds no digit in body except in NEWS2 and SpO2", () => {
    for (const s of strings(FILE.body)) expect(s.replace(/news2/gi, "").replaceAll("SpO2", "")).not.toMatch(/[0-9]/);
  });

  it("holds none of the forbidden words", () => {
    for (const s of strings(FILE)) expect(s).not.toMatch(/predicts deterioration|reinforcement learning|self-learning|guarantee|fewer false alarms/i);
  });

  it("sorts keys at every level and ends with one newline", () => {
    expect(RAW.endsWith("}\n")).toBe(true);
    expect(serialize({ b: [{ z: 1, a: 2 }], a: null })).toBe('{\n "a": null,\n "b": [\n  {\n   "a": 2,\n   "z": 1\n  }\n ]\n}\n');
  });
});

describe("pipelineFile refuses", () => {
  it("a digit in in or in a name", () => {
    expect(() => pipelineFile(OK, [stage({ in: "Seven or 7 things" })])).toThrow("digit");
    expect(() => pipelineFile(OK, [stage({ name: "Stage 2" })])).toThrow("digit");
  });
  it("an in or out that is empty or longer than 80 characters", () => {
    expect(() => pipelineFile(OK, [stage({ in: "" })])).toThrow("1 to 80");
    expect(() => pipelineFile(OK, [stage({ out: "x".repeat(81) })])).toThrow("1 to 80");
  });
  it("more than 12 stages", () => {
    expect(() => pipelineFile(OK, Array.from({ length: 13 }, () => stage()))).toThrow("12");
  });
  it("an unknown stage id", () => {
    expect(() => pipelineFile(OK, [stage({ id: "other" })])).toThrow("other");
  });
  it("a claim the contract forbids, anywhere in the file", () => {
    expect(() => pipelineFile(OK, [stage({ out: "A guarantee of coverage" })])).toThrow("forbids");
    expect(() => pipelineFile({ ...OK, note: "Fewer false alarms than before" })).toThrow("forbids");
  });
  it("a note that is empty or longer than 300 characters, and keeps a good one", () => {
    expect(() => pipelineFile({ ...OK, note: "" })).toThrow("300");
    expect(() => pipelineFile({ ...OK, note: "x".repeat(301) })).toThrow("300");
    expect(pipelineFile({ ...OK, note: "One note." })).toMatchObject({ notes: ["One note."] });
    expect(pipelineFile(OK)).toMatchObject({ notes: [] });
  });
  it("a bad date or commit", () => {
    expect(() => pipelineFile({ ...OK, date: "13-11-2026" })).toThrow("date");
    expect(() => pipelineFile({ ...OK, date: "2026-02-30" })).toThrow("date");
    expect(() => pipelineFile({ ...OK, date: "2026-13-45" })).toThrow("date");
    expect(() => pipelineFile({ ...OK, commit: "abc" })).toThrow("commit");
  });
});
