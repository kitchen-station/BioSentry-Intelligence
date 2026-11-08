import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, openSession, setAuthToken } from "../client";
import { httpProvider } from "./http";

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status === 404 ? "Not Found" : "Error",
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  setAuthToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAuthToken(null);
});

describe("httpProvider.getPatient", () => {
  it("returns the patient on 200", async () => {
    fetchMock.mockResolvedValue(reply(200, { id: "p-001" }));
    await expect(httpProvider.getPatient("p-001")).resolves.toEqual({ id: "p-001" });
    expect(fetchMock.mock.calls[0][0]).toBe("/patients/p-001");
  });

  it("returns null on 404 (missing and not-yours look the same)", async () => {
    fetchMock.mockResolvedValue(reply(404, { detail: "Not found" }));
    await expect(httpProvider.getPatient("p-999")).resolves.toBeNull();
  });

  it.each([401, 403, 500])("rethrows %s as an ApiError", async (status) => {
    fetchMock.mockResolvedValue(reply(status, { detail: "no" }));
    const failure = await httpProvider.getPatient("p-001").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(status);
  });
});

describe("openSession", () => {
  it("asks the API for a token and holds requests until it arrives", async () => {
    fetchMock
      .mockResolvedValueOnce(reply(200, { token: "t-123" }))
      .mockResolvedValueOnce(reply(200, { ranked: [], abstained: [] }));
    void openSession("md-santos");
    await httpProvider.getWard();
    expect(fetchMock.mock.calls[0][0]).toBe("/session");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({ clinician_id: "md-santos" });
    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer t-123");
  });

  it("fails closed when the API refuses: no token, never the clinician id", async () => {
    fetchMock.mockResolvedValueOnce(reply(403, { detail: "no" })).mockResolvedValueOnce(reply(401, { detail: "no" }));
    void openSession("rn-reyes");
    await expect(httpProvider.getWard()).rejects.toBeInstanceOf(ApiError);
    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("drops a token that arrives after sign-out", async () => {
    let answer!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (answer = r)));
    const opening = openSession("md-santos");
    setAuthToken(null);
    answer(reply(200, { token: "late" }));
    await opening;
    fetchMock.mockResolvedValueOnce(reply(200, []));
    await httpProvider.listAllPatients();
    expect(new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers).has("Authorization")).toBe(false);
  });
});

describe("httpProvider requests", () => {
  it("sends the bearer token the session set", async () => {
    setAuthToken("rn-manalo");
    fetchMock.mockResolvedValue(reply(200, { ranked: [], abstained: [] }));
    await httpProvider.getWard();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer rn-manalo");
    expect(fetchMock.mock.calls[0][0]).toBe("/wards/W4/rows");
  });

  it("sends no Authorization header without a session", async () => {
    fetchMock.mockResolvedValue(reply(200, []));
    await httpProvider.listAllPatients();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });
});

describe("httpProvider acknowledgements", () => {
  it("lists them from the server", async () => {
    fetchMock.mockResolvedValue(reply(200, [{ patient_id: "p-001", score: 9 }]));
    await expect(httpProvider.getAcknowledgements()).resolves.toEqual([{ patient_id: "p-001", score: 9 }]);
    expect(fetchMock.mock.calls[0][0]).toBe("/acknowledgements");
  });

  it("posts the score to the patient's route and rejects when the server refuses", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(httpProvider.acknowledge("p-001", 9)).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/patients/p-001/acknowledgement");
    expect(JSON.parse(init.body as string)).toEqual({ score: 9 });

    fetchMock.mockResolvedValueOnce(reply(404, { detail: "Not found" }));
    await expect(httpProvider.acknowledge("p-999", 9)).rejects.toBeInstanceOf(ApiError);
  });
});
