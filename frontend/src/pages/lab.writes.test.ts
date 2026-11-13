// The lab may read the ward and may not write it. This reads the source, so it covers every code
// path, not only the ones a render reaches.
import { describe, expect, it } from "vitest";

const sources = import.meta.glob(
  ["./Lab.tsx", "./Research.tsx", "./Pipeline.tsx", "./ResearchAgentSection.tsx", "../components/LabFrame.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

describe("lab modules", () => {
  it("find all five sources", () => {
    expect(Object.keys(sources)).toHaveLength(5);
  });

  it.each(Object.entries(sources))("%s imports only getWard from the api", (_, src) => {
    const named = [...src.matchAll(/import\s*\{([^}]*)\}\s*from\s*"(?:\.\.?\/)+api"/g)]
      .flatMap((m) => m[1].split(",").map((n) => n.trim()).filter(Boolean));
    expect(named.every((n) => n === "getWard")).toBe(true);
    expect(src).not.toMatch(/api\/client|api\/providers/);
    expect(src).not.toMatch(/import\s+(?:\*|\w+)[^;]*from\s*"(?:\.\.?\/)+api"/);
  });
});
