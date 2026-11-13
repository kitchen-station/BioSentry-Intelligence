import { PIPELINE, type Stage } from "../pipeline.ts";

const LINKS: Record<string, "models" | "research-agent" | null> = {
  news2: "models", baseline: "models", prediction: "models", reward: "models", confidence: "models",
  agent: "research-agent", quality: null, arbiter: null,
};

const fail = (msg: string): never => {
  throw new Error(msg);
};

/** Keys sorted at every level, indent 1, one trailing newline: the contract's write rule. */
const sortKeys = (x: unknown): unknown =>
  Array.isArray(x)
    ? x.map(sortKeys)
    : x && typeof x === "object"
      ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, sortKeys((x as Record<string, unknown>)[k])]))
      : x;

export const serialize = (obj: unknown): string => JSON.stringify(sortKeys(obj), null, 1) + "\n";

// The contract exempts the tokens NEWS2 and SpO2 in any letter case: the stage id "news2" is lower case.
const noDigit = (s: string) => !/[0-9]/.test(s.replace(/news2|spo2/gi, ""));

// The claims no exported file may hold (the contract's "Words a file must not hold").
const BANNED = /predicts deterioration|reinforcement learning|self-learning|guarantee|fewer false alarms/i;
const strings = (x: unknown): string[] =>
  typeof x === "string" ? [x] : Array.isArray(x) ? x.flatMap(strings) : x && typeof x === "object" ? Object.values(x).flatMap(strings) : [];
const realDate = (d: string) => {
  const t = new Date(`${d}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) && !Number.isNaN(t.getTime()) && t.toISOString().slice(0, 10) === d;
};

/** The whole envelope of pipeline.json, built from the stage list. Throws on any contract violation. */
export function pipelineFile({ date, commit, note }: { date: string; commit: string; note?: string }, stages: readonly Stage[] = PIPELINE): object {
  if (!realDate(date)) fail("date must be a real day, YYYY-MM-DD");
  if (note !== undefined && (note.length < 1 || note.length > 300)) fail("a note is 1 to 300 characters");
  if (!/^[0-9a-f]{40}$/.test(commit)) fail("commit must be 40 lowercase hex characters");
  if (stages.length > 12) fail("at most 12 stages");
  const body = {
    stages: stages.map((s) => {
      if (!(s.id in LINKS)) fail(`no links_to for stage ${s.id}`);
      for (const t of [s.in, s.out]) if (t.length < 1 || t.length > 80) fail(`in and out must be 1 to 80 characters: ${s.id}`);
      return { id: s.id, name: s.name, state: s.state, in: s.in, out: s.out, links_to: LINKS[s.id] };
    }),
  };
  const bad = (x: unknown): boolean =>
    typeof x === "string" ? !noDigit(x)
      : Array.isArray(x) ? x.some(bad)
        : x && typeof x === "object" ? Object.entries(x).some(([k, v]) => !noDigit(k) || bad(v))
          : false;
  if (bad(body)) fail("a key or string in body holds a digit");
  const doc = {
    body,
    format: "biosentry-evaluation/3",
    notes: note === undefined ? [] : [note],
    provenance: {
      commit,
      data: "stage names, states and what goes in and out",
      date,
      exporter: { name: "npm run export:pipeline", version: "1" },
      repo: "kitchen-station/BioSentry-Intelligence",
      seeds: [],
      // The commit is always written out, also when the command took it from git: this line gives the same bytes again.
      source: [`npm run export:pipeline -- --date ${date} --commit ${commit}${note === undefined ? "" : ` --note ${JSON.stringify(note)}`}`],
      synthetic: true,
      validated: false,
    },
    section: "pipeline",
  };
  if (strings(doc).some((s) => BANNED.test(s))) fail("a string holds a claim the contract forbids");
  return doc;
}
