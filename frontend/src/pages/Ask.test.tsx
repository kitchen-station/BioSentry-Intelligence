// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuestionDetail, QuestionItem, SessionRole } from "../types";
import { formatClock } from "../components/format";
import { Ask } from "./Ask";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(status: number) {
      super(String(status));
      this.status = status;
    }
  }
  return { askLab: vi.fn(), getLabQuestions: vi.fn(), getLabQuestion: vi.fn(), hasLabQuestions: true, ApiError: MockApiError };
});
const MockApiError = api.ApiError;
vi.mock("../api", () => api);

const signOut = vi.fn();
const page = (role: SessionRole = "md", id = "md-santos") =>
  render(
    <MemoryRouter initialEntries={["/lab/ask"]}>
      <Routes>
        <Route path="/lab/ask" element={<Ask session={{ clinician_id: id, ward: "W4", role }} onSignOut={signOut} />} />
        <Route path="/lab" element={<p>the lab</p>} />
        <Route path="/ward" element={<p>the ward</p>} />
      </Routes>
    </MemoryRouter>,
  );
const clock = (iso: string) => formatClock(Date.parse(iso));

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
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.hasLabQuestions = true;
});

const input = () => screen.getByLabelText(/Your question/) as HTMLInputElement;
const ask = () => screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
const settle = () => act(async () => {});

