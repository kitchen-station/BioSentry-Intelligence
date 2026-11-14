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
    void openSession("md-santos", "a passphrase");
    await httpProvider.getWard();
    expect(fetchMock.mock.calls[0][0]).toBe("/session");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      clinician_id: "md-santos",
      passphrase: "a passphrase",
    });
    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer t-123");
  });

  it("fails closed when the API refuses: no token, never the clinician id", async () => {
    fetchMock.mockResolvedValueOnce(reply(401, { detail: "no" })).mockResolvedValueOnce(reply(401, { detail: "no" }));
    const refused = expect(openSession("rn-reyes", "wrong")).rejects.toBeInstanceOf(ApiError); // the sign-in page hears of it
    await expect(httpProvider.getWard()).rejects.toBeInstanceOf(ApiError);
    await refused;
    const init = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("drops a token that arrives after sign-out", async () => {
    let answer!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (answer = r)));
    const opening = openSession("md-santos", "a passphrase");
    setAuthToken(null);
    answer(reply(200, { token: "late" }));
    await opening;
    fetchMock.mockResolvedValueOnce(reply(200, []));
    await httpProvider.listAllPatients();
    expect(new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers).has("Authorization")).toBe(false);
  });
});

describe("httpProvider requests", () => {
  it("reads the ward's stays", async () => {
    setAuthToken("rn-manalo");
    fetchMock.mockResolvedValue(reply(200, []));
    await expect(httpProvider.getStays()).resolves.toEqual([]);
    expect(fetchMock.mock.calls[0][0]).toBe("/wards/W4/stays");
  });

  it("reads the live ward the simulator published", async () => {
    setAuthToken("rn-manalo");
    const live = { shared: { clockS: 0 }, patients: [], can_control: true };
    fetchMock.mockResolvedValue(reply(200, live));
    await expect(httpProvider.getLiveWard()).resolves.toEqual(live);
    expect(fetchMock.mock.calls[0][0]).toBe("/wards/W4/live");
  });

  it("reads the admin overview, and a 403 is an ApiError", async () => {
    fetchMock.mockResolvedValueOnce(reply(200, { ward: "W4" }));
    await expect(httpProvider.getOverview()).resolves.toEqual({ ward: "W4" });
    expect(fetchMock.mock.calls[0][0]).toBe("/wards/W4/overview");
    fetchMock.mockResolvedValueOnce(reply(403, { detail: "Forbidden" }));
    const failure = await httpProvider.getOverview().catch((e: unknown) => e);
    expect((failure as ApiError).status).toBe(403);
  });

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

describe("httpProvider.getBaseline", () => {
  it("reads the patient's baseline route with the bearer token and serves the body as sent", async () => {
    setAuthToken("rn-manalo");
    fetchMock.mockResolvedValue(reply(200, { patient_id: "p-001", vitals: [{ latest: 72.123456789 }] }));
    await expect(httpProvider.getBaseline("p-001")).resolves.toEqual({
      patient_id: "p-001",
      vitals: [{ latest: 72.123456789 }],
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/patients/p-001/baseline");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer rn-manalo");
  });

  it("encodes the id, so a `?` or `#` in it cannot reach the patient route", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    await httpProvider.getBaseline("p-1?x=1#y/z");
    expect(fetchMock.mock.calls[0][0]).toBe("/patients/p-1%3Fx%3D1%23y%2Fz/baseline");
  });

  it("rejects on 404 rather than answering null: null means fixture mode", async () => {
    fetchMock.mockResolvedValue(reply(404, { detail: "Not found" }));
    const failure = await httpProvider.getBaseline("p-999").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(404);
  });
});

describe("httpProvider maps each call to its endpoint", () => {
  const sent = (call = 0) => {
    const [path, init = {}] = fetchMock.mock.calls[call] as [string, RequestInit?];
    const body = init.body;
    return {
      path,
      method: init.method ?? "GET",
      body: typeof body === "string" ? JSON.parse(body) : body,
    };
  };
  const file = new File(["x"], "labs.pdf", { type: "application/pdf" });

  it.each([
    ["getBaseline", () => httpProvider.getBaseline("p-1"), "GET", "/patients/p-1/baseline", undefined],
    ["getNotes", () => httpProvider.getNotes("p-1"), "GET", "/patients/p-1/notes", undefined],
    ["addNote", () => httpProvider.addNote("p-1", "RN Cruz", "RN", "Restless"), "POST", "/patients/p-1/notes", { author: "RN Cruz", role: "RN", content: "Restless" }],
    ["listAllPatients", () => httpProvider.listAllPatients(), "GET", "/patients", undefined],
    ["admitPatient", () => httpProvider.admitPatient({ firstName: "A" } as never), "POST", "/patients", { firstName: "A" }],
    ["enterLabResults", () => httpProvider.enterLabResults("p-1", { creatinine: 1.2 } as never), "POST", "/patients/p-1/labs", { creatinine: 1.2 }],
    ["recordVitals", () => httpProvider.recordVitals("p-1", { systolic: 120, diastolic: 80 }), "POST", "/patients/p-1/vitals", { systolic: 120, diastolic: 80 }],
    ["getAcknowledgements", () => httpProvider.getAcknowledgements(), "GET", "/acknowledgements", undefined],
    ["acknowledge", () => httpProvider.acknowledge("p-1", 9), "POST", "/patients/p-1/acknowledgement", { score: 9 }],
  ] as const)("%s", async (_, call, method, path, body) => {
    fetchMock.mockResolvedValue(reply(200, { ok: true }));
    await expect(call()).resolves.toEqual({ ok: true });
    expect(sent()).toEqual({ path, method, body });
  });

  it("deleteNote sends a DELETE to the note", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(httpProvider.deleteNote("p-1", "n-002")).resolves.toBeUndefined();
    expect(sent()).toMatchObject({ path: "/patients/p-1/notes/n-002", method: "DELETE" });
  });

  it.each([
    ["extractLabsFromFile", () => httpProvider.extractLabsFromFile(file), "/extract/labs"],
    ["extractPatientFromFile", () => httpProvider.extractPatientFromFile(file), "/extract/patient"],
  ] as const)("%s uploads the file", async (_, call, path) => {
    fetchMock.mockResolvedValue(reply(200, { values: {} }));
    await expect(call()).resolves.toEqual({ values: {} });
    const { path: sentPath, method, body } = sent();
    expect([sentPath, method]).toEqual([path, "POST"]);
    expect((body as FormData).get("file")).toBeInstanceOf(File);
  });
});

describe("httpProvider lab runs", () => {
  it("asks, lists and reads only the lab run paths", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    const input = { kind: "compare", mode: "w4", seed: 1, days: 1, baseline: "news2_total_ge_5", change: "news2_chart_trigger" } as const;
    await httpProvider.requestLabRun(input);
    await httpProvider.getLabRuns();
    await httpProvider.getLabRun(7);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/wards/W4/lab/runs", "/wards/W4/lab/runs", "/wards/W4/lab/runs/7"]);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(input);
  });
});

describe("httpProvider stay history", () => {
  it("reads only the history path of one patient", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await httpProvider.getPatientHistory("p-001");
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/patients/p-001/history"]);
  });
});

describe("httpProvider ward research", () => {
  it("reads only the ward's research path, with GET", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await httpProvider.getWardResearch();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/wards/W4/research"]);
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });
});

describe("httpProvider patient questions", () => {
  it("asks, lists and reads only the question paths of one patient", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await httpProvider.askPatient("p-001", "Why is the rate up?");
    await httpProvider.getPatientQuestions("p-001");
    await httpProvider.getPatientQuestion("p-001", 7);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/patients/p-001/questions", "/patients/p-001/questions", "/patients/p-001/questions/7"]);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: "Why is the rate up?" });
  });
});

describe("httpProvider lab questions", () => {
  it("asks, lists and reads only the lab question paths of the ward", async () => {
    fetchMock.mockImplementation(async () => reply(200, {}));
    await httpProvider.askLab("What if we alerted at 7?");
    await httpProvider.getLabQuestions();
    await httpProvider.getLabQuestion(7);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/wards/W4/lab/questions", "/wards/W4/lab/questions", "/wards/W4/lab/questions/7"]);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: "What if we alerted at 7?" });
  });
});
