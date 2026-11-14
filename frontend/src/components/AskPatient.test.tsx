// @vitest-environment jsdom
// The panel is the shared thread (see Thread.test.tsx) with a heading and, for a physician or charge nurse, a link to the Ask page.
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionRole } from "../types";
import { AskPatient } from "./AskPatient";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ askPatient: vi.fn(), getPatientQuestions: vi.fn(), getPatientQuestion: vi.fn(), ApiError: class extends Error {} }));
vi.mock("../api", () => api);

const panel = async (role: SessionRole) => {
  api.getPatientQuestions.mockResolvedValue([
    { id: 1, requested_by: "rn-manalo", requested_at: "2026-11-13T09:30:00Z", question: "Why is the rate up?", state: "queued", reason: null, claimed_at: null, finished_at: null },
  ]);
  render(
    <MemoryRouter>
      <AskPatient patientId="p-001" clinicianId="rn-manalo" role={role} onSignOut={() => {}} />
    </MemoryRouter>,
  );
  await act(async () => {});
};
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the panel", () => {
  it.each(["md", "charge_rn"] as const)("shows the thread of this patient and a link to Ask for %s", async (role) => {
    await panel(role);
    expect(screen.getByRole("heading", { level: 2, name: "Ask about this patient" })).toBeTruthy();
    expect(api.getPatientQuestions).toHaveBeenCalledWith("p-001");
    expect(screen.getByText("Why is the rate up?")).toBeTruthy();
    expect(screen.getByText("Research agent")).toBeTruthy();
    expect(screen.getByText("The agent sees this patient's data only, with no name. Do not type a name in your question.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open in Ask" }).getAttribute("href")).toBe("/ask/patient/p-001");
  });

  it("keeps the thread, with no link, for a staff nurse", async () => {
    await panel("rn");
    expect(screen.getByText("Why is the rate up?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
