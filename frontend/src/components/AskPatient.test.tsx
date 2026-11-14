// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PatientQuestionDetail, PatientQuestionItem } from "../types";
import { AskPatient } from "./AskPatient";
import { formatClock } from "./format";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return { askPatient: vi.fn(), getPatientQuestions: vi.fn(), getPatientQuestion: vi.fn(), ApiError: MockApiError };
});
const MockApiError = api.ApiError;
vi.mock("../api", () => api);

const signOut = vi.fn();
const panel = () => render(<AskPatient patientId="p-001" clinicianId="rn-manalo" onSignOut={signOut} />);
const clock = (iso: string) => formatClock(Date.parse(iso));

const ASKED = "2026-11-13T09:30:00Z";
const item = (over: Partial<PatientQuestionItem>): PatientQuestionItem => ({
  id: 1, requested_by: "rn-manalo", requested_at: ASKED, question: "Why is the rate up?", state: "done",
  reason: null, claimed_at: null, finished_at: null, ...over,
});
const detail = (over: Partial<PatientQuestionDetail>): PatientQuestionDetail => ({ ...item({}), answer: "one\ntwo", report: null, ...over });

beforeEach(() => {
  api.getPatientQuestions.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const input = () => screen.getByLabelText(/Your question/) as HTMLInputElement;
const ask = () => screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
const settle = () => act(async () => {});

describe("the panel", () => {
  it("shows the heading, the one line, the empty list and reads once on mount", async () => {
    panel();
    await settle();
    expect(screen.getByRole("heading", { level: 2, name: "Ask about this patient" })).toBeTruthy();
    expect(
      screen.getByText("The agent sees this patient's data only, with no name. Do not type a name in your question. Synthetic, not validated."),
    ).toBeTruthy();
    expect(screen.getByText("No question yet.")).toBeTruthy();
    expect(screen.getByText("0/500")).toBeTruthy();
    expect(input().maxLength).toBe(500);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(1);
    expect(api.getPatientQuestions).toHaveBeenCalledWith("p-001");
  });

  it("shows each state in words and who asked another clinician's question", async () => {
    api.getPatientQuestions.mockResolvedValue([
      item({ id: 1, state: "queued" }),
      item({ id: 2, state: "running", claimed_at: "2026-11-13T09:31:00Z" }),
      item({ id: 3, state: "running" }),
      item({ id: 4, state: "done", requested_by: "md-santos" }),
      item({ id: 5, state: "refused", reason: "not a question about this patient" }),
      item({ id: 6, state: "failed", reason: "no worker claimed it within an hour" }),
      item({ id: 7, state: "failed" }),
      item({ id: 8, state: "refused", reason: "Refused: a number in the answer is not in this patient's data." }),
      item({ id: 9, state: "paused" as PatientQuestionItem["state"] }),
    ]);
    panel();
    await settle();
    const rows = screen.getAllByRole("listitem").map((r) => r.textContent);
    expect(rows[0]).toContain("Waiting");
    expect(rows[1]).toContain(`Working since ${clock("2026-11-13T09:31:00Z")}`);
    expect(rows[2]).toContain("Working");
    expect(rows[2]).not.toContain("since");
    expect(rows[3]).toContain("asked by md-santos");
    expect(rows[3]).toContain("Answered");
    expect(rows[0]).not.toContain("asked by");
    expect(rows[0]).toContain(clock(ASKED));
    expect(rows[4]).toContain("Refused: not a question about this patient");
    expect(rows[5]).toContain("Failed: no worker claimed it within an hour");
    expect(rows[6]).toContain("Failed: no reason given");
    expect(rows[7]).toContain("\u00b7 Refused: a number in the answer is not in this patient's data."); // the word once
    expect(rows[8]).toContain("\u00b7 paused"); // a state this build does not know is shown as it came
    expect(rows[8]).not.toContain("undefined");
  });

  it("renders a server string as text, never as markup", async () => {
    const evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    api.getPatientQuestions.mockResolvedValue([item({ question: evil, requested_by: "<b>x</b>", state: "refused", reason: "<i>r</i>" })]);
    const { container } = panel();
    await settle();
    expect(container.querySelector("img, script, b, i, a")).toBeNull();
    expect(screen.getByText(evil)).toBeTruthy();
  });
});

describe("sending", () => {
  it("sends the question trimmed, clears the input after a 201 and reads again", async () => {
    api.askPatient.mockResolvedValue(item({ state: "queued" }));
    panel();
    await settle();
    await userEvent.type(input(), "  Why now?  ");
    expect(screen.getByText("12/500")).toBeTruthy();
    api.getPatientQuestions.mockResolvedValue([item({ state: "queued" })]);
    await userEvent.click(ask());
    expect(api.askPatient).toHaveBeenCalledWith("p-001", "Why now?");
    expect(input().value).toBe("");
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Waiting/)).toBeTruthy();
  });

  it("sends on Enter", async () => {
    api.askPatient.mockResolvedValue(item({}));
    panel();
    await settle();
    await userEvent.type(input(), "Why?{Enter}");
    expect(api.askPatient).toHaveBeenCalledTimes(1);
  });

  it("is disabled when empty or only spaces", async () => {
    panel();
    await settle();
    expect(ask().disabled).toBe(true);
    await userEvent.type(input(), "   ");
    expect(ask().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askPatient).not.toHaveBeenCalled();
    await userEvent.type(input(), "x");
    expect(ask().disabled).toBe(false);
  });

  it("is disabled while a send is in flight", async () => {
    api.askPatient.mockReturnValue(new Promise(() => {}));
    panel();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect(ask().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askPatient).toHaveBeenCalledTimes(1);
  });

  it.each(["queued", "running"] as const)("is disabled and says why while my question is %s", async (state) => {
    api.getPatientQuestions.mockResolvedValue([item({ state })]);
    panel();
    await settle();
    await userEvent.type(input(), "Another");
    expect(ask().disabled).toBe(true);
    expect(
      screen.getByText("Wait for the answer to your last question. A question that is not taken within one hour fails by itself."),
    ).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(api.askPatient).not.toHaveBeenCalled();
  });

  it("is not blocked by another clinician's open question or my answered one", async () => {
    api.getPatientQuestions.mockResolvedValue([item({ id: 2, state: "queued", requested_by: "md-santos" }), item({ id: 1 })]);
    panel();
    await settle();
    await userEvent.type(input(), "Another");
    expect(ask().disabled).toBe(false);
    expect(screen.queryByText(/Wait for the answer to your last question/)).toBeNull();
  });

  it.each([
    [429, "Question limit reached. Try again later."],
    [403, "You cannot ask about this patient."],
    [404, "This patient is no longer on your list."],
    [422, "The question could not be sent. Use one line, up to 500 characters."],
    [500, "Cannot reach the ward server."],
  ])("says the %i error in one line and keeps the text", async (status, line) => {
    api.askPatient.mockRejectedValue(new MockApiError(status));
    panel();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe(line);
    expect(input().value).toBe("Why?");
    expect(ask().disabled).toBe(false);
  });

  it("says a network failure in one line and keeps the text", async () => {
    api.askPatient.mockRejectedValue(new Error("offline"));
    panel();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe("Cannot reach the ward server.");
    expect(input().value).toBe("Why?");
  });

  it("ends the session on a 401", async () => {
    api.askPatient.mockRejectedValue(new MockApiError(401));
    panel();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(input().value).toBe("Why?");
  });

  it("says a 404 and a failure of the first read", async () => {
    api.getPatientQuestions.mockRejectedValueOnce(new MockApiError(404)).mockRejectedValueOnce(new Error("x"));
    panel();
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("This patient is no longer on your list.");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("Cannot reach the ward server.");
  });
});

