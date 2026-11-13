/**
 * Owns: HTTP transport -- fetch wrapper, auth token, error handling.
 * Must never: know about specific endpoints or business types.
 * Invariants: none directly; serves providers that enforce them.
 */

import type { SessionRole } from "../types";

const BASE_URL: string = import.meta.env.VITE_API_URL ?? "";

let _token: string | null = null;
// Requests wait for a sign-in in flight. The generation drops a token that arrives after sign-out.
let _pending: Promise<void> = Promise.resolve();
let _generation = 0;

export function setAuthToken(token: string | null): void {
  _generation++;
  _pending = Promise.resolve();
  _token = token;
}

/**
 * Signs in (POST /session). Resolves with the role the server holds once the token is held; rejects with the ApiError when the
 * API refuses (401: wrong passphrase, unknown clinician or none set; 429: too many failures), so
 * the sign-in page can say so. Until it settles, other requests wait; after a refusal they go out
 * without a token and the API answers 401: the client fails closed. The passphrase is only ever
 * in this request body: never stored, never logged.
 */
export function openSession(clinicianId: string, passphrase: string): Promise<SessionRole> {
  const generation = ++_generation;
  _token = null;
  const opening = request<{ token: string; role: SessionRole }>(
    "/session",
    { method: "POST", body: JSON.stringify({ clinician_id: clinicianId, passphrase }) },
    false,
  ).then((r) => {
    if (generation === _generation) _token = r.token;
    return r.role;
  });
  _pending = opening.then(() => {}, () => {});
  return opening;
}

export function getAuthToken(): string | null {
  return _token;
}

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;

  constructor(status: number, statusText: string, body: unknown) {
    super(`API ${status}: ${statusText}`);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  waitForSession = true,
): Promise<T> {
  if (waitForSession) await _pending;
  const headers = new Headers(init.headers);

  if (_token) {
    headers.set("Authorization", `Bearer ${_token}`);
  }

  if (
    !headers.has("Content-Type") &&
    init.body &&
    typeof init.body === "string"
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...init, headers });

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, res.statusText, body);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

export function fetchJson<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteRequest(path: string): Promise<void> {
  return request<void>(path, { method: "DELETE" });
}

export function uploadFile<T>(
  path: string,
  file: File,
  fieldName = "file",
): Promise<T> {
  const form = new FormData();
  form.append(fieldName, file);
  return request<T>(path, { method: "POST", body: form });
}
