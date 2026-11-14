// @vitest-environment jsdom
/** The one thread (the Ask page and the patient panel render it) and the state of its questions (useQuestions). */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuestionDetail, QuestionItem } from "../types";
import { Thread, type Scope } from "./Thread";
import { GETTING_READY_AFTER_MS, queuedLine } from "./useQuestions";
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
  return {
    askLab: vi.fn(), getLabQuestions: vi.fn(), getLabQuestion: vi.fn(),
    askPatient: vi.fn(), getPatientQuestions: vi.fn(), getPatientQuestion: vi.fn(), ApiError: MockApiError,
  };
});
const MockApiError = api.ApiError;
vi.mock("../api", () => api);

const signOut = vi.fn();
const WARD: Scope = { kind: "ward" };
const PATIENT: Scope = { kind: "patient", id: "p-001" };
const thread = (scope: Scope = WARD) => render(<Thread scope={scope} clinicianId="md-santos" onSignOut={signOut} />);
const clock = (iso: string) => formatClock(Date.parse(iso));
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

const ASKED = "2026-11-13T09:30:00Z";
const item = (over: Partial<QuestionItem>): QuestionItem => ({
  id: 1, requested_by: "md-santos", requested_at: ASKED, question: "What if we alerted at 7?", state: "done",
  reason: null, claimed_at: null, finished_at: null, ...over,
});
const detail = (over: Partial<QuestionDetail>): QuestionDetail => ({ ...item({}), answer: "one\ntwo", report: null, ...over });
const REPORT = {
  grounding: "ok",
  experiment: { template: "rule_compare", tool: "evaluate_rules", args: { rule: "news2_total_ge_7" } },
  numbers: [1, 2],
};

