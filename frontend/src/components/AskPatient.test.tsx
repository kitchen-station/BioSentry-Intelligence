// @vitest-environment jsdom
// The page's section is a heading and one link to the patient's thread on the Ask page.
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { AskPatient } from "./AskPatient";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(cleanup);

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

describe("the section", () => {
  it("has its heading and a link that opens the patient's thread, and holds no thread", async () => {
    render(
      <MemoryRouter initialEntries={["/patient/p 1"]}>
        <AskPatient patientId="p 1" />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Ask about this patient" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "Open the chat" }));
    expect(screen.getByText("at /ask/patient/p%201")).toBeTruthy();
  });
});