describe("the page", () => {
  it("shows the heading, the note, the warning, the example and the way back", async () => {
    page();
    await settle();
    expect(screen.getByRole("heading", { level: 1, name: "Ask the research agent" })).toBeTruthy();
    expect(
      screen.getByText(
        "The agent answers from simulated trials only. It runs one of its own fixed experiments and checks every number in its answer against that run. Synthetic, not validated.",
      ),
    ).toBeTruthy();
    const warning = screen.getByText(
      "Do not type anything about a patient: no name, no bed, no id. Every clinician of this ward can read each question, and it is kept.",
    );
    expect(warning.textContent).toContain("Warning.");
    expect(screen.getByText(/For example: What if we alerted at a NEWS2 total of 7 or more\?/).textContent).toContain(
      "It refuses a question that fits none of its experiments.",
    );
    expect(screen.getByText("No question yet.")).toBeTruthy();
    expect(screen.getByText("0/500").getAttribute("aria-hidden")).toBe("true");
    expect(input().maxLength).toBe(500);
    await userEvent.click(screen.getByRole("link", { name: "Back to the Simulation lab" }));
    expect(screen.getByText("the lab")).toBeTruthy();
  });

  it("reads once on mount for a physician and a charge nurse, and shows the form", async () => {
    for (const role of ["md", "charge_rn"] as const) {
      page(role);
      await settle();
      expect(ask()).toBeTruthy();
      cleanup();
    }
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
  });

  it("gives a staff nurse the list and no form", async () => {
    api.getLabQuestions.mockResolvedValue([item({ requested_by: "md-santos" })]);
    page("rn", "rn-reyes");
    await settle();
    expect(screen.getByText("Only a physician or a charge nurse can ask. You can read the questions and answers.")).toBeTruthy();
    expect(screen.queryByLabelText(/Your question/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask" })).toBeNull();
    expect(screen.getByText(/asked by md-santos/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show answer" })).toBeTruthy();
  });

  it("gives an admin one line, no form, no list and no request", async () => {
    page("admin", "admin-ruiz");
    await settle();
    expect(screen.getByText("Questions are for the ward's clinicians.")).toBeTruthy();
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
    expect(api.getLabQuestions).not.toHaveBeenCalled();
  });

  it("makes no request in the fixture build", async () => {
    api.hasLabQuestions = false;
    page();
    await settle();
    expect(screen.getByText("Questions need the server.")).toBeTruthy();
    expect(within(screen.getByRole("main")).queryByRole("button")).toBeNull();
    for (const call of [api.getLabQuestions, api.getLabQuestion, api.askLab]) expect(call).not.toHaveBeenCalled();
  });
});

describe("the list", () => {
  it("shows each state in words, who asked another clinician's question, and the time", async () => {
    api.getLabQuestions.mockResolvedValue([
      item({ id: 1, state: "queued" }),
      item({ id: 2, state: "running", claimed_at: "2026-11-13T09:31:00Z" }),
      item({ id: 3, state: "running" }),
      item({ id: 4, state: "done", requested_by: "rn-manalo" }),
      item({ id: 5, state: "refused", reason: "fits none of the experiments" }),
      item({ id: 6, state: "failed", reason: "no worker claimed it within an hour" }),
      item({ id: 7, state: "failed" }),
      item({ id: 8, state: "refused", reason: "Refused: no such rule" }),
      item({ id: 9, state: "paused" as QuestionItem["state"] }),
    ]);
    page();
    await settle();
    const rows = screen.getAllByRole("listitem").map((r) => r.textContent);
    expect(rows[0]).toContain("Waiting");
    expect(rows[0]).toContain(clock(ASKED));
    expect(rows[0]).not.toContain("asked by");
    expect(rows[1]).toContain(`Working since ${clock("2026-11-13T09:31:00Z")}`);
    expect(rows[2]).toContain("Working");
    expect(rows[2]).not.toContain("since");
    expect(rows[3]).toContain("asked by rn-manalo");
    expect(rows[3]).toContain("Answered");
    expect(rows[4]).toContain("Refused by the research agent: fits none of the experiments");
    expect(rows[5]).toContain("Failed: no worker claimed it within an hour");
    expect(rows[6]).toContain("Failed: no reason given");
    expect(rows[7]).toContain("· Refused by the research agent: no such rule"); // the word once
    expect(rows[7]).not.toContain("Refused: ");
    expect(rows[8]).toContain("· paused");
    expect(screen.getAllByText(/Waiting/)[0].getAttribute("aria-live")).toBe("polite");
  });

  it("renders a marker in the question, the asker and the reason as text", async () => {
    api.getLabQuestions.mockResolvedValue([item({ question: "<b>q</b>", requested_by: "<b>x</b>", state: "refused", reason: "<b>r</b>" })]);
    const { container } = page();
    await settle();
    expect(container.querySelector("b, a[href^='http']")).toBeNull();
    expect(screen.getByText("<b>q</b>")).toBeTruthy();
    expect(screen.getByText(/<b>r<\/b>/)).toBeTruthy();
  });
});

describe("sending", () => {
  it("sends the question trimmed, clears the input after a 201 and reads again", async () => {
    api.askLab.mockResolvedValue(item({ state: "queued" }));
    page();
    await settle();
    await userEvent.type(input(), "  Why now?  ");
    expect(screen.getByText("12/500")).toBeTruthy();
    api.getLabQuestions.mockResolvedValue([item({ state: "queued" })]);
    await userEvent.click(ask());
    expect(api.askLab).toHaveBeenCalledWith("Why now?");
    expect(input().value).toBe("");
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Waiting/)).toBeTruthy();
  });

  it("is disabled when empty or only spaces, and while a send is in flight", async () => {
    api.askLab.mockReturnValue(new Promise(() => {}));
    page();
    await settle();
    expect(ask().disabled).toBe(true);
    await userEvent.type(input(), "   ");
    expect(ask().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).not.toHaveBeenCalled();
    await userEvent.type(input(), "x");
    await userEvent.click(ask());
    expect(ask().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).toHaveBeenCalledTimes(1);
  });

  it.each(["queued", "running"] as const)("disables the form and says why while my question is %s", async (state) => {
    api.getLabQuestions.mockResolvedValue([item({ state })]);
    page();
    await settle();
    expect(input().disabled).toBe(true);
    expect(ask().disabled).toBe(true);
    expect(
      screen.getByText("Wait for the answer to your last question. A question that is not taken within one hour fails by itself."),
    ).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(api.askLab).not.toHaveBeenCalled();
  });

  it("is not blocked by another clinician's open question", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 2, state: "queued", requested_by: "rn-manalo" }), item({ id: 1 })]);
    page();
    await settle();
    await userEvent.type(input(), "Another");
    expect(ask().disabled).toBe(false);
    expect(screen.queryByText(/Wait for the answer/)).toBeNull();
  });

  it.each([
    [429, "Question limit reached: one open question at a time, ten a day. Try again later."],
    [403, "Only a physician or a charge nurse can ask."],
    [404, "This ward is not on this server."],
    [422, "The question could not be sent. Use one line, up to 500 characters."],
    [500, "Cannot reach the ward server."],
  ])("says the %i error in one line and keeps the text", async (status, line) => {
    api.askLab.mockRejectedValue(new MockApiError(status));
    page();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe(line);
    expect(input().value).toBe("Why?");
    expect(ask().disabled).toBe(false);
  });

  it("says a network failure in one line", async () => {
    api.askLab.mockRejectedValue(new Error("offline"));
    page();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe("Cannot reach the ward server.");
  });

  it("ends the session on a 401", async () => {
    api.askLab.mockRejectedValue(new MockApiError(401));
    page();
    await settle();
    await userEvent.type(input(), "Why?");
    await userEvent.click(ask());
    expect((await screen.findByRole("alert")).textContent).toBe("Your session has ended. Sign in again.");
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it("says a failed first read", async () => {
    api.getLabQuestions.mockRejectedValueOnce(new MockApiError(404));
    page();
    await settle();
    expect(screen.getByRole("alert").textContent).toBe("This ward is not on this server.");
  });
});

