// @vitest-environment jsdom
/**
 * App: the session survives a reload only when what is stored is well formed, the route guards
 * send a signed-out visitor to sign-in (remembering where they were going) and keep a staff
 * nurse out of the station, and signing out clears the session everywhere.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "./types";
import { App } from "./App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ initSession: vi.fn(), clearSession: vi.fn() }));
vi.mock("./api", () => api);

// The pages are stand-ins: these tests are about the shell, not what each page shows.
vi.mock("./pages/SignIn", () => ({
  SignIn: ({ onSignIn }: { onSignIn: (s: Session) => void }) => {
    const from = (useLocation().state as { from?: string } | null)?.from;
    return (
      <div>
        <p>sign-in page{from ? ` from ${from}` : ""}</p>
        <button
          type="button"
          onClick={() => onSignIn({ clinician_id: "md-santos", ward: "W4", role: "md" })}
        >
          sign in
        </button>
      </div>
    );
  },
}));
vi.mock("./pages/Ward", () => ({
  Ward: ({ session, onSignOut }: { session: Session; onSignOut: () => void }) => (
    <div>
      <p>ward page for {session.clinician_id}</p>
      <button type="button" onClick={onSignOut}>
        sign out
      </button>
    </div>
  ),
}));
vi.mock("./pages/PatientDetail", () => ({
  PatientDetail: ({ session }: { session: Session }) => <p>patient page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Station", () => ({
  Station: ({ session, onSignOut }: { session: Session; onSignOut: () => void }) => (
    <div>
      <p>station page for {session.clinician_id}</p>
      <button type="button" onClick={onSignOut}>
        sign out
      </button>
    </div>
  ),
}));

const KEY = "biosentry.session";
const nurse: Session = { clinician_id: "rn-reyes", ward: "W4", role: "rn" };
const charge: Session = { clinician_id: "rn-manalo", ward: "W4", role: "charge_rn" };

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
const store = (value: unknown) => sessionStorage.setItem(KEY, JSON.stringify(value));

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("restoring the session", () => {
  it("restores a stored session and re-arms the API with it", () => {
    store(nurse);
    at("/ward");
    expect(screen.getByText("ward page for rn-reyes")).toBeTruthy();
    expect(api.initSession).toHaveBeenCalledWith("rn-reyes");
  });

  it.each([
    ["malformed JSON", "{not json"],
    ["an unknown role", JSON.stringify({ ...nurse, role: "admin" })],
    ["a missing ward", JSON.stringify({ clinician_id: "rn-reyes", role: "rn" })],
    ["a non-string clinician id", JSON.stringify({ ...nurse, clinician_id: 7 })],
    ["null", "null"],
  ])("treats %s as signed out", (_, raw) => {
    sessionStorage.setItem(KEY, raw);
    at("/ward");
    expect(screen.getByText("sign-in page from /ward")).toBeTruthy();
    expect(api.initSession).not.toHaveBeenCalled();
  });

  it("treats unreadable storage as signed out", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    at("/ward");
    expect(screen.getByText("sign-in page from /ward")).toBeTruthy();
  });
});

describe("route guards", () => {
  it.each(["/ward", "/patient/p-001", "/station"])(
    "sends a signed-out visitor from %s to sign-in, remembering where they were going",
    (path) => {
      at(path);
      expect(screen.getByText(`sign-in page from ${path}`)).toBeTruthy();
    },
  );

  it("shows a patient to a signed-in clinician", () => {
    store(nurse);
    at("/patient/p-001");
    expect(screen.getByText("patient page for rn-reyes")).toBeTruthy();
  });

  it("keeps a staff nurse out of the station: back to the ward", () => {
    store(nurse);
    at("/station");
    expect(screen.getByText("ward page for rn-reyes")).toBeTruthy();
  });

  it.each([charge, { clinician_id: "md-santos", ward: "W4", role: "md" } as Session])(
    "lets $role into the station",
    (s) => {
      store(s);
      at("/station");
      expect(screen.getByText(`station page for ${s.clinician_id}`)).toBeTruthy();
    },
  );
});

describe("signing in and out", () => {
  it("signs in: arms the API, stores the session, and the guarded pages open", async () => {
    at("/");
    await userEvent.click(screen.getByRole("button", { name: "sign in" }));
    expect(api.initSession).toHaveBeenCalledWith("md-santos");
    expect(JSON.parse(sessionStorage.getItem(KEY) ?? "null")).toEqual({
      clinician_id: "md-santos",
      ward: "W4",
      role: "md",
    });
  });

  it("still signs in when storage refuses the write (the session lives in memory)", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    at("/");
    await userEvent.click(screen.getByRole("button", { name: "sign in" }));
    expect(api.initSession).toHaveBeenCalledWith("md-santos");
  });

  it.each([
    ["the ward", "/ward", nurse],
    ["the station", "/station", charge],
  ])("signs out from %s: clears the API session and the stored one", async (_, path, s) => {
    store(s);
    at(path);
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(api.clearSession).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(KEY)).toBeNull();
    expect(screen.getByText(/sign-in page/)).toBeTruthy();
  });

  it("signs out even when storage refuses the removal", async () => {
    store(nurse);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    at("/ward");
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(api.clearSession).toHaveBeenCalledOnce();
    expect(screen.getByText(/sign-in page/)).toBeTruthy();
  });
});
