// @vitest-environment jsdom
/**
 * The app shell: the bar names the page and carries its status, the page links and the account sit
 * beside the content, and on a phone the menu button opens the page links in a drawer that Esc, the
 * backdrop, its Close button and a link (which changes the page) all close.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

const shell = () =>
  render(
    <MemoryRouter initialEntries={["/ward"]}>
      <AppShell
        session={{ clinician_id: "md-aquino", ward: "W4", role: "md" }}
        onSignOut={vi.fn()}
        title="Ward W4"
        status={<span>Updated 09:54</span>}
      >
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </AppShell>
    </MemoryRouter>,
  );

const drawer = () => screen.queryByRole("dialog", { name: "Main menu" });
const openDrawer = () => userEvent.click(screen.getByRole("button", { name: "Open menu" }));

afterEach(cleanup);

describe("AppShell", () => {
  it("puts the title and status in the bar, the content in main, and credits the typeface", () => {
    shell();
    const bar = screen.getByRole("banner");
    expect(within(bar).getByText("Ward W4")).toBeTruthy();
    expect(within(bar).getByText("Updated 09:54")).toBeTruthy();
    expect(within(screen.getByRole("main")).getByText("at /ward")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Pages" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /md-aquino/ })).toBeTruthy();
    expect(screen.getByText("Set in HarmonyOS Sans")).toBeTruthy();
  });

  it("leaves the bar without a title when the page gives none", () => {
    render(
      <MemoryRouter>
        <AppShell session={{ clinician_id: "x", ward: "W4", role: "rn" }} onSignOut={vi.fn()}>
          <p>content</p>
        </AppShell>
      </MemoryRouter>,
    );
    expect(screen.getByRole("banner").textContent).toBe("");
  });

  it("opens the page links in a drawer, and its Close button closes it", async () => {
    shell();
    expect(drawer()).toBeNull();
    await openDrawer();
    const d = drawer()!;
    expect((d as HTMLDialogElement).open).toBe(true);
    expect(screen.getByRole("button", { name: "Open menu" }).getAttribute("aria-expanded")).toBe("true");
    expect(within(d).getByRole("navigation", { name: "Pages" })).toBeTruthy();
    await userEvent.click(within(d).getByRole("button", { name: "Close menu" }));
    await waitFor(() => expect(drawer()).toBeNull());
  });

  it("closes the drawer on Esc and on a press on the backdrop, not on a press inside it", async () => {
    shell();
    await openDrawer();
    const cancel = new Event("cancel", { cancelable: true });
    fireEvent(drawer()!, cancel);
    expect(cancel.defaultPrevented).toBe(true); // the shell animates the close itself
    await waitFor(() => expect(drawer()).toBeNull());

    await openDrawer();
    await userEvent.click(within(drawer()!).getByRole("navigation", { name: "Pages" }));
    expect(drawer()).not.toBeNull();
    await userEvent.click(drawer()!); // the dialog element itself is the backdrop
    await waitFor(() => expect(drawer()).toBeNull());
  });

  it("closes the drawer when one of its links changes the page", async () => {
    shell();
    await openDrawer();
    await userEvent.click(within(drawer()!).getByRole("link", { name: "Station" }));
    expect(screen.getByText("at /station")).toBeTruthy();
    await waitFor(() => expect(drawer()).toBeNull());
  });
});
