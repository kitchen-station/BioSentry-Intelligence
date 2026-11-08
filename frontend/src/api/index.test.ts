/**
 * The provider switch. With no VITE_API_URL every call goes to the fixture provider, loaded on
 * first use, and the session is the fixture's active clinician. With one, the session is a bearer
 * token: one handed in, or one asked for at sign-in (never the clinician id).
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => {
  // Each method and how many arguments it takes (ApiProvider in contract.ts).
  const arity = {
    getWard: 0, getPatient: 1, getNotes: 1, addNote: 4, deleteNote: 2, listAllPatients: 0,
    admitPatient: 1, enterLabResults: 2, recordVitals: 2, getAcknowledgements: 0, acknowledge: 2,
    extractLabsFromFile: 1, extractPatientFromFile: 1,
  } as const;
  const provider = Object.fromEntries(Object.keys(arity).map((n) => [n, vi.fn(async () => n)]));
  return { arity, provider, setActiveClinician: vi.fn() };
});
vi.mock("./providers/fixture", () => ({
  fixtureProvider: fixture.provider,
  setActiveClinician: fixture.setActiveClinician,
}));

const client = vi.hoisted(() => ({ openSession: vi.fn(async () => undefined), setAuthToken: vi.fn() }));
vi.mock("./client", async (actual) => ({ ...(await actual<typeof import("./client")>()), ...client }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.clearAllMocks();
});

async function load(apiUrl: string) {
  vi.stubEnv("VITE_API_URL", apiUrl);
  vi.resetModules();
  return import("./index");
}

const flush = () => new Promise((r) => setTimeout(r, 0)); // the lazy import settles

describe("fixture mode", () => {
  it("forwards every call, with its arguments, to the fixture provider", async () => {
    const api = (await load("")) as unknown as Record<string, (...a: unknown[]) => Promise<unknown>>;
    for (const [name, n] of Object.entries(fixture.arity)) {
      await expect(api[name]("a1", "a2", "a3", "a4")).resolves.toBe(name);
      expect(fixture.provider[name].mock.calls, name).toEqual([["a1", "a2", "a3", "a4"].slice(0, n)]);
    }
  });

  it("signs in and out by setting the fixture's active clinician, never a token", async () => {
    const api = await load("");
    api.initSession("rn-manalo", "ignored-token");
    await flush();
    expect(fixture.setActiveClinician).toHaveBeenLastCalledWith("rn-manalo");
    api.clearSession();
    await flush();
    expect(fixture.setActiveClinician).toHaveBeenLastCalledWith(null);
    expect(client.setAuthToken).not.toHaveBeenCalled();
    expect(client.openSession).not.toHaveBeenCalled();
  });
});

describe("HTTP mode", () => {
  it("uses a token it is handed, or asks the API for one, and drops it on sign-out", async () => {
    const api = await load("http://127.0.0.1:8000");
    api.initSession("rn-manalo", "t-1");
    expect(client.setAuthToken).toHaveBeenLastCalledWith("t-1");
    api.initSession("rn-manalo");
    expect(client.openSession).toHaveBeenCalledWith("rn-manalo");
    api.clearSession();
    expect(client.setAuthToken).toHaveBeenLastCalledWith(null);
    expect(fixture.setActiveClinician).not.toHaveBeenCalled();
  });
});
