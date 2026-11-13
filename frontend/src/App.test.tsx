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

const api = vi.hoisted(() => ({ restoreSession: vi.fn(() => true), clearSession: vi.fn(), hasLabQuestions: true }));
vi.mock("./api", () => api);

// The pages are stand-ins: these tests are about the shell, not what each page shows.
vi.mock("./pages/SignIn", () => ({
  SignIn: ({ onSignIn }: { onSignIn: (s: Session) => void }) => {
    const { from, who } = (useLocation().state ?? {}) as { from?: string; who?: string };
    const navigate = useNavigate();
    return (
      <div>
        <p>sign-in page{from ? ` from ${from}` : ""}{who ? ` by ${who}` : ""}</p>
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
  Ward: ({ session, onSignOut }: { session: Session; onSignOut: (explicit?: boolean) => void }) => (
    <div>
      <p>ward page for {session.clinician_id}</p>
      <button type="button" onClick={() => onSignOut(true)}>
        sign out
      </button>
      <button type="button" onClick={() => onSignOut()}>
        session ends
      </button>
    </div>
  ),
}));
vi.mock("./pages/PatientDetail", () => ({
  PatientDetail: ({ session, onSignOut }: { session: Session; onSignOut: (explicit?: boolean) => void }) => (
    <div>
      <p>patient page for {session.clinician_id}</p>
      <button type="button" onClick={() => onSignOut(true)}>
        sign out
      </button>
      <button type="button" onClick={() => onSignOut()}>
        session ends
      </button>
    </div>
  ),
}));
vi.mock("./pages/Lab", () => ({
  Lab: ({ session }: { session: Session }) => <p>lab page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Ask", () => ({
  Ask: ({ session }: { session: Session }) => <p>ask page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Research", () => ({
  Research: ({ session }: { session: Session }) => <p>research page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Pipeline", () => ({
  Pipeline: ({ session }: { session: Session }) => <p>pipeline page for {session.clinician_id}</p>,
}));
vi.mock("./pages/Station", () => ({
  Station: ({ session, onSignOut }: { session: Session; onSignOut: (explicit?: boolean) => void }) => (
    <div>
      <p>station page for {session.clinician_id}</p>
      <button type="button" onClick={() => onSignOut(true)}>
        sign out
      </button>
      <button type="button" onClick={() => onSignOut()}>
        session ends
      </button>
    </div>
  ),
}));

vi.mock("./pages/Overview", () => ({
  Overview: ({ session }: { session: Session }) => <p>overview page for {session.clinician_id}</p>,
}));

const KEY = "biosentry.session";
const admin: Session = { clinician_id: "admin-ruiz", ward: "W4", role: "admin" };
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
    ["an unknown role", JSON.stringify({ ...nurse, role: "root" })],
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

  // Rewritten: this used to expect "from /patient/p-001" after a chosen sign-out, which is the fault
  // (the next person landed on the last page of the one who left).
  it("signs out from the patient page and keeps no return page", async () => {
    store(nurse);
    at("/patient/p-001");
    await userEvent.click(screen.getByRole("button", { name: "sign out" }));
    expect(screen.getByText("sign-in page")).toBeTruthy();
  });

  it("keeps the return page and who it belongs to when a session ends by itself", async () => {
    store(nurse);
    at("/patient/p-001");
    await userEvent.click(screen.getByRole("button", { name: "session ends" }));
    expect(screen.getByText("sign-in page from /patient/p-001 by rn-reyes")).toBeTruthy();
  });

  it("a fresh deep link has a return page and no owner", () => {
    at("/patient/p-001");
    expect(screen.getByText("sign-in page from /patient/p-001")).toBeTruthy();
  });

  it.each([
    ["rn", nurse],
    ["md", { clinician_id: "md-santos", ward: "W4", role: "md" }],
    ["charge_rn", charge],
  ] as const)("shows every lab page to %s", (_, who) => {
    store(who);
    for (const [path, words] of [["/lab", "lab page"], ["/lab/research", "research page"], ["/lab/pipeline", "pipeline page"]]) {
      const { unmount } = at(path);
      expect(screen.getByText(`${words} for ${who.clinician_id}`)).toBeTruthy();
      unmount();
    }
  });

  it("shows the Ask page to a signed-in clinician and sends a signed-out visitor to sign-in", () => {
    store(nurse);
    const { unmount } = at("/lab/ask");
    expect(screen.getByText("ask page for rn-reyes")).toBeTruthy();
    unmount();
    sessionStorage.clear();
    at("/lab/ask");
    expect(screen.getByText("sign-in page from /lab/ask")).toBeTruthy();
  });

  it("has no Ask route in the fixture build", () => {
    api.hasLabQuestions = false;
    store(nurse);
    at("/lab/ask");
    api.hasLabQuestions = true;
    expect(screen.queryByText("ask page for rn-reyes")).toBeNull();
  });

  it.each([
    ["/research", "research page"],
    ["/pipeline", "pipeline page"],
  ])("sends the old %s address into the lab", (path, words) => {
    store(nurse);
    at(path);
    expect(screen.getByText(`${words} for rn-reyes`)).toBeTruthy();
  });

  it("sends a signed-out visitor of the lab to sign-in, remembering where", () => {
    at("/lab/research");
    expect(screen.getByText("sign-in page from /lab/research")).toBeTruthy();
  });

  it("sends a signed-out visitor of an old address to sign-in", () => {
    at("/research");
    expect(screen.getByText("sign-in page from /research")).toBeTruthy();
  });

  it("shows the overview to an admin, who may also open the lab", () => {
    store(admin);
    at("/overview");
    expect(screen.getByText("overview page for admin-ruiz")).toBeTruthy();
    cleanup();
    at("/lab");
    expect(screen.getByText("lab page for admin-ruiz")).toBeTruthy();
  });

  it("lets an admin open the ward list and a patient page", () => {
    store(admin);
    at("/ward");
    expect(screen.getByText("ward page for admin-ruiz")).toBeTruthy();
    cleanup();
    at("/patient/p-001");
    expect(screen.getByText("patient page for admin-ruiz")).toBeTruthy();
  });

  it("still sends an admin from the station to the overview", () => {
    store(admin);
    at("/station");
    expect(screen.getByText("overview page for admin-ruiz")).toBeTruthy();
  });

  it("hands any other role to the overview page, which refuses it, and a signed-out visitor to sign in", () => {
    store(nurse);
    at("/overview");
    expect(screen.getByText("overview page for rn-reyes")).toBeTruthy();
    cleanup();
    sessionStorage.clear();
    at("/overview");
    expect(screen.getByText("sign-in page from /overview")).toBeTruthy();
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
