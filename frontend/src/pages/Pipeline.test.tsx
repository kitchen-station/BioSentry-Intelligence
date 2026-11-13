// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getWard: vi.fn(), restoreSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", () => api);

import { PIPELINE, STAGE_STATES } from "../data/pipeline";
import { Pipeline } from "./Pipeline";

afterEach(() => {
  cleanup();
  api.getWard.mockReset();
});

const show = (ward: Promise<unknown>) => {
  api.getWard.mockReturnValue(ward);
  render(
    <MemoryRouter>
      <Pipeline session={{ clinician_id: "md-santos", ward: "W4", role: "md" }} onSignOut={() => {}} />
    </MemoryRouter>,
  );
};
const states = () => screen.getAllByRole("listitem").map((li) => within(li).getByText(/^(live in|shown,|research only|planned)/).textContent);
const OWN = PIPELINE.map((s) => s.state);

describe("pipeline data", () => {
  it("has eight stages, each with a known state and one or two sentences", () => {
    expect(PIPELINE).toHaveLength(8);
    for (const s of PIPELINE) {
      expect(STAGE_STATES).toContain(s.state);
      expect([1, 2]).toContain(s.text.length);
    }
  });

  it("has exactly one stage that decides", () => {
    expect(PIPELINE.filter((s) => s.decides)).toHaveLength(1);
  });
});

describe("pipeline page", () => {
  it("lists the eight stages in order with their own states while loading", () => {
    show(new Promise(() => {}));
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(
      PIPELINE.map((s, i) => `${i + 1}${s.name}`),
    );
    expect(states()).toEqual(OWN);
    expect(screen.getByText("Reading the ward's tier status.")).toBeTruthy();
    expect(screen.getByText(/Synthetic, not validated/)).toBeTruthy();
    expect(screen.getByText("Decides what reaches the nurse")).toBeTruthy();
  });

  it("takes the NEWS2 state from the ward when tier A is live", async () => {
    show(Promise.resolve({ tier_status: [{ tier: "A", label: "NEWS2", live: true }] }));
    expect(await screen.findByText(/NEWS2 state from this ward's tier status/)).toBeTruthy();
    expect(states()).toEqual(OWN);
  });

  it("shows tier A as not changing the ranking when the ward says it is not live", async () => {
    show(Promise.resolve({ tier_status: [{ tier: "A", label: "NEWS2", live: false }] }));
    await screen.findByText(/NEWS2 state from this ward's tier status/);
    expect(states()[0]).toBe("shown, does not change the ranking");
    expect(states().slice(1)).toEqual(OWN.slice(1));
  });

  it("uses the page's own state when the ward has no tier A entry", async () => {
    show(Promise.resolve({ tier_status: [] }));
    await screen.findByText(/NEWS2 state from this ward's tier status/);
    expect(states()).toEqual(OWN);
  });

  it("says so and keeps its own states when the ward read fails", async () => {
    show(Promise.reject(new Error("down")));
    await waitFor(() => expect(screen.getByText(/Ward read failed/)).toBeTruthy());
    expect(states()).toEqual(OWN);
  });

  it("ignores a ward answer that arrives after the page is gone", async () => {
    let resolve!: (v: unknown) => void;
    show(new Promise((r) => (resolve = r)));
    cleanup();
    resolve({ tier_status: [] });
    await Promise.resolve();
  });

  it("ignores a ward failure that arrives after the page is gone", async () => {
    let reject!: (e: Error) => void;
    show(new Promise((_, r) => (reject = r)));
    cleanup();
    reject(new Error("late"));
    await Promise.resolve();
  });
});
