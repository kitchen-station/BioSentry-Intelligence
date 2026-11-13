// @vitest-environment jsdom
/**
 * Sign-in: department, then profile, then passphrase, then destination. The session handed up
 * names the profile's clinician, ward and role; only a physician or a charge nurse is offered the
 * station; Back steps one phase at a time; a signed-out link is returned to after sign-in.
 * With the API the passphrase is sent and the page waits for the answer: a refusal (401, 429) is
 * said plainly and stays on the passphrase step. The public demo has no server and says so.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { SignIn } from "./SignIn";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ usesServer: false, signIn: vi.fn(), clearSession: vi.fn() }));
vi.mock("../api", async () => ({
  ApiError: (await vi.importActual<typeof import("../api/client")>("../api/client")).ApiError,
  get usesServer() {
    return api.usesServer;
  },
  signIn: api.signIn,
  clearSession: api.clearSession,
}));

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

const click = async (name: RegExp | string) =>
  userEvent.click(await screen.findByRole("button", { name })); // a sign-in answers asynchronously

beforeEach(() => {
  api.usesServer = false;
  api.signIn.mockReset().mockResolvedValue(undefined);
});
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
    expect(api.signIn).toHaveBeenCalledWith("rn-reyes", "hunter2");
    expect(await screen.findByText("Choose destination")).toBeTruthy();
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

describe("the public demo (no server)", () => {
  it("prefills a passphrase and says plainly that nothing is checked", async () => {
    page();
    await click(/Nurse/);
    await click(/Nurse Reyes/);
    expect((screen.getByLabelText("Passphrase") as HTMLInputElement).value).toBe("anything");
    expect(screen.getByText(/Public demo: there is no server and no real sign-in/)).toBeTruthy();
    expect(screen.getByText(/Any passphrase is accepted/)).toBeTruthy();
    expect(screen.getByText(/Public demo: synthetic data, no server, no real sign-in/)).toBeTruthy();
    expect(screen.queryByText(/hospital identity provider/)).toBeNull();
    await click("Continue");
    expect(api.signIn).toHaveBeenCalledWith("rn-reyes", "anything");
  });

  it("does not require a passphrase", async () => {
    page();
    await click(/Nurse/);
    await click(/Nurse Reyes/);
    expect((screen.getByLabelText("Passphrase") as HTMLInputElement).required).toBe(false);
  });
});

describe("with the API", () => {
  beforeEach(() => {
    api.usesServer = true;
  });

  const toPassphrase = async () => {
    const onSignIn = page();
    await click(/Nurse/);
    await click(/Nurse Reyes/);
    return onSignIn;
  };
  const type = (text: string) => userEvent.type(screen.getByLabelText("Passphrase"), text);

  it("starts empty and required, capped like the server, and does not claim any passphrase works", async () => {
    await toPassphrase();
    const input = screen.getByLabelText("Passphrase") as HTMLInputElement;
    expect([input.value, input.required, input.maxLength]).toEqual(["", true, 256]);
    expect(screen.queryByText(/anything/i)).toBeNull();
    expect(screen.queryByText(/accepts any passphrase/i)).toBeNull();
    expect(screen.getByText(/The ward server checks your passphrase/)).toBeTruthy();
    expect(screen.getByText(/Pilot build: synthetic data only/)).toBeTruthy();
  });

  it("sends the passphrase, waits for the answer, then offers the destinations", async () => {
    let answer!: () => void;
    api.signIn.mockReturnValue(new Promise<void>((r) => (answer = r)));
    await toPassphrase();
    await type("a long passphrase");
    await click("Continue");
    expect(api.signIn).toHaveBeenCalledWith("rn-reyes", "a long passphrase");
    const waiting = screen.getByRole("button", { name: "Signing in" }) as HTMLButtonElement;
    expect(waiting.disabled).toBe(true);
    expect(screen.queryByText("Choose destination")).toBeNull(); // not before the server says so
    answer();
    expect(await screen.findByText("Choose destination")).toBeTruthy();
  });

  it("does not submit twice while waiting", async () => {
    api.signIn.mockReturnValue(new Promise<void>(() => undefined));
    await toPassphrase();
    await type("a long passphrase{Enter}{Enter}");
    expect(api.signIn).toHaveBeenCalledOnce();
  });

  it("says a 401 plainly, stays on the passphrase step and empties the field", async () => {
    api.signIn.mockRejectedValue(new ApiError(401, "Unauthorized", { detail: "Sign-in failed" }));
    const onSignIn = await toPassphrase();
    await type("wrong passphrase");
    await click("Continue");
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Sign-in failed\./);
    expect(screen.queryByText("Choose destination")).toBeNull();
    expect((screen.getByLabelText("Passphrase") as HTMLInputElement).value).toBe("");
    expect(onSignIn).not.toHaveBeenCalled();
  });

  it("says a 429 plainly: locked for 15 minutes", async () => {
    api.signIn.mockRejectedValue(new ApiError(429, "Too Many Requests", { detail: "no" }));
    await toPassphrase();
    await type("whatever it was");
    await click("Continue");
    expect((await screen.findByRole("alert")).textContent).toMatch(/Too many failed attempts.*15 minutes/);
    expect(screen.queryByText("Choose destination")).toBeNull();
  });

  it("says when the server cannot be reached, and clears the message once Back is used", async () => {
    api.signIn.mockRejectedValue(new TypeError("Failed to fetch"));
    await toPassphrase();
    await type("whatever it was");
    await click("Continue");
    expect((await screen.findByRole("alert")).textContent).toMatch(/Cannot reach the ward server/);
    await click(/Back/);
    await click(/Nurse Reyes/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("can try again after a refusal", async () => {
    api.signIn.mockRejectedValueOnce(new ApiError(401, "Unauthorized", {}));
    await toPassphrase();
    await type("first try");
    await click("Continue");
    await screen.findByRole("alert");
    await type("second try");
    await click("Continue");
    expect(await screen.findByText("Choose destination")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.signIn).toHaveBeenLastCalledWith("rn-reyes", "second try");
  });

  it("signs in and goes to the overview when the server answers admin for a nurse card", async () => {
    api.signIn.mockResolvedValue("admin");
    const onSignIn = await toPassphrase();
    await type("a long passphrase");
    await click("Continue");
    expect(await screen.findByText("at /overview")).toBeTruthy();
    expect(onSignIn).toHaveBeenCalledWith({ clinician_id: "rn-reyes", ward: "W4", role: "admin" });
    expect(screen.queryByText("Choose destination")).toBeNull();
  });

  it("labels the destination step with the server's role, not the card's", async () => {
    api.signIn.mockResolvedValue("charge_rn");
    await toPassphrase();
    await type("a long passphrase");
    await click("Continue");
    await screen.findByText("Choose destination");
    expect(screen.getByText(/Charge Nurse/)).toBeTruthy();
    expect(screen.queryByText(/Staff Nurse/)).toBeNull();
  });

  describe("administration", () => {
    const toAdmin = async () => {
      const onSignIn = page();
      await click(/Administration/);
      await userEvent.type(screen.getByLabelText("Administrator id"), "admin-ruiz");
      await userEvent.type(screen.getByLabelText("Passphrase"), "a long passphrase");
      return onSignIn;
    };

    it("is not offered in the public demo", async () => {
      api.usesServer = false;
      page();
      await screen.findByText("I am a");
      expect(screen.queryByRole("button", { name: /Administration/ })).toBeNull();
    });

    it("asks for an id and passphrase, with no profile card, and lands on the overview", async () => {
      api.signIn.mockResolvedValue("admin");
      const onSignIn = await toAdmin();
      await click("Continue");
      expect(api.signIn).toHaveBeenCalledWith("admin-ruiz", "a long passphrase");
      expect(onSignIn).toHaveBeenCalledWith({ clinician_id: "admin-ruiz", ward: "W4", role: "admin" });
      expect(await screen.findByText("at /overview")).toBeTruthy();
    });

    it("refuses any other role from the server and clears the session", async () => {
      api.signIn.mockResolvedValue("rn");
      const onSignIn = await toAdmin();
      await click("Continue");
      expect((await screen.findByRole("alert")).textContent).toBe(
        "This account is not an administrator. Sign in from Nursing or Medical.",
      );
      expect(api.clearSession).toHaveBeenCalledOnce();
      expect(onSignIn).not.toHaveBeenCalled();
    });

    it("says a refused sign-in plainly, and Back returns to the departments", async () => {
      api.signIn.mockRejectedValue(new ApiError(401, "Unauthorized", {}));
      await toAdmin();
      await click("Continue");
      expect((await screen.findByRole("alert")).textContent).toMatch(/Sign-in failed/);
      await click(/Back/);
      expect(await screen.findByText("I am a")).toBeTruthy();
    });
  });

  it("trusts the server's role over the card's", async () => {
    api.signIn.mockResolvedValue("charge_rn");
    const onSignIn = await toPassphrase();
    await type("a long passphrase");
    await click("Continue");
    expect(await screen.findByRole("button", { name: /Station/ })).toBeTruthy();
    await click(/Ward Monitor/);
    expect(onSignIn).toHaveBeenCalledWith({ clinician_id: "rn-reyes", ward: "W4", role: "charge_rn" });
  });
});
