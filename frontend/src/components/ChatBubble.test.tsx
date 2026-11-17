// @vitest-environment jsdom
// The assistant's panel, opened from the top bar's Assistant button (the real AppShell) and from a patient page's own button.
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session, SessionRole } from "../types";
import { AppShell } from "./AppShell";
import { ChatBubble } from "./ChatBubble";
import { OPEN_CHAT, openAssistant } from "./roles";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ hasLabQuestions: true, hasPatientQuestions: true }));
vi.mock("../api", () => api);
// The thread has its own tests; here it is a stand-in that says which thread it is and holds one tab stop.
vi.mock("./Thread", () => ({
  Thread: ({ scope }: { scope: { kind: string; id?: string } }) => <button type="button">thread {scope.id ?? scope.kind}</button>,
}));

const Where = () => {
  const navigate = useNavigate();
  return (
    <>
      <p data-testid="where">{useLocation().pathname}</p>
      <button type="button" onClick={() => navigate(-1)}>back</button>
      {/* A stand-in for the patient page's header button. */}
      <button type="button" onClick={(e) => openAssistant(e.currentTarget)}>from the page</button>
    </>
  );
};
const at = (path: string, role: SessionRole = "md") => {
  const session: Session = { clinician_id: "md-santos", ward: "W4", role };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ChatBubble session={session} onSignOut={vi.fn()}>
        <AppShell session={session} onSignOut={vi.fn()}>
          <Where />
        </AppShell>
      </ChatBubble>
    </MemoryRouter>,
  );
};
// The top bar's trigger.
const bubble = () => screen.getByRole("button", { name: "Assistant" });
const noTrigger = () => expect(screen.queryByRole("button", { name: "Assistant" })).toBeNull();

afterEach(() => {
  cleanup();
  api.hasLabQuestions = true;
  api.hasPatientQuestions = true;
});

describe("where the top bar's Assistant button shows", () => {
  it("shows for a physician and a charge nurse on a signed-in page, closed, in the bar", () => {
    at("/ward");
    expect(bubble().getAttribute("aria-expanded")).toBe("false");
    expect(bubble().getAttribute("aria-haspopup")).toBe("dialog");
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Assistant" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    cleanup();
    at("/admit", "charge_rn");
    expect(bubble()).toBeTruthy();
  });

  it("is absent at sign-in, on the Ask pages, and for an administrator", () => {
    for (const [path, role] of [["/", "md"], ["/ask", "md"], ["/ask/patient/p-001", "md"], ["/ward", "admin"], ["/patient/p-001", "admin"]] as const) {
      at(path, role);
      noTrigger();
      cleanup();
    }
  });

  it("shows for a staff nurse on a patient's page only", () => {
    at("/ward", "rn");
    noTrigger();
    cleanup();
    at("/patient/p-001", "rn");
    expect(bubble()).toBeTruthy();
  });

  it("is not shown where the build has no chat", () => {
    api.hasLabQuestions = false;
    at("/ward");
    noTrigger();
    cleanup();
    api.hasPatientQuestions = false;
    at("/patient/p-001");
    noTrigger();
  });

  it("has no floating button left", () => {
    at("/ward");
    expect(screen.queryByRole("button", { name: "Ask" })).toBeNull();
  });
});