describe("the answer", () => {
  const evil = "<b>bold</b>\nline two";
  const open = async (d: Partial<QuestionDetail>) => {
    api.getLabQuestions.mockResolvedValue([item({ id: 9 })]);
    api.getLabQuestion.mockResolvedValue(detail({ id: 9, ...d }));
    const view = page();
    await settle();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    return view;
  };

  it("shows the answer, the experiment, the checked line, the record and the note in order, and reads once", async () => {
    const { container } = await open({ answer: evil, report: REPORT });
    expect(api.getLabQuestion).toHaveBeenCalledWith(9);
    const pres = [...container.querySelectorAll("pre")];
    expect(pres[0].textContent).toBe(evil);
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByRole("heading", { level: 3, name: "The experiment that ran" })).toBeTruthy();
    expect(screen.getByText("Template: rule_compare")).toBeTruthy();
    expect(screen.getByText("Tool: evaluate_rules")).toBeTruthy();
    expect(pres[1].textContent).toBe('{"rule":"news2_total_ge_7"}');
    expect(screen.getByText("Every number in the answer was checked against this run's result.")).toBeTruthy();
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(within(details).getByText("The whole stored record")).toBeTruthy();
    const record = screen.getByRole("region", { name: "Stored record of question 9" });
    expect(record.tagName).toBe("PRE");
    expect(record.tabIndex).toBe(0);
    expect(record.textContent).toBe(JSON.stringify(REPORT, null, 2));
    expect(screen.getByText("Synthetic, not validated.")).toBeTruthy();
    const order = [pres[0], screen.getByText("The experiment that ran"), screen.getByText(/Every number in the answer/), details, screen.getByText("Synthetic, not validated.")];
    order.slice(1).forEach((el, i) => expect(order[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy());
    const hide = screen.getByRole("button", { name: "Hide answer" });
    expect(hide.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(hide.getAttribute("aria-controls")!)!.contains(pres[0])).toBe(true);
    await userEvent.click(hide);
    expect(container.querySelector("pre")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(container.querySelector("pre")).not.toBeNull();
    expect(api.getLabQuestion).toHaveBeenCalledTimes(1);
  });

  it.each([null, ""])("says so when the stored answer is %j", async (answer) => {
    const { container } = await open({ answer, report: REPORT });
    expect(screen.getByText("No answer text was stored.")).toBeTruthy();
    expect(container.querySelector("pre")!.textContent).toBe('{"rule":"news2_total_ge_7"}'); // the first pre is now the arguments
  });

  it.each([
    ["null", null],
    ["a string", "x" as unknown as QuestionDetail["report"]],
    ["a list", [1] as unknown as QuestionDetail["report"]],
    ["empty", {}],
    ["an experiment that is a string", { experiment: "x" }],
    ["an experiment that is null", { experiment: null }],
  ])("says the record names no experiment when the record is %s", async (_, report) => {
    await open({ report });
    expect(screen.getByText("The stored record names no experiment.")).toBeTruthy();
    expect(screen.queryByText(/^Template:/)).toBeNull();
    expect(screen.getByText("The whole stored record")).toBeTruthy();
  });

  it("shows only the keys that are there, whatever their type", async () => {
    const { container } = await open({ report: { experiment: { template: 7, args: [1, { a: null }] } } });
    expect(screen.getByText("Template: 7")).toBeTruthy();
    expect(screen.queryByText(/^Tool:/)).toBeNull();
    expect(container.querySelectorAll("pre")[1].textContent).toBe('[1,{"a":null}]');
    expect(screen.queryByText("The stored record names no experiment.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Hide answer" }));
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.queryByText(/^Tool:/)).toBeNull();
  });

  it("shows the tool alone", async () => {
    await open({ report: { experiment: { tool: "evaluate_rules" } } });
    expect(screen.getByText("Tool: evaluate_rules")).toBeTruthy();
    expect(screen.queryByText(/^Template:/)).toBeNull();
    expect(screen.queryByText(/^Arguments:/)).toBeNull();
  });

  it.each([["failed"], [true], [undefined], [null]])("does not say the numbers were checked when grounding is %j", async (grounding) => {
    await open({ report: { ...REPORT, grounding } });
    expect(screen.queryByText(/checked against this run/)).toBeNull();
    expect(screen.getByText("Template: rule_compare")).toBeTruthy();
  });

  it("renders a marker in the answer and the record as text", async () => {
    const { container } = await open({ answer: "<b>a</b>", report: { grounding: "ok", note: "<b>r</b>", experiment: { template: "<b>t</b>", tool: "<i>x</i>", args: "<u>y</u>" } } });
    expect(container.querySelector("main b, main i, main u, main a[href^='http']")).toBeNull();
    expect(screen.getByText("Template: <b>t</b>")).toBeTruthy();
    expect(screen.getByRole("region", { name: /Stored record/ }).textContent).toContain("<b>r</b>");
  });

  it("says a failed read in one line and can try again", async () => {
    api.getLabQuestions.mockResolvedValue([item({ id: 9 })]);
    api.getLabQuestion.mockRejectedValueOnce(new MockApiError(404)).mockResolvedValue(detail({ id: 9 }));
    page();
    await settle();
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.getByRole("alert").textContent).toBe("This ward is not on this server.");
    await userEvent.click(screen.getByRole("button", { name: "Show answer" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Hide answer" })).toBeTruthy();
  });

  it("has a button only for an answered question", async () => {
    api.getLabQuestions.mockResolvedValue([item({ state: "queued" }), item({ id: 2, state: "failed" })]);
    page();
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
    page();
    await tick(0);
  };
  afterEach(() => vi.useRealTimers());

  it("reads every 5 s while a question is open, then stops when all are final", async () => {
    api.getLabQuestions.mockResolvedValueOnce(open).mockResolvedValue(done);
    await start();
    await tick(5_000);
    expect(screen.getByText(/Answered/)).toBeTruthy();
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(2);
  });

  it("starts no timer when none is open", async () => {
    api.getLabQuestions.mockResolvedValue(done);
    await start();
    await tick(60_000);
    expect(api.getLabQuestions).toHaveBeenCalledTimes(1);
  });

  it("does not let a read that answers late replace a newer list", async () => {
    let late: (v: QuestionItem[]) => void = () => {};
    const theirs = [item({ id: 1, state: "queued", requested_by: "rn-manalo" })];
    const both = [item({ id: 2, state: "queued", question: "Mine?" }), ...theirs];
    api.getLabQuestions.mockResolvedValueOnce(theirs).mockReturnValueOnce(new Promise((r) => { late = r; })).mockResolvedValue(both);
    api.askLab.mockResolvedValue(both[0]);
    await start();
    await tick(5_000); // the timed read is in flight
    fireEvent.change(input(), { target: { value: "Mine?" } });
    fireEvent.click(ask());
    await tick(0);
    expect(screen.getByText("Mine?")).toBeTruthy();
    late(theirs);
    await tick(0);
    expect(screen.getByText("Mine?")).toBeTruthy();
    expect(ask().disabled).toBe(true);
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
});
