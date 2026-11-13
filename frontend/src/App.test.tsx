// @vitest-environment jsdom
/**
 * App: the session survives a reload only when what is stored is well formed and the API can be
 * re-armed with it (an API token lives in memory, so after a reload it cannot: sign in again), the
 * route guards send a signed-out visitor to sign-in (remembering where they were going) and keep a
 * staff nurse out of the station, and signing out clears the session everywhere.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "./types";
import { App } from "./App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ restoreSession: vi.fn(() => true), clearSession: vi.fn() }));
vi.mock("./api", () => api);

// The pages are stand-ins: these tests are about the shell, not what each page shows.
vi.mock("./pages/SignIn", () => ({
  SignIn: ({ onSignIn }: { onSignIn: (s: Session) => void }) => {
    const from = (useLocation().state as { from?: string } | null)?.from;
    const navigate = useNavigate();
    return (
      <div>
        <p>sign-in page{from ? ` from ${from}` : ""}</p>
        <button
          type="button"
          onClick={() => {
            onSignIn({ clinician_id: "md-santos", ward: "W4", role: "md" });
            navigate("/ward"); // as the real page does once it has signed in
          }}
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
vi.mock("./pages/Research", () => ({
  Research: ({ session }: { session: Session }) => <p>research page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Pipeline", () => ({
  Pipeline: ({ session }: { session: Session }) => <p>pipeline page for {session.clinician_id}</p>,
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
  api.restoreSession.mockReturnValue(true);
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
    expect(api.restoreSession).toHaveBeenCalledWith("rn-reyes");
  });

  it("does not show a signed-in screen the API cannot serve: no token after a reload is sign-in again", () => {
    api.restoreSession.mockReturnValue(false);
    store(nurse);
    at("/ward");
    expect(screen.getByText("sign-in page from /ward")).toBeTruthy();
    expect(screen.queryByText("ward page for rn-reyes")).toBeNull();
    expect(sessionStorage.getItem(KEY)).toBeNull(); // and the stale record is dropped
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
    expect(api.restoreSession).not.toHaveBeenCalled();
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
  it.each(["/ward", "/patient/p-001", "/station", "/research", "/pipeline"])(
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

  it("shows the pipeline to any signed-in clinician", () => {
    store(nurse);
    at("/pipeline");
    expect(screen.getByText("pipeline page for rn-reyes")).toBeTruthy();
  });

  it("shows the research numbers to any signed-in clinician", () => {
    store(nurse);
    at("/research");
    expect(screen.getByText("research page for rn-reyes")).toBeTruthy();
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
  it("signs in: stores who is signed in (the sign-in page has already got the token)", async () => {
    at("/");
    await userEvent.click(screen.getByRole("button", { name: "sign in" }));
    expect(screen.getByText("ward page for md-santos")).toBeTruthy();
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
    expect(screen.getByText("ward page for md-santos")).toBeTruthy();
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
