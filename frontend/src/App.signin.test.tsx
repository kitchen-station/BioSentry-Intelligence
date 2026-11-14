// @vitest-environment jsdom
/** App with the real sign-in page and the real session menu: where the next sign-in lands. */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Session } from "./types";
import { SessionMenu } from "./components/SessionMenu";
import { App } from "./App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  restoreSession: vi.fn(() => true),
  clearSession: vi.fn(),
  signIn: vi.fn(),
  usesServer: false,
}));
vi.mock("./api", async () => ({
  ApiError: (await vi.importActual<typeof import("./api/client")>("./api/client")).ApiError,
  restoreSession: api.restoreSession,
  hasLabQuestions: false,
  hasWardResearch: false,
  clearSession: api.clearSession,
  signIn: api.signIn,
  get usesServer() {
    return api.usesServer;
  },
}));

type StubProps = { session: Session; onSignOut: (e?: boolean) => void };
const stub = (name: string) => ({ session, onSignOut }: StubProps) => (
  <div>
    <p>{name} page for {session.clinician_id}</p>
    <SessionMenu session={session} onSignOut={onSignOut} />
    <button type="button" onClick={() => onSignOut()}>session ends</button>
  </div>
);
vi.mock("./pages/Ward", () => ({ Ward: (p: StubProps) => stub("ward")(p) }));
vi.mock("./pages/PatientDetail", () => ({ PatientDetail: (p: StubProps) => stub("patient")(p) }));
vi.mock("./pages/Station", () => ({ Station: (p: StubProps) => stub("station")(p) }));
vi.mock("./pages/Overview", () => ({ Overview: (p: StubProps) => stub("overview")(p) }));
vi.mock("./pages/Lab", () => ({ Lab: (p: StubProps) => stub("lab")(p) }));
vi.mock("./pages/Research", () => ({ Research: (p: StubProps) => stub("research")(p) }));
vi.mock("./pages/Pipeline", () => ({ Pipeline: (p: StubProps) => stub("pipeline")(p) }));

const click = async (name: RegExp | string) => userEvent.click(await screen.findByRole("button", { name }));
const signOut = async () => {
  await click(/^(rn|md)-/);
  await click("Sign out");
};
const nurse = (who: RegExp, dest: RegExp) => async () => {
  await click(/^Nurse\s*Staff/);
  await click(who);
  await click("Continue");
  await click(dest);
};

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  api.usesServer = false; // the demo prefills a passphrase
  api.signIn.mockResolvedValue(undefined);
  api.restoreSession.mockReturnValue(true);
});
afterEach(cleanup);

const start = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Back />
    </MemoryRouter>,
  );

// The browser's Back button: a stub inside the router that goes one entry back in its history.
function Back() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>history back</button>;
}

describe("where a sign-in lands", () => {
  const physician = { clinician_id: "md-santos", ward: "W4", role: "md" };

  it("a nurse after a physician signed out on the lab: the ward", async () => {
    sessionStorage.setItem("biosentry.session", JSON.stringify(physician));
    start("/lab");
    await signOut();
    await nurse(/Nurse de la Cruz/, /Ward Monitor/)();
    expect(await screen.findByText("ward page for rn-delacruz")).toBeTruthy();
  });

  it("a physician after another physician signed out on the lab: the station", async () => {
    sessionStorage.setItem("biosentry.session", JSON.stringify(physician));
    start("/lab");
    await signOut();
    await click(/^Physician/);
    await click(/Dr\. Aquino/);
    await click("Continue");
    await click(/Station/);
    expect(await screen.findByText("station page for md-aquino")).toBeTruthy();
  });

  it("an admin after a clinician signed out on the lab: the overview", async () => {
    api.usesServer = true;
    sessionStorage.setItem("biosentry.session", JSON.stringify(physician));
    start("/lab");
    await signOut();
    api.signIn.mockResolvedValue("admin");
    await click(/^Administration/);
    await userEvent.type(screen.getByLabelText("Administrator id"), "admin-ruiz");
    await userEvent.type(screen.getByLabelText("Passphrase"), "x");
    await click("Continue");
    expect(await screen.findByText("overview page for admin-ruiz")).toBeTruthy();
  });

  it("the same clinician whose session ended on a patient page returns to it", async () => {
    sessionStorage.setItem("biosentry.session", JSON.stringify(physician));
    start("/patient/p-001");
    await click("session ends");
    await click(/^Physician/);
    await click(/Dr\. Santos/);
    await click("Continue");
    await click(/Ward Monitor/);
    expect(await screen.findByText("patient page for md-santos")).toBeTruthy();
  });

  it("another clinician after a session ended on a patient page: the chosen page", async () => {
    sessionStorage.setItem("biosentry.session", JSON.stringify(physician));
    start("/patient/p-001");
    await click("session ends");
    await nurse(/Nurse de la Cruz/, /Ward Monitor/)();
    expect(await screen.findByText("ward page for rn-delacruz")).toBeTruthy();
  });

  it("a fresh deep link returns to the patient page after sign-in", async () => {
    start("/patient/p-001");
    await nurse(/Nurse de la Cruz/, /Ward Monitor/)();
    expect(await screen.findByText("patient page for rn-delacruz")).toBeTruthy();
  });

  it("Back never reaches an old sign-in entry that holds the last person's return page", async () => {
    start("/lab"); // a deep link with no session: sign-in is shown with { from: "/lab" }
    await click(/^Physician/);
    await click(/Dr\. Santos/);
    await click("Continue");
    await click(/Ward Monitor/);
    expect(await screen.findByText("lab page for md-santos")).toBeTruthy();
    await click("history back");
    expect(screen.queryByText(/sign-in page|Choose destination|I am a/)).toBeNull(); // the sign-in entry is gone
    await signOut();
    await click("history back"); // /lab, signed out: replaced by sign-in with no state
    await click("history back");
    await nurse(/Nurse de la Cruz/, /Ward Monitor/)();
    expect(await screen.findByText("ward page for rn-delacruz")).toBeTruthy();
  });
});
