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
  getWard: vi.fn(),
  addNote: vi.fn(), deleteNote: vi.fn(), admitPatient: vi.fn(), enterLabResults: vi.fn(), recordVitals: vi.fn(),
  acknowledge: vi.fn(), extractLabsFromFile: vi.fn(), extractPatientFromFile: vi.fn(), signIn: vi.fn(), clearSession: vi.fn(),
}));
vi.mock("../api", () => api);

const WRITES = [
  api.addNote, api.deleteNote, api.admitPatient, api.enterLabResults, api.recordVitals,
  api.acknowledge, api.extractLabsFromFile, api.extractPatientFromFile, api.signIn, api.clearSession,
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
    expect(screen.getByText(/can change that ward, and so the ranks on the ward list/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the ward simulator" }).getAttribute("href")).toBe("/simulator/");
  });

  it("hides the simulator link for a local API address", () => {
    vi.stubEnv("VITE_API_URL", "http://127.0.0.1:8000");
    lab();
    expect(screen.queryByRole("link", { name: "Open the ward simulator" })).toBeNull();
  });
});

describe("the lab writes nothing to the ward", () => {
  it("calls no write and sends no request but a GET while every lab page is opened", async () => {
    api.getWard.mockResolvedValue(synthetic);
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    lab();
    for (const name of ["Research numbers", "The pipeline"]) {
      await userEvent.click(screen.getByRole("link", { name }));
      expect(await screen.findByRole("heading", { level: 1, name })).toBeTruthy();
      await userEvent.click(screen.getByRole("link", { name: "Back to the ward" }));
      expect(screen.getByText("the ward")).toBeTruthy();
      cleanup();
      lab();
    }
    for (const w of WRITES) expect(w).toHaveBeenCalledTimes(0);
    for (const call of fetchSpy.mock.calls) expect((call[1]?.method ?? "GET").toUpperCase()).toBe("GET");
  });
});