describe("the panel", () => {
  it("opens the ward's thread as a labelled dialog and moves the focus into it", async () => {
    at("/ward");
    await userEvent.click(bubble());
    const dialog = screen.getByRole("dialog", { name: "Assistant" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(within(dialog).getByText("The ward")).toBeTruthy();
    expect(dialog.getAttribute("aria-describedby")).toBe(within(dialog).getByText("The ward").id);
    expect(screen.getByRole("button", { name: "thread ward" })).toBeTruthy();
    expect(bubble().getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close the chat" }));
  });

  it("opens that patient's thread on a patient's page", async () => {
    at("/patient/p-001");
    await userEvent.click(bubble());
    expect(within(screen.getByRole("dialog", { name: "Assistant" })).getByText("This patient")).toBeTruthy();
    expect(screen.getByRole("button", { name: "thread p-001" })).toBeTruthy();
  });

  it("closes by Escape, the close button, the bar's button and a click outside, and gives the focus back to the bar's button", async () => {
    at("/ward");
    const closers = [
      () => userEvent.keyboard("{Escape}"),
      () => userEvent.click(screen.getByRole("button", { name: "Close the chat" })),
      () => userEvent.click(bubble()),
      () => userEvent.click(screen.getByTestId("chat-backdrop")),
    ];
    for (const closeIt of closers) {
      await userEvent.click(bubble());
      expect(screen.getByRole("dialog")).toBeTruthy();
      await closeIt();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(bubble());
    }
  });

  it("gives the focus back to whichever control opened it: the page's own button", async () => {
    at("/patient/p-001", "rn");
    const own = screen.getByRole("button", { name: "from the page" });
    await userEvent.click(own);
    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeTruthy();
    expect(bubble().getAttribute("aria-expanded")).toBe("true");
    await userEvent.keyboard("{Escape}");
    expect(document.activeElement).toBe(own);
    await userEvent.click(own);
    await userEvent.click(screen.getByTestId("chat-backdrop"));
    expect(document.activeElement).toBe(own);
  });

  it("keeps Tab and Shift+Tab inside the panel", async () => {
    at("/ward");
    await userEvent.click(bubble());
    const close = screen.getByRole("button", { name: "Close the chat" });
    const thread = screen.getByRole("button", { name: "thread ward" });
    await userEvent.tab({ shift: true }); // from the first stop back to the last
    expect(document.activeElement).toBe(thread);
    await userEvent.tab(); // from the last stop on to the first
    expect(document.activeElement).toBe(close);
    await userEvent.tab(); // in the middle the browser's own order applies
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Open as a page" }));
  });

  it("links to the same thread as a page, and closes on the way", async () => {
    at("/patient/p-001");
    await userEvent.click(bubble());
    await userEvent.click(screen.getByRole("link", { name: "Open as a page" }));
    expect(screen.getByTestId("where").textContent).toBe("/ask/patient/p-001");
    expect(screen.queryByRole("dialog")).toBeNull();
    cleanup();
    at("/ward");
    await userEvent.click(bubble());
    expect(screen.getByRole("link", { name: "Open as a page" }).getAttribute("href")).toBe("/ask");
  });

  it("gives a staff nurse the patient's thread with no link to the Ask page", async () => {
    at("/patient/p-001", "rn");
    await userEvent.click(bubble());
    expect(screen.getByRole("button", { name: "thread p-001" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open as a page" })).toBeNull();
  });
});

describe("after the independent review", () => {
  it("reads the patient from the route: a trailing slash and an encoded id are the same patient page", async () => {
    at("/patient/p%20001/", "rn");
    await userEvent.click(bubble());
    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "thread p 001" })).toBeTruthy();
  });

  it("closes by Escape when the focus has dropped out of the panel", async () => {
    at("/ward");
    await userEvent.click(bubble());
    act(() => (document.activeElement as HTMLElement).blur());
    expect(document.activeElement).toBe(document.body);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(bubble());
  });

  it("brings Tab and Shift+Tab back into the panel when the focus has dropped out of it", async () => {
    at("/ward");
    await userEvent.click(bubble());
    act(() => (document.activeElement as HTMLElement).blur());
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close the chat" }));
    act(() => (document.activeElement as HTMLElement).blur());
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "thread ward" }));
  });

  it("does not reopen on the way back to the page it was opened on", async () => {
    at("/ward");
    await userEvent.click(bubble());
    await userEvent.click(screen.getByRole("link", { name: "Open as a page" }));
    expect(screen.getByTestId("where").textContent).toBe("/ask");
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(screen.getByTestId("where").textContent).toBe("/ward");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(bubble().getAttribute("aria-expanded")).toBe("false");
  });

  it("opens when the patient page's own button asks, and only where the assistant is shown", async () => {
    at("/patient/p-001", "rn");
    act(() => void window.dispatchEvent(new Event(OPEN_CHAT)));
    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeTruthy();
    cleanup();
    at("/patient/p-001", "admin");
    act(() => void window.dispatchEvent(new Event(OPEN_CHAT)));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
