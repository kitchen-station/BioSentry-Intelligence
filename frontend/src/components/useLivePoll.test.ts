import { describe, expect, it, vi } from "vitest";
import type { SimStatus } from "./format";

const build = vi.hoisted(() => ({ usesServer: true }));
vi.mock("../api", () => ({
  getLiveWard: vi.fn(),
  get usesServer() {
    return build.usesServer;
  },
}));
import { pollEvery } from "./useLivePoll";

const sim = (speed: SimStatus["speed"], ended = false): SimStatus => ({
  clockS: 0,
  speed,
  ended,
  counts: { received: 0, rejected: 0, abstained: 0 },
});

describe("pollEvery", () => {
  it.each([
    ["no live view", null, false, 5_000],
    ["paused", sim(0), false, 5_000],
    ["1x", sim(1), false, 5_000],
    ["10x", sim(10), false, 5_000],
    ["60x", sim(60), false, 2_000],
    ["60x, run ended", sim(60, true), false, 5_000],
    ["60x, hidden tab", sim(60), true, 5_000],
    ["10x, hidden tab", sim(10), true, 5_000],
  ])("%s: %i ms", (_, s, hidden, ms) => {
    expect(pollEvery(s, hidden)).toBe(ms);
  });

  it("is 15 s in the demo build, whatever the speed", () => {
    build.usesServer = false;
    expect(pollEvery(sim(60), false)).toBe(15_000);
    build.usesServer = true;
  });
});
