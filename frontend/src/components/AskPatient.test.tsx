// @vitest-environment jsdom
// The page's section is a heading and one button; the thread is in the chat bubble's panel (see ChatBubble.test.tsx).
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskPatient } from "./AskPatient";
import { OPEN_CHAT } from "./roles";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(cleanup);

describe("the section", () => {
  it("has its heading and a button that asks the chat panel to open, and holds no thread", async () => {
    const opened = vi.fn();
    window.addEventListener(OPEN_CHAT, opened);
    try {
      render(<AskPatient />);
      expect(screen.getByRole("heading", { level: 2, name: "Ask about this patient" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
      await userEvent.click(screen.getByRole("button", { name: "Open the chat" }));
      expect(opened).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(OPEN_CHAT, opened);
    }
  });
});
