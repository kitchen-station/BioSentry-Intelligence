// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LabFrame, SimulatedNote } from "./LabFrame";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const phone = () =>
  vi.spyOn(window, "matchMedia").mockImplementation((q) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList);

describe("LabFrame", () => {
  const frame = () =>
    render(
      <MemoryRouter initialEntries={["/lab"]}>
        <LabFrame session={{ clinician_id: "admin-ruiz", ward: "W4", role: "admin" }} onSignOut={() => {}}>
          <p>inside</p>
        </LabFrame>
      </MemoryRouter>,
    );

  it("carries the standing label, the way back and the page", () => {
    frame();
    expect(screen.getByRole("note").textContent).toBe("Simulation lab: not the ward. Nothing here changes a patient's rank.");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Ward" }).getAttribute("href")).toBe("/ward");
    expect(screen.getByText("inside")).toBeTruthy();
  });

  it("names the lab in the phone's app bar", () => {
    phone();
    frame();
    expect(within(screen.getByRole("banner")).getByText("Simulation lab")).toBeTruthy();
  });
});

describe("LabFrame, the clinician's wording", () => {
  it("names What-if runs and says the trials are simulated", () => {
    phone();
    render(
      <MemoryRouter initialEntries={["/what-if"]}>
        <LabFrame whatIf session={{ clinician_id: "md-santos", ward: "W4", role: "md" }} onSignOut={() => {}}>
          <p>inside</p>
        </LabFrame>
      </MemoryRouter>,
    );
    expect(within(screen.getByRole("banner")).getByText("What-if runs")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toBe("Simulated trials only. Nothing here changes a patient's rank.");
    expect(screen.queryByText(/Simulation lab/)).toBeNull();
  });
});

describe("SimulatedNote", () => {
  it("says the patients are simulated", () => {
    render(<SimulatedNote />);
    expect(screen.getByRole("note").textContent).toBe("Simulated patients until a hospital is connected.");
  });
});
