// The lab may read the ward and may not write it. This reads the source and follows imports from
// every lab page, so it covers every code path, not only the ones a render reaches.
import { describe, expect, it } from "vitest";

const all = import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const sources = Object.fromEntries(Object.entries(all).filter(([p]) => !/\.test\.tsx?$/.test(p)));

const SPEC = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)(["'])([^"']+)\1/g;
const API = /["'](?:\.\.?\/)+api(?:\/[^"']*)?["']/;
const RELATIVE = /^\.\.?\//;

const dirOf = (p: string) => p.slice(0, p.lastIndexOf("/"));
function resolve(from: string, spec: string): string | undefined {
  const parts = dirOf(from).split("/");
  for (const s of spec.split("/")) {
    if (s === "..") parts.pop();
    else if (s !== ".") parts.push(s);
  }
  const base = parts.join("/");
  return [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`].find((c) => c in sources);
}

function reached(roots: string[]): string[] {
  const seen = new Set<string>();
  const todo = [...roots];
  for (let p = todo.pop(); p; p = todo.pop()) {
    if (seen.has(p)) continue;
    seen.add(p);
    for (const m of sources[p].matchAll(SPEC)) {
      const next = RELATIVE.test(m[2]) ? resolve(p, m[2]) : undefined;
      if (next) todo.push(next);
    }
  }
  return [...seen];
}

// The statements in a module that name the api folder, whole, up to the end of the statement.
const apiStatements = (src: string) =>
  src.split(/;\s*\n|\n(?=import|export)/).filter((s) => API.test(s)).map((s) => s.replace(/\s+/g, " ").replace(/;$/, "").trim());

const roots = [
  ...Object.keys(sources).filter((p) => p.startsWith("./") && sources[p].includes("<LabFrame")),
  "../components/LabFrame.tsx",
];

describe("lab modules", () => {
  it("finds the lab pages and the frame", () => {
    expect(roots.length).toBeGreaterThanOrEqual(4);
    expect(roots).toContain("./Pipeline.tsx");
  });

  it("reach the api only through getWard in Pipeline", () => {
    const users = Object.fromEntries(
      reached(roots).map((p) => [p, apiStatements(sources[p])] as const).filter(([, s]) => s.length > 0),
    );
    // SessionMenu imports nothing from the api today. Its clearSession would only drop the local token and send no request.
    expect(users).toEqual({ "./Pipeline.tsx": ['import { getWard } from "../api"'] });
  });
});

describe("the api matcher", () => {
  it.each([
    'import { getWard as g } from "../api";',
    'import * as api from "../api";',
    'const m = await import("../api");',
    'import { getWard } from "../api/index";',
    "import { getWard } from '../api';",
    'export { addNote } from "../api";',
  ])("catches %s", (line) => {
    expect(apiStatements(line)).toHaveLength(1);
  });
});
