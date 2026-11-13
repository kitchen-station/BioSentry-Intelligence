// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { LabFrame, SimulatedNote } from "./LabFrame";

afterEach(cleanup);

describe("LabFrame", () => {
  it("names the lab, carries the standing label, the way back and the page", () => {
    render(
      <MemoryRouter initialEntries={["/lab"]}>
        <LabFrame session={{ clinician_id: "rn-reyes", ward: "W4", role: "rn" }} onSignOut={() => {}}>
          <p>inside</p>
        </LabFrame>
      </MemoryRouter>,
    );
    expect(screen.getByText(/BioSentry . Simulation lab/)).toBeTruthy();
    expect(screen.getByRole("note").textContent).toBe("Simulation lab: not the ward. Nothing here changes a patient's rank.");
    expect(screen.getByRole("link", { name: "Back to the ward" }).getAttribute("href")).toBe("/ward");
    expect(screen.getByText("inside")).toBeTruthy();
  });
});

describe("SimulatedNote", () => {
  it("says the patients are simulated", () => {
    render(<SimulatedNote />);
    expect(screen.getByRole("note").textContent).toBe("Simulated patients until a hospital is connected.");
  });
});
