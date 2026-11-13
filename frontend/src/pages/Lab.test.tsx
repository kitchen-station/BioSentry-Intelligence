// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import synthetic from "../data/synthetic.json";
import { Lab } from "./Lab";
import { Pipeline } from "./Pipeline";
import { Research } from "./Research";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Every write is a spy that fails if called; the one read the lab makes returns the fixture ward.
const api = vi.hoisted(() => ({
  getWard: vi.fn(), getLabRuns: vi.fn(), getLabRun: vi.fn(), hasLabRuns: false, requestLabRun: vi.fn(),
  addNote: vi.fn(), deleteNote: vi.fn(), admitPatient: vi.fn(), enterLabResults: vi.fn(), recordVitals: vi.fn(),
  acknowledge: vi.fn(), extractLabsFromFile: vi.fn(), extractPatientFromFile: vi.fn(), signIn: vi.fn(), clearSession: vi.fn(),
}));
vi.mock("../api", () => api);

const WRITES = [
  api.addNote, api.deleteNote, api.admitPatient, api.enterLabResults, api.recordVitals,
  api.acknowledge, api.extractLabsFromFile, api.extractPatientFromFile, api.signIn, api.clearSession, api.requestLabRun,
];
const session = { clinician_id: "md-santos", ward: "W4", role: "md" } as const;
const none = () => {};

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const lab = () =>
  render(
    <MemoryRouter initialEntries={["/lab"]}>
      <Routes>
        <Route path="/lab" element={<Lab session={session} onSignOut={none} />} />
        <Route path="/lab/research" element={<Research session={session} onSignOut={none} />} />
        <Route path="/lab/pipeline" element={<Pipeline session={session} onSignOut={none} />} />
        <Route path="/ward" element={<p>the ward</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("lab index", () => {
  it("lists the three places and no simulator link in the fixture build", () => {
    lab();
    expect(screen.getByRole("heading", { level: 1, name: "Simulation lab" })).toBeTruthy();
    const href = (name: string) => screen.getByRole("link", { name }).getAttribute("href");
    expect(href("Research numbers")).toBe("/lab/research");
    expect(href("The research agent's questions and answers")).toBe("/lab/research#agent");
    expect(href("The pipeline")).toBe("/lab/pipeline");
    expect(screen.queryByText("Outside the lab")).toBeNull();
    expect(screen.queryByRole("link", { name: "Open the ward simulator" })).toBeNull();
  });

  it("links the ward simulator, with its warning, when the one-address image serves it", () => {
    vi.stubEnv("VITE_API_URL", "/api");
    lab();
    expect(screen.getByRole("heading", { name: "Outside the lab" })).toBeTruthy();
    expect(screen.getByText(/a change made there changes the ranks on the ward list/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open the ward simulator" });
    expect(link.getAttribute("href")).toBe("/simulator/");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener");
    expect(screen.getByText(/It opens in a new tab and asks for its own sign-in\./)).toBeTruthy();
  });

  it("hides the simulator link for a local API address", () => {
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8000");
    lab();
    expect(screen.queryByRole("link", { name: "Open the ward simulator" })).toBeNull();
  });

  it("shows the simulated runs section, with its fixture line and no call", () => {
    lab();
    expect(screen.getByRole("heading", { name: "Simulated runs" })).toBeTruthy();
    expect(screen.getByText("Simulated runs need the server.")).toBeTruthy();
    expect(api.getLabRuns).toHaveBeenCalledTimes(0);
  });
});

describe("the lab writes nothing to the ward", () => {
  it("calls no write of the mocked api while every lab page renders and its in-lab links are clicked", async () => {
    api.getWard.mockResolvedValue(synthetic);
    lab();
    for (const name of ["Research numbers", "The pipeline"]) {
      await userEvent.click(screen.getByRole("link", { name }));
      expect(await screen.findByRole("heading", { level: 1, name })).toBeTruthy();
      await userEvent.click(screen.getByRole("link", { name: "Ward" }));
      expect(screen.getByText("the ward")).toBeTruthy();
      cleanup();
      lab();
    }
    for (const w of WRITES) expect(w).toHaveBeenCalledTimes(0);
  });
});

describe("the lab with the lab-run capability on", () => {
  it.each(["md", "charge_rn"] as const)("makes no request and no ward write on mount for %s", async (role) => {
    api.hasLabRuns = true;
    api.getLabRuns.mockResolvedValue([]);
    render(
      <MemoryRouter initialEntries={["/lab"]}>
        <Routes>
          <Route path="/lab" element={<Lab session={{ ...session, role }} onSignOut={none} />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("No simulated runs yet.")).toBeTruthy();
    for (const w of WRITES) expect(w).toHaveBeenCalledTimes(0);
    api.hasLabRuns = false;
  });
});