describe("the answer", () => {
  const evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>\nline two';
  const answered = () => {
    api.getPatientQuestions.mockResolvedValue([item({ id: 9 })]);
    api.getPatientQuestion.mockResolvedValue(detail({ id: 9, answer: evil }));
  };

  it("loads once, keeps the line breaks, shows markup as text, hides and shows without a second call", async () => {
    answered();
    const { container } = panel();
    await settle();
    expect(container.querySelector("pre")).toBeNull();
    const button = screen.getByRole("button", { name: "Show answer" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(button);
    expect(api.getPatientQuestion).toHaveBeenCalledWith("p-001", 9);
    const pre = container.querySelector("pre")!;
    expect(pre.textContent).toBe(evil);
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.getByText(`Answered from the data as it was at ${clock(ASKED)}.`)).toBeTruthy();
    const hide = screen.getByRole("button", { name: "Hide answer" });
    expect(hide.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(hide.getAttribute("aria-controls")!)!.contains(pre)).toBe(true);
    await userEvent.click(hide);
    expect(container.querySelector("pre")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(container.querySelector("pre")).not.toBeNull();
    expect(api.getPatientQuestion).toHaveBeenCalledTimes(1);
  });

  it.each([null, ""])("says so when the server holds no answer text (%j), and shows no empty answer", async (answer) => {
    api.getPatientQuestions.mockResolvedValue([item({ id: 9 })]);
    api.getPatientQuestion.mockResolvedValue(detail({ id: 9, answer }));
    const { container } = panel();
    await settle();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(container.querySelector("pre")).toBeNull();
    expect(screen.getByText("No answer text was stored.")).toBeTruthy();
    expect(screen.queryByText(/Answered from the data/)).toBeNull();
  });

  it("says a failed read in one line and can try again", async () => {
    answered();
    api.getPatientQuestion.mockRejectedValueOnce(new MockApiError(404));
    panel();
    await settle();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.getByRole("alert").textContent).toBe("This patient is no longer on your list.");
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Hide answer" })).toBeTruthy();
  });

  it("has a button only for an answered question", async () => {
    api.getPatientQuestions.mockResolvedValue([item({ state: "queued" })]);
    panel();
    await settle();
    expect(screen.queryByRole("button", { name: /answer/ })).toBeNull();
  });
});

describe("the timer", () => {
  const open = [item({ id: 1, state: "queued" })];
  const done = [item({ id: 1, state: "done" })];
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const start = async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    panel();
    await tick(0);
  };
  afterEach(() => vi.useRealTimers());

  it("reads every 5 s while a question is open, then stops when all are final", async () => {
    api.getPatientQuestions.mockResolvedValueOnce(open).mockResolvedValue(done);
    await start();
    expect(screen.getByText(/Waiting/)).toBeTruthy();
    await tick(5_000);
    expect(screen.getByText(/Answered/)).toBeTruthy();
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    await tick(60_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
  });

  it("starts no timer when none is open, and stops when the page closes", async () => {
    api.getPatientQuestions.mockResolvedValue(done);
    await start();
    await tick(60_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(1);
    cleanup();
    api.getPatientQuestions.mockResolvedValue(open);
    await start();
    cleanup();
    await tick(60_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
  });

  it("does not let a read that answers late replace a newer list", async () => {
    let late: (v: PatientQuestionItem[]) => void = () => {};
    const theirs = [item({ id: 1, state: "queued", requested_by: "md-santos" })]; // a colleague's open question keeps the timer on
    const both = [item({ id: 2, state: "queued", question: "Mine?" }), ...theirs];
    api.getPatientQuestions.mockResolvedValueOnce(theirs).mockReturnValueOnce(new Promise((r) => { late = r; })).mockResolvedValue(both);
    api.askPatient.mockResolvedValue(both[0]);
    await start();
    await tick(5_000); // the timed read is in flight
    fireEvent.change(input(), { target: { value: "Mine?" } });
    fireEvent.click(ask());
    await tick(0); // the send, then its own read
    expect(screen.getByText("Mine?")).toBeTruthy();
    late(theirs); // the older read answers now
    await tick(0);
    expect(screen.getByText("Mine?")).toBeTruthy();
    expect(ask().disabled).toBe(true);
  });

  it("does not start a read while one is in flight", async () => {
    let finish: (v: PatientQuestionItem[]) => void = () => {};
    api.getPatientQuestions.mockResolvedValueOnce(open).mockReturnValueOnce(new Promise((r) => { finish = r; })).mockResolvedValue(done);
    await start();
    await tick(15_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    finish(done);
    await tick(0);
    expect(screen.getByText(/Answered/)).toBeTruthy();
  });

  it("stops after a failed read; Refresh starts it again", async () => {
    api.getPatientQuestions.mockResolvedValueOnce(open).mockRejectedValueOnce(new Error("x")).mockResolvedValue(open);
    await start();
    await tick(5_000);
    expect(screen.getByRole("alert").textContent).toBe("Cannot reach the ward server.");
    await tick(60_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await tick(0);
    await tick(5_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(4);
  });

  it("ends the session on a 401 in a timed read and stops", async () => {
    api.getPatientQuestions.mockResolvedValueOnce(open).mockRejectedValue(new MockApiError(401));
    await start();
    await tick(5_000);
    expect(signOut).toHaveBeenCalledTimes(1);
    await tick(60_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
  });

  it("keeps an open answer and the typed text through a timed read", async () => {
    const mixed = [item({ id: 2, state: "queued", requested_by: "md-santos" }), item({ id: 1 })];
    api.getPatientQuestions.mockResolvedValue(mixed);
    api.getPatientQuestion.mockResolvedValue(detail({ answer: "kept" }));
    await start();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    await userEvent.type(input(), "typed");
    await tick(5_000);
    expect(api.getPatientQuestions).toHaveBeenCalledTimes(2);
    expect(screen.getByText("kept")).toBeTruthy();
    expect(input().value).toBe("typed");
    expect(document.activeElement).toBe(input());
    expect(within(screen.getAllByRole("listitem")[0]).getByText(/Waiting/)).toBeTruthy();
  });
});
