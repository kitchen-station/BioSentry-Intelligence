/**
 * The provider switch. With no VITE_API_URL every call goes to the fixture provider, loaded on
 * first use, and the session is the fixture's active clinician (no server, nothing checked). With
 * one, the session is a bearer token asked for with the passphrase at sign-in (never the clinician
 * id), held in memory only: a reload cannot restore it.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => {
  // Each method and how many arguments it takes (ApiProvider in contract.ts).
  const arity = {
    getWard: 0, getPatient: 1, getNotes: 1, addNote: 4, deleteNote: 2, listAllPatients: 0, getStays: 0,
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

const client = vi.hoisted(() => ({
  openSession: vi.fn(async () => undefined),
  setAuthToken: vi.fn(),
  getAuthToken: vi.fn((): string | null => null),
}));
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

  it("has no server: any passphrase signs in, by setting the fixture's active clinician", async () => {
    const api = await load("");
    expect(api.usesServer).toBe(false);
    await api.signIn("rn-manalo", "whatever");
    expect(fixture.setActiveClinician).toHaveBeenLastCalledWith("rn-manalo");
    await api.signIn("rn-manalo", "");
    expect(fixture.setActiveClinician).toHaveBeenCalledTimes(2);
    expect(client.openSession).not.toHaveBeenCalled();
  });

  it("restores a session after a reload and signs out by clearing the active clinician", async () => {
    const api = await load("");
    expect(api.restoreSession("rn-manalo")).toBe(true);
    await flush();
    expect(fixture.setActiveClinician).toHaveBeenLastCalledWith("rn-manalo");
    api.clearSession();
    await flush();
    expect(fixture.setActiveClinician).toHaveBeenLastCalledWith(null);
    expect(client.setAuthToken).not.toHaveBeenCalled();
  });
});

describe("HTTP mode", () => {
  it("signs in by asking the API for a token with the passphrase, and rejects when it refuses", async () => {
    const api = await load("http://127.0.0.1:8000");
    expect(api.usesServer).toBe(true);
    await api.signIn("rn-manalo", "a passphrase");
    expect(client.openSession).toHaveBeenCalledWith("rn-manalo", "a passphrase");
    expect(fixture.setActiveClinician).not.toHaveBeenCalled();
    client.openSession.mockRejectedValueOnce(new Error("401"));
    await expect(api.signIn("rn-manalo", "wrong")).rejects.toThrow("401");
  });

  it("cannot restore a session after a reload: the token was only in memory", async () => {
    const api = await load("http://127.0.0.1:8000");
    expect(api.restoreSession("rn-manalo")).toBe(false);
    client.getAuthToken.mockReturnValueOnce("t-1"); // still the same page: the token is there
    expect(api.restoreSession("rn-manalo")).toBe(true);
    expect(client.openSession).not.toHaveBeenCalled();
    expect(fixture.setActiveClinician).not.toHaveBeenCalled();
  });

  it("drops the token on sign-out", async () => {
    const api = await load("http://127.0.0.1:8000");
    api.clearSession();
    expect(client.setAuthToken).toHaveBeenLastCalledWith(null);
    expect(fixture.setActiveClinician).not.toHaveBeenCalled();
  });
});
