// @vitest-environment jsdom
/**
 * Sign-in: department, then profile, then passphrase, then destination. The session handed up
 * names the profile's clinician, ward and role; only a physician or a charge nurse is offered the
 * station; Back steps one phase at a time; a signed-out link is returned to after sign-in.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SignIn } from "./SignIn";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Where() {
  return <p>at {useLocation().pathname}</p>;
}

const page = (from?: unknown) => {
  const onSignIn = vi.fn();
  render(
    <MemoryRouter initialEntries={[{ pathname: "/", state: from === undefined ? null : { from } }]}>
      <Routes>
        <Route path="/" element={<SignIn onSignIn={onSignIn} />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return onSignIn;
};

const click = (name: RegExp | string) => userEvent.click(screen.getByRole("button", { name }));

afterEach(cleanup);

describe("SignIn", () => {
  it("a staff nurse signs in to the ward and is not offered the station", async () => {
    const onSignIn = page();
    await click(/Nurse/);
    expect(screen.getByText("Select nurse")).toBeTruthy();
    expect(screen.queryByText("Dr. Santos")).toBeNull(); // physicians are filtered out
    await click(/Nurse Reyes/);
    const passphrase = screen.getByLabelText("Passphrase") as HTMLInputElement;
    await userEvent.clear(passphrase);
    await userEvent.type(passphrase, "hunter2");
    expect(passphrase.value).toBe("hunter2");
    await click("Continue");
    expect(screen.getByText("Choose destination")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Station/ })).toBeNull();
    await click(/Ward Monitor/);
    expect(onSignIn).toHaveBeenCalledWith({ clinician_id: "rn-reyes", ward: "W4", role: "rn" });
    expect(screen.getByText("at /ward")).toBeTruthy();
  });

  it("a physician can go to the station", async () => {
    const onSignIn = page();
    await click(/Physician/);
    expect(screen.getByText("Select physician")).toBeTruthy();
    await click(/Dr. Aquino/);
    await click("Continue");
    await click(/Station/);
    expect(onSignIn).toHaveBeenCalledWith({ clinician_id: "md-aquino", ward: "W4", role: "md" });
    expect(screen.getByText("at /station")).toBeTruthy();
  });

  it("a charge nurse is offered the station too", async () => {
    page();
    await click(/Nurse/);
    await click(/Nurse Manalo/);
    await click("Continue");
    expect(screen.getByRole("button", { name: /Station/ })).toBeTruthy();
  });

  it("Back steps one phase at a time, and is not shown on the first", async () => {
    page();
    expect(screen.queryByRole("button", { name: /Back/ })).toBeNull();
    await click(/Physician/);
    await click(/Dr. Santos/);
    await click("Continue");
    await click(/Back/); // destination -> passphrase, profile kept
    expect(screen.getByLabelText("Passphrase")).toBeTruthy();
    expect(screen.getByText("Dr. Santos")).toBeTruthy();
    await click(/Back/); // passphrase -> profile
    expect(screen.getByText("Select physician")).toBeTruthy();
    await click(/Back/); // profile -> department
    expect(screen.getByText("I am a")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Back/ })).toBeNull();
  });

  it("returns a clinician to the page a signed-out link was for", async () => {
    page("/patient/p-001");
    await click(/Nurse/);
    await click(/Nurse Reyes/);
    await click("Continue");
    await click(/Ward Monitor/);
    expect(screen.getByText("at /patient/p-001")).toBeTruthy();
  });

  it.each([["/"], ["https://evil.example"], [42]])(
    "ignores a return target that is not an in-app path (%s)",
    async (from) => {
      page(from);
      await click(/Nurse/);
      await click(/Nurse Reyes/);
      await click("Continue");
      await click(/Ward Monitor/);
      expect(screen.getByText("at /ward")).toBeTruthy();
    },
  );
});