beforeEach(() => {
  api.getLabQuestions.mockResolvedValue([]);
  api.getPatientQuestions.mockResolvedValue([]);
  api.getLabQuestion.mockResolvedValue(detail({ report: REPORT }));
  api.getPatientQuestion.mockResolvedValue(detail({}));
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const input = () => screen.getByLabelText(/Your question/) as HTMLInputElement;
const send = () => screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
const settle = () => act(async () => {});
const turns = () => screen.getAllByRole("listitem");

describe("queuedLine", () => {
  it("swaps at exactly 3 minutes, and a running question of the thread wins", () => {
    expect(GETTING_READY_AFTER_MS).toBe(180_000);
    expect(queuedLine(2 * 60_000 + 59_000, false)).toBe("Sent. Waiting for the research agent.");
    expect(queuedLine(3 * 60_000, false)).toBe("Getting ready: the research agent may not be online yet. Your question stays in line for one hour.");
    expect(queuedLine(0, true)).toBe("Waiting: the research agent is answering another question.");
    expect(queuedLine(9 * 60_000, true)).toBe("Waiting: the research agent is answering another question.");
  });
});

describe("the turns", () => {
  it("lists the oldest at the top, as a list, with a label on each side", async () => {
    api.getLabQuestions.mockResolvedValue([
      item({ id: 2, question: "newer", requested_by: "rn-manalo", state: "queued", requested_at: ago(1000) }),
      item({ id: 1, question: "older" }),
    ]);
    thread();
    await settle();
    const rows = turns();
    expect(rows[0].textContent).toContain("older");
    expect(rows[1].textContent).toContain("newer");
    expect(within(rows[0]).getByText(`You · ${clock(ASKED)}`)).toBeTruthy();
    expect(within(rows[1]).getByText(/^rn-manalo · /)).toBeTruthy();
    for (const r of rows) expect(within(r).getByText("Research agent")).toBeTruthy();
    expect(screen.getByRole("list").getAttribute("role")).toBe("list");
  });

  it("says each state in its own sentence", async () => {
    api.getLabQuestions.mockResolvedValue([
      item({ id: 12, state: "paused" as QuestionItem["state"] }),
      item({ id: 11, state: "failed", reason: "the tool said Failed: x" }),
      item({ id: 10, state: "refused" }),
      item({ id: 9, state: "refused", reason: "the worker does not accept this job: question" }),
      item({ id: 8, state: "refused", reason: "Refused: a number in the answer is not in the tool result." }),
      item({ id: 7, state: "failed" }),
      item({ id: 6, state: "failed", reason: "no worker claimed it within an hour" }),
      item({ id: 5, state: "refused", reason: "Refused: no template fits this question." }),
      item({ id: 4, state: "running" }),
      item({ id: 3, state: "running", claimed_at: "2026-11-13T09:31:00Z" }),
      item({ id: 2, state: "queued", requested_at: ago(2 * 60_000) }),
      item({ id: 1, state: "queued", requested_at: ago(4 * 60_000) }),
    ]);
    thread();
    await settle();
    const live = turns().map((r) => within(r).getAllByText(/./).find((e) => e.getAttribute("aria-live") === "polite")!.textContent);
    expect(live).toEqual([
      // id 1 and 2 wait while id 3 and 4 run
      "Waiting: the research agent is answering another question.",
      "Waiting: the research agent is answering another question.",
      `Working since ${clock("2026-11-13T09:31:00Z")}.`,
      "Working.",
      "Refused by the research agent: no template fits this question.",
      "Not answered: no research agent took it within an hour. Ask again later.",
      "Failed: no reason given",
      "Refused by the research agent: a number in the answer is not in the tool result.",
      "Refused: the worker does not accept this job: question",
      "Refused: no reason given",
      "Failed: the tool said Failed: x",
      "paused",
    ]);
  });

  it.each([
    [2 * 60_000, "Sent. Waiting for the research agent."],
    [4 * 60_000, "Getting ready: the research agent may not be online yet. Your question stays in line for one hour."],
  ])("says the wait of %i ms for a queued question when none runs", async (wait, line) => {
    api.getLabQuestions.mockResolvedValue([item({ state: "queued", requested_at: ago(wait) })]);
    thread();
    await settle();
    expect(screen.getByText(line).getAttribute("aria-live")).toBe("polite");
  });

  it("renders a marker in a question, an asker, a reason, an answer and a record as text", async () => {
    api.getLabQuestions.mockResolvedValue([
      item({ id: 2, question: "<b>q</b>", requested_by: "<b>x</b>", state: "refused", reason: "<b>r</b>" }),
      item({ id: 1, question: "<b>q2</b>" }),
    ]);
    api.getLabQuestion.mockResolvedValue(
      detail({ answer: "<b>a</b>\nline two", report: { experiment: { template: "<b>t</b>", args: { x: "<b>y</b>" } } } }),
    );
    const { container } = thread();
    await settle();
    expect(container.querySelector("b, a[href^='http'], img, script")).toBeNull();
    expect(screen.getByText("<b>q</b>")).toBeTruthy();
    expect(screen.getByText(/<b>r<\/b>/)).toBeTruthy();
    expect(container.querySelector("pre")!.textContent).toBe("<b>a</b>\nline two");
    expect(screen.getByText("Template: <b>t</b>")).toBeTruthy();
    expect(container.querySelector("pre[aria-label]")!.textContent).toContain('"<b>y</b>"');
  });
});

describe("the answer", () => {
  it("shows the answer, the experiment, the grounding line and the record in a closed details", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 9 })]);
    const { container } = thread();
    await settle();
    expect(api.getLabQuestion).toHaveBeenCalledWith(9);
    expect(container.querySelector("pre")!.textContent).toBe("one\ntwo");
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(within(details).getByText("What this is based on")).toBeTruthy();
    expect(within(details).getByText("Template: rule_compare")).toBeTruthy();
    expect(within(details).getByText("Tool: evaluate_rules")).toBeTruthy();
    expect(within(details).getByText('{"rule":"news2_total_ge_7"}')).toBeTruthy();
    expect(within(details).getByText("Every number in the answer was checked against this run's result.")).toBeTruthy();
    expect(JSON.parse(details.querySelector("pre[aria-label]")!.textContent!)).toEqual(REPORT);
    expect(screen.getByText("Synthetic, not validated.")).toBeTruthy();
    expect(screen.getByText("Answered.")).toBeTruthy();
  });

  it("says no grounding when the record does not, names no experiment when none is stored, and tolerates odd shapes", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 3 }), item({ id: 2 }), item({ id: 1 })]);
    api.getLabQuestion
      .mockResolvedValueOnce(detail({ answer: "", report: { grounding: "failed", experiment: "text" } }))
      .mockResolvedValueOnce(detail({ answer: null, report: null }))
      .mockResolvedValueOnce(detail({ report: { experiment: { template: 5, tool: null, args: ["a"] } } }));
    thread();
    await settle();
    expect(screen.queryByText(/Every number in the answer/)).toBeNull();
    expect(screen.getAllByText("The stored record names no experiment.")).toHaveLength(2);
    expect(screen.getAllByText("No answer text was stored.")).toHaveLength(2);
    expect(screen.getByText("Template: 5")).toBeTruthy();
    expect(screen.getByText('["a"]')).toBeTruthy();
    expect(screen.queryByText(/Tool:/)).toBeNull();
  });

  it("for a patient says when the data was frozen, and holds no stored record", async () => {
    api.getPatientQuestions.mockResolvedValue([item({ id: 9 })]);
    const { container } = thread(PATIENT);
    await settle();
    expect(api.getPatientQuestion).toHaveBeenCalledWith("p-001", 9);
    expect(screen.getByText("Answered from this patient's data.")).toBeTruthy();
    expect(container.querySelector("pre[aria-label]")).toBeNull();
    expect(screen.queryByText(/Template/)).toBeNull();
    expect(screen.getByText("Synthetic, not validated.")).toBeTruthy();
  });

  it("loads the 5 newest answers by themselves, one read each, and the older one on its button", async () => {
    vi.useFakeTimers();
    api.getLabQuestions.mockResolvedValueOnce([
      ...[8, 7, 6, 5, 4, 3, 2, 1].map((id) => item({ id })),
      item({ id: 20, state: "queued", requested_at: ago(1000) }),
    ].sort((a, b) => b.id - a.id));
    thread();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(api.getLabQuestion.mock.calls.map((c) => c[0])).toEqual([8, 7, 6, 5, 4]);
    expect(screen.getAllByRole("button", { name: "Show answer" })).toHaveLength(3);
    api.getLabQuestions.mockResolvedValue([item({ id: 20, state: "queued" }), ...[8, 7, 6, 5, 4, 3, 2, 1].map((id) => item({ id }))]);
    await act(() => vi.advanceTimersByTimeAsync(5_000)); // a timed read: nothing is read again
    expect(api.getLabQuestion).toHaveBeenCalledTimes(5);
    fireEvent.click(screen.getAllByRole("button", { name: "Show answer" })[0]);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(api.getLabQuestion).toHaveBeenCalledTimes(6);
    expect(api.getLabQuestion).toHaveBeenLastCalledWith(1);
    expect(screen.getAllByRole("button", { name: "Show answer" })).toHaveLength(2);
  });

  it("says a failed read in one line, and the button reads it again", async () => {
    api.getPatientQuestions.mockResolvedValue([item({ id: 9 })]);
    api.getPatientQuestion.mockRejectedValueOnce(new MockApiError(404));
    thread(PATIENT);
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("This patient is no longer on your list.");
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("one\ntwo", { normalizer: (s) => s })).toBeTruthy();
    expect(api.getPatientQuestion).toHaveBeenCalledTimes(2);
  });

  it("has no answer part for a question that is not answered", async () => {
    api.getLabQuestions.mockResolvedValue([item({ state: "queued" })]);
    thread();
    await settle();
    expect(screen.queryByRole("button", { name: /answer/ })).toBeNull();
    expect(api.getLabQuestion).not.toHaveBeenCalled();
  });
});

