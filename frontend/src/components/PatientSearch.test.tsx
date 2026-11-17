// @vitest-environment jsdom
/** The patient search: `/` focuses it, results in rank order with NOT RANKED last, at most six, keys to move and open. */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import synthetic from "../data/synthetic.json";
import type { WardResponse } from "../types";
import { PatientSearch } from "./PatientSearch";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(cleanup);

function Where() {
  return <p>at {useLocation().pathname}</p>;
}
const ward = { ward: "W4", ranked: synthetic.ranked, abstained: synthetic.abstained } as unknown as WardResponse;
const search = () => {
  render(
    <MemoryRouter initialEntries={["/ward"]}>
      <input aria-label="Another field" />
      <PatientSearch ward={ward} />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return screen.getByRole("combobox", { name: "Find a patient by name or bed" }) as HTMLInputElement;
};
const options = () => within(screen.getByRole("listbox", { name: "Patients in rank order" })).getAllByRole("option");
const names = () => options().map((o) => o.querySelector(".hw-search__name")?.textContent);

describe("PatientSearch", () => {
  it("is focused by / from outside a field, and not from inside one", () => {
    const box = search();
    const other = screen.getByRole("textbox", { name: "Another field" });
    other.focus();
    fireEvent.keyDown(other, { key: "/" });
    expect(document.activeElement).toBe(other);
    fireEvent.keyDown(document.body, { key: "/", ctrlKey: true });
    expect(document.activeElement).toBe(other);
    fireEvent.keyDown(document.body, { key: "/" });
    expect(document.activeElement).toBe(box);
  });

  it("matches a name or a bed, ranked patients in rank order, NOT RANKED last, at most six", async () => {
    const box = search();
    expect(box.getAttribute("aria-expanded")).toBe("false");
    await userEvent.type(box, "a");
    expect(box.getAttribute("aria-expanded")).toBe("true");
    expect(options()).toHaveLength(6);
    expect(options()[0].textContent).toBe("#1Santos, Maria67 F · Bed 4A9 High");
    expect(options()[0].querySelector(".hw-tag")!.className).toBe("hw-tag hw-tag--emergency hw-tag--round");
    await userEvent.clear(box);
    await userEvent.type(box, "4");
    expect(names()).toEqual(["Santos, Maria", "Flores, Ramon"]); // bed 4A ranked, bed 4B NOT RANKED
    expect(options()[1].querySelector(".hw-tag--dashed")!.textContent).toBe("NOT RANKED");
  });

  it("moves the active result with Up and Down, and Enter opens it", async () => {
    const box = search();
    await userEvent.type(box, "4");
    expect(box.getAttribute("aria-activedescendant")).toBe(options()[0].id);
    await userEvent.keyboard("{ArrowDown}{ArrowDown}"); // stops at the last
    expect(options()[1].getAttribute("aria-selected")).toBe("true");
    expect(box.getAttribute("aria-activedescendant")).toBe(options()[1].id);
    await userEvent.keyboard("{ArrowUp}{ArrowUp}"); // and at the first
    expect(options()[0].getAttribute("aria-selected")).toBe("true");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(screen.getByText("at /patient/p-004")).toBeTruthy();
    expect(box.value).toBe("");
  });

  it("opens a result on a click", async () => {
    const box = search();
    await userEvent.type(box, "Reyes");
    await userEvent.click(options()[0]);
    expect(screen.getByText("at /patient/p-002")).toBeTruthy();
  });

  it("says when no patient matches, and Enter then does nothing", async () => {
    const box = search();
    await userEvent.type(box, "zz{Enter}");
    expect(screen.getByRole("status").textContent).toBe("No patient on Ward W4 matches. Try a surname or a bed.");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(box.getAttribute("aria-expanded")).toBe("false");
    expect(box.hasAttribute("aria-activedescendant")).toBe(false);
    expect(screen.getByText("at /ward")).toBeTruthy();
  });

  it("clears on Esc, then leaves the field on a second Esc, and hides the results when it loses focus", async () => {
    const box = search();
    await userEvent.type(box, "Reyes");
    await userEvent.keyboard("{Escape}");
    expect(box.value).toBe("");
    expect(document.activeElement).toBe(box);
    await userEvent.keyboard("{Escape}");
    expect(document.activeElement).not.toBe(box);
    await userEvent.type(box, "Reyes");
    await userEvent.click(screen.getByRole("textbox", { name: "Another field" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
