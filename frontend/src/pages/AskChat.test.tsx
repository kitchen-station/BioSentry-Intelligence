// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuestionItem } from "../types";
import { AskChat } from "./AskChat";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return {
    askLab: vi.fn(), getLabQuestions: vi.fn(), getLabQuestion: vi.fn(),
    askPatient: vi.fn(), getPatientQuestions: vi.fn(), getPatientQuestion: vi.fn(), getWard: vi.fn(),
    hasLabQuestions: true, hasPatientQuestions: true, ApiError: MockApiError,
  };
});
vi.mock("../api", () => api);

const signOut = vi.fn();
const page = (path = "/ask") =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/ask" element={<AskChat session={{ clinician_id: "md-santos", ward: "W4", role: "md" }} onSignOut={signOut} />} />
        <Route path="/ask/patient/:id" element={<AskChat session={{ clinician_id: "md-santos", ward: "W4", role: "md" }} onSignOut={signOut} />} />
      </Routes>
    </MemoryRouter>,
  );
const item = (over: Partial<QuestionItem>): QuestionItem => ({
  id: 1, requested_by: "md-santos", requested_at: "2026-11-13T09:30:00Z", question: "Why?", state: "queued",
  reason: null, claimed_at: null, finished_at: null, ...over,
});
const settle = () => act(async () => {});

beforeEach(() => {
  api.getWard.mockResolvedValue({ ranked: [{ id: "p-001", name: "Ada Park" }], abstained: [{ id: "p-002", name: "Bo Lind" }] });
  api.getLabQuestions.mockResolvedValue([item({ question: "ward question" })]);
  api.getPatientQuestions.mockResolvedValue([item({ question: "patient question" })]);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.hasLabQuestions = true;
  api.hasPatientQuestions = true;
});

describe("the page", () => {
  it("shows the heading, the ward's frame and the ward's thread", async () => {
    page();
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Ask" })).toBeTruthy();
    expect(screen.getByText("Ward W4")).toBeTruthy();
    expect(screen.getByRole("button", { name: /md-santos/ })).toBeTruthy(); // the session menu
    expect(screen.getByText("Simulated patients until a hospital is connected.")).toBeTruthy();
    expect(screen.getByText("ward question")).toBeTruthy();
    expect(screen.getByText(/Do not type anything about a patient/)).toBeTruthy();
    expect(api.getPatientQuestions).not.toHaveBeenCalled();
  });

  it("offers the ward and each patient, ranked and not ranked, and marks the current thread", async () => {
    page();
    await settle();
    const picker = within(screen.getByRole("navigation", { name: "Threads" }));
    expect(picker.getAllByRole("link").map((l) => [l.textContent, l.getAttribute("href"), l.getAttribute("aria-current")])).toEqual([
      ["The ward", "/ask", "page"],
      ["Ada Park", "/ask/patient/p-001", null],
      ["Bo Lind", "/ask/patient/p-002", null],
    ]);
  });

  it("opens a patient's thread from the picker and marks it", async () => {
    page();
    await settle();
    await userEvent.click(screen.getByRole("link", { name: "Bo Lind" }));
    await settle();
    expect(api.getPatientQuestions).toHaveBeenCalledWith("p-002");
    expect(screen.getByText("patient question")).toBeTruthy();
    expect(screen.queryByText("ward question")).toBeNull();
    expect(screen.getByRole("link", { name: "Bo Lind" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "The ward" }).getAttribute("aria-current")).toBeNull();
    expect(screen.getByText(/The agent sees this patient's data only/)).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "The ward" }));
    await settle();
    expect(screen.getByText("ward question")).toBeTruthy();
  });

  it("opens a patient's thread at its address", async () => {
    page("/ask/patient/p-001");
    await settle();
    expect(screen.getByText("patient question")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ada Park" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "The ward" }).getAttribute("aria-current")).toBeNull();
  });

  it("keeps the ward's thread, and says so, when the patient list cannot be read", async () => {
    api.getWard.mockRejectedValue(new Error("x"));
    page();
    await settle();
    expect(screen.getByText("The patient list could not be loaded.")).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "Threads" })).getAllByRole("link").map((l) => l.textContent)).toEqual(["The ward"]);
    expect(screen.getByText("ward question")).toBeTruthy();
  });

  it("ends the session when the patient list answers 401", async () => {
    api.getWard.mockRejectedValue(new api.ApiError(401));
    page();
    await settle();
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("shows the heading and one quiet line in the demo build, and sends no request", async () => {
    api.hasLabQuestions = false;
    api.hasPatientQuestions = false;
    page();
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Ask" })).toBeTruthy();
    expect(screen.getByText("Getting ready: the chat is not connected in this demo.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    cleanup();
    page("/ask/patient/p-001");
    await settle();
    expect(screen.getByText("Getting ready: the chat is not connected in this demo.")).toBeTruthy();
    for (const call of [api.getWard, api.getLabQuestions, api.getPatientQuestions, api.askLab, api.askPatient]) expect(call).not.toHaveBeenCalled();
  });
});