describe("the composer", () => {
  it("warns above the field, for the ward and for a patient, and gives the help line only for the ward", async () => {
    thread();
    await settle();
    expect(
      screen.getByText("Do not type anything about a patient: no name, no bed, no id. Every clinician of this ward can read each question, and it is kept.").parentElement!
        .textContent,
    ).toMatch(/^Warning\. Do not type/);
    expect(screen.getByText(/The agent answers from simulated trials only\. For example: What if we alerted at a NEWS2 total of 7 or more\?/)).toBeTruthy();
    expect(screen.getByText("0/500").getAttribute("aria-hidden")).toBe("true");
    expect(input().maxLength).toBe(500);
    expect(screen.getByText("No question yet.")).toBeTruthy();
    cleanup();
    thread(PATIENT);
    await settle();
    expect(screen.getByText("The agent sees this patient's data only, with no name. Do not type a name in your question.")).toBeTruthy();
    expect(screen.queryByText(/simulated trials only/)).toBeNull();
  });

  it("sends the question trimmed, clears the input, reads again and scrolls the newest turn into view", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      api.askLab.mockResolvedValue(item({ state: "queued" }));
      thread();
      await settle();
      await userEvent.type(input(), "  Why now?  ");
      expect(screen.getByText("12/500")).toBeTruthy();
      api.getLabQuestions.mockResolvedValue([item({ state: "queued", requested_at: ago(1000) })]);
      await userEvent.click(send());
      expect(api.askLab).toHaveBeenCalledWith("Why now?");
      expect(input().value).toBe("");
      expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
      expect(screen.getByText("Sent. Waiting for the research agent.")).toBeTruthy();
      expect(scroll).toHaveBeenCalledTimes(1);
      expect(scroll.mock.contexts[0]).toBe(turns()[0]);
    } finally {
      delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("sends a patient question to the patient's API, on Enter", async () => {
    api.askPatient.mockResolvedValue(item({}));
    thread(PATIENT);
    await settle();
    await userEvent.type(input(), "Why?{Enter}");
    expect(api.askPatient).toHaveBeenCalledWith("p-001", "Why?");
    expect(api.askLab).not.toHaveBeenCalled();
    expect(api.getLabQuestions).not.toHaveBeenCalled();
  });

  it("is disabled when empty or only spaces, and while a send is in flight", async () => {
    api.askLab.mockReturnValue(new Promise(() => {}));
    thread();
    await settle();
    expect(send().disabled).toBe(true);
    await userEvent.type(input(), "   ");
    expect(send().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).not.toHaveBeenCalled();
    await userEvent.type(input(), "x");
    await userEvent.click(send());
    expect(send().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).toHaveBeenCalledTimes(1);
  });

  it.each(["queued", "running"] as const)("is disabled with one line while my question is %s", async (state) => {
    api.getLabQuestions.mockResolvedValue([item({ state })]);
    thread();
    await settle();
    expect(input().disabled).toBe(true);
    expect(send().disabled).toBe(true);
    expect(screen.getByText("Wait for the answer to your last question.")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).not.toHaveBeenCalled();
  });

  it("is not blocked by another clinician's open question or my answered one", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 2, state: "queued", requested_by: "rn-manalo" }), item({ id: 1 })]);
    thread();
    await settle();
    await userEvent.type(input(), "Another");
    expect(send().disabled).toBe(false);
    expect(screen.queryByText(/Wait for the answer/)).toBeNull();
  });

  it.each([
    [WARD, 429, "Question limit reached: one open question each, ten a day each, five waiting on the ward. Try again later."],
    [WARD, 403, "Only a physician or a charge nurse can ask."],
    [WARD, 404, "This ward is not on this server."],
    [WARD, 422, "The question could not be sent. Use one line, up to 500 characters."],
    [PATIENT, 429, "Question limit reached. Try again later."],
    [PATIENT, 403, "You cannot ask about this patient."],
    [PATIENT, 404, "This patient is no longer on your list."],
    [PATIENT, 500, "Cannot reach the ward server."],
  ])("says the %j %i error in one line and keeps the text", async (scope, status, line) => {
    const failing = new MockApiError(status);
    api.askLab.mockRejectedValue(failing);
    api.askPatient.mockRejectedValue(failing);
    thread(scope);
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toBe(line);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(input().value).toBe("Why?");
    expect(send().disabled).toBe(false);
  });

  it("says a network failure in one line, and ends the session on a 401", async () => {
    api.askLab.mockRejectedValueOnce(new Error("offline")).mockRejectedValueOnce(new MockApiError(401));
    thread();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toBe("Cannot reach the ward server.");
    expect(signOut).not.toHaveBeenCalled();
    await userEvent.click(send());
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
    expect(input().value).toBe("Why?");
  });

  it("says a 404 and a failure of a read, for Refresh too", async () => {
    api.getPatientQuestions.mockRejectedValueOnce(new MockApiError(404)).mockRejectedValueOnce(new Error("x"));
    thread(PATIENT);
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("This patient is no longer on your list.");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("Cannot reach the ward server.");
  });
});

