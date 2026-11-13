/**
 * The fetch wrapper: the bearer token it carries, how it reports a refusal, and the DELETE and
 * upload helpers. The network is a stubbed fetch.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteRequest, getAuthToken, openSession, setAuthToken, uploadFile } from "./client";

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

const sent = (i = 0) => fetchMock.mock.calls[i] as [string, RequestInit];

describe("the session token", () => {
  it("is what sign-in returned, and gone after sign-out", async () => {
    fetchMock.mockResolvedValue(Response.json({ token: "t-1", role: "charge_rn" }));
    await expect(openSession("rn-manalo", "a passphrase")).resolves.toBe("charge_rn"); // the server's role
    expect(getAuthToken()).toBe("t-1");
    setAuthToken(null);
    expect(getAuthToken()).toBeNull();
  });

  it("is asked for with the passphrase in the body of POST /session, and nowhere else", async () => {
    fetchMock.mockResolvedValue(Response.json({ token: "t-1" }));
    await openSession("rn-manalo", "a passphrase");
    const [path, init] = sent();
    expect(path).toBe("/session");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ clinician_id: "rn-manalo", passphrase: "a passphrase" });
    expect(path).not.toContain("passphrase");
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it.each([401, 429])("is refused with the API's %s, and there is no token", async (status) => {
    fetchMock.mockResolvedValue(Response.json({ detail: "no" }, { status }));
    const failure = await openSession("rn-manalo", "wrong").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(status);
    expect(getAuthToken()).toBeNull();
  });

  it("does not keep the token of an earlier sign-in while a new one is refused", async () => {
    setAuthToken("old");
    fetchMock.mockResolvedValue(Response.json({ detail: "no" }, { status: 401 }));
    await openSession("rn-reyes", "wrong").catch(() => undefined);
    expect(getAuthToken()).toBeNull();
  });
});

describe("deleteRequest", () => {
  it("sends DELETE with the bearer token and resolves on 204", async () => {
    setAuthToken("t-1");
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(deleteRequest("/patients/p-001/notes/n-001")).resolves.toBeUndefined();
    const [path, init] = sent();
    expect(path).toBe("/patients/p-001/notes/n-001");
    expect(init.method).toBe("DELETE");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer t-1");
  });
});

describe("uploadFile", () => {
  it("posts the file as multipart form data, under 'file' unless told otherwise", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(Response.json({ ok: true })));
    const file = new File(["%PDF"], "labs.pdf", { type: "application/pdf" });

    await expect(uploadFile("/extract/labs", file)).resolves.toEqual({ ok: true });
    await uploadFile("/extract/patient", file, "document");

    const [, first] = sent(0);
    expect(first.method).toBe("POST");
    expect((first.body as FormData).get("file")).toBeInstanceOf(File);
    // FormData sets its own multipart boundary: the wrapper must not claim JSON.
    expect(new Headers(first.headers).has("Content-Type")).toBe(false);
    expect((sent(1)[1].body as FormData).get("document")).toBeInstanceOf(File);
  });
});

describe("a refused request", () => {
  it("is an ApiError carrying the JSON body", async () => {
    fetchMock.mockResolvedValue(Response.json({ detail: "no" }, { status: 422, statusText: "Unprocessable" }));
    const failure = await deleteRequest("/x").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 422, body: { detail: "no" } });
  });

  // KNOWN BUG (client.ts, the !res.ok branch): res.json() consumes the body before it fails, so
  // the res.text() fallback throws "Body is unusable" and a non-JSON refusal (a proxy's 502 page)
  // surfaces as a TypeError, not an ApiError. Remove `.fails` when it is fixed (read text once,
  // then try JSON.parse).
  it.fails("is an ApiError carrying the text when the body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 }));
    const failure = await deleteRequest("/x").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 502, body: "<html>Bad gateway</html>" });
  });
});