describe("the timer", () => {
  const open = [item({ id: 1, state: "queued", requested_at: ago(1000) })];
  const done = [item({ id: 1, state: "done" })];
  const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const start = async () => {
    vi.useFakeTimers();
    thread();
    await tick(0);
  };

  it("reads every 5 s while a question is open, then stops when all are final", async () => {
    api.getLabQuestions.mockResolvedValueOnce(open).mockResolvedValue(done);
    await start();
    expect(screen.getByText(/Sent\. Waiting/)).toBeTruthy();
    await tick(5_000);
    expect(screen.getByText("Answered.")).toBeTruthy();
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
  });

  it("changes the line of a waiting question at its 3 minutes, on the timed read", async () => {
    api.getLabQuestions.mockResolvedValue([item({ state: "queued", requested_at: new Date(Date.now() - 170_000).toISOString() })]);
    await start();
    expect(screen.getByText(/^Sent\./)).toBeTruthy();
    await tick(10_000); // 180 s
    expect(screen.getByText(/^Getting ready:/)).toBeTruthy();
  });

  it("starts no timer when none is open, and stops when the page closes", async () => {
    api.getLabQuestions.mockResolvedValue(done);
    await start();
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(1);
    cleanup();
    api.getLabQuestions.mockResolvedValue(open);
    await start();
    cleanup();
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
  });

  it("does not let a read that answers late replace a newer list", async () => {
    let late: (v: QuestionItem[]) => void = () => {};
    const theirs = [item({ id: 1, state: "queued", requested_by: "rn-manalo", requested_at: ago(1000) })]; // a colleague's open question keeps the timer on
    const both = [item({ id: 2, state: "queued", question: "Mine?", requested_at: ago(500) }), ...theirs];
    api.getLabQuestions.mockResolvedValueOnce(theirs).mockReturnValueOnce(new Promise((r) => { late = r; })).mockResolvedValue(both);
    api.askLab.mockResolvedValue(both[0]);
    await start();
    await tick(5_000); // the timed read is in flight
    fireEvent.change(input(), { target: { value: "Mine?" } });
    fireEvent.click(send());
    await tick(0); // the send, then its own read
    expect(screen.getByText("Mine?")).toBeTruthy();
    late(theirs); // the older read answers now
    await tick(0);
    expect(screen.getByText("Mine?")).toBeTruthy();
    expect(send().disabled).toBe(true);
  });

  it("does not let a failure that comes late stop the timer or show an error", async () => {
    let late: (e: Error) => void = () => {};
    const theirs = item({ id: 1, state: "queued", requested_by: "rn-manalo", requested_at: ago(1000) });
    const both = [item({ id: 2, state: "queued", question: "Mine?", requested_at: ago(500) }), theirs];
    api.getLabQuestions
      .mockResolvedValueOnce([theirs])
      .mockReturnValueOnce(new Promise((_, r) => { late = r; }))
      .mockResolvedValue(both);
    api.askLab.mockResolvedValue(both[0]);
    await start();
    await tick(5_000); // the timed read is in flight
    fireEvent.change(input(), { target: { value: "Mine?" } });
    fireEvent.click(send());
    await tick(0); // the send, then a newer read that works
    late(new MockApiError(500)); // the older read fails now
    await tick(0);
    expect(screen.queryByRole("alert")).toBeNull();
    const before = api.getLabQuestions.mock.calls.length;
    await tick(5_000);
    expect(api.getLabQuestions.mock.calls.length).toBe(before + 1);
  });

  it("does not start a read while one is in flight", async () => {
    let finish: (v: QuestionItem[]) => void = () => {};
    api.getLabQuestions.mockResolvedValueOnce(open).mockReturnValueOnce(new Promise((r) => { finish = r; })).mockResolvedValue(done);
    await start();
    await tick(15_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    finish(done);
    await tick(0);
    expect(screen.getByText("Answered.")).toBeTruthy();
  });

  it("stops after a failed read; Refresh starts it again", async () => {
    api.getLabQuestions.mockResolvedValueOnce(open).mockRejectedValueOnce(new Error("x")).mockResolvedValue(open);
    await start();
    await tick(5_000);
    expect(screen.getByRole("alert").textContent).toBe("Cannot reach the ward server.");
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await tick(0);
    await tick(5_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(4);
  });

  it("ends the session on a 401 in a timed read and stops", async () => {
    api.getLabQuestions.mockResolvedValueOnce(open).mockRejectedValue(new MockApiError(401));
    await start();
    await tick(5_000);
    expect(signOut).toHaveBeenCalledTimes(1);
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
  });

  it("keeps the typed text and an answer through a timed read", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 2, state: "queued", requested_by: "rn-manalo", requested_at: ago(1000) }), item({ id: 1 })]);
    api.getLabQuestion.mockResolvedValue(detail({ answer: "kept" }));
    await start();
    fireEvent.change(input(), { target: { value: "typed" } });
    await tick(5_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    expect(screen.getByText("kept")).toBeTruthy();
    expect(input().value).toBe("typed");
    expect(api.getLabQuestion).toHaveBeenCalledTimes(1);
  });
});
