/**
 * Owns: provider selection and public API surface for the frontend.
 * Must never: contain business logic or endpoint definitions.
 * Invariants: components import from here, never from providers or data/fixtures directly.
 *
 * The provider is chosen at BUILD time by VITE_API_URL. An HTTP-mode build carries neither the
 * fixture provider nor the synthetic dataset: the fixture provider is only ever reached through
 * a dynamic import inside a branch the bundler removes when VITE_API_URL is set (a test builds
 * both modes and fails if the dataset appears in the HTTP one). In fixture mode it is a
 * separate chunk, loaded on first use.
 */

import type { ApiProvider } from "./contract";
import { httpProvider } from "./providers/http";
import { getAuthToken, openSession, setAuthToken } from "./client";

const USE_FIXTURES = !import.meta.env.VITE_API_URL;

type FixtureModule = typeof import("./providers/fixture");

// One promise, so a session set at sign-in is applied before the first call that follows it.
let fixtureModule: Promise<FixtureModule> | undefined;
const fixtures = (): Promise<FixtureModule> => (fixtureModule ??= import("./providers/fixture"));

/**
 * The fixture provider, loaded on first use. Every method is already asynchronous, so loading
 * lazily changes nothing for callers. Listed method by method on purpose: adding a method to
 * ApiProvider is a compile error here until it is forwarded.
 */
function lazyFixtureProvider(): ApiProvider {
  return {
    getWard: () => fixtures().then((m) => m.fixtureProvider.getWard()),
    getPatient: (id) => fixtures().then((m) => m.fixtureProvider.getPatient(id)),
    getBaseline: (patientId) => fixtures().then((m) => m.fixtureProvider.getBaseline(patientId)),
    getNotes: (patientId) => fixtures().then((m) => m.fixtureProvider.getNotes(patientId)),
    addNote: (patientId, author, role, content) =>
      fixtures().then((m) => m.fixtureProvider.addNote(patientId, author, role, content)),
    deleteNote: (patientId, noteId) =>
      fixtures().then((m) => m.fixtureProvider.deleteNote(patientId, noteId)),
    listAllPatients: () => fixtures().then((m) => m.fixtureProvider.listAllPatients()),
    getStays: () => fixtures().then((m) => m.fixtureProvider.getStays()),
    admitPatient: (input) => fixtures().then((m) => m.fixtureProvider.admitPatient(input)),
    enterLabResults: (patientId, labs) =>
      fixtures().then((m) => m.fixtureProvider.enterLabResults(patientId, labs)),
    recordVitals: (patientId, input) =>
      fixtures().then((m) => m.fixtureProvider.recordVitals(patientId, input)),
    getAcknowledgements: () => fixtures().then((m) => m.fixtureProvider.getAcknowledgements()),
    acknowledge: (patientId, score) =>
      fixtures().then((m) => m.fixtureProvider.acknowledge(patientId, score)),
    extractLabsFromFile: (file) =>
      fixtures().then((m) => m.fixtureProvider.extractLabsFromFile(file)),
    extractPatientFromFile: (file) =>
      fixtures().then((m) => m.fixtureProvider.extractPatientFromFile(file)),
  };
}

const provider: ApiProvider = USE_FIXTURES ? lazyFixtureProvider() : httpProvider;

export const getWard = provider.getWard;
export const getPatient = provider.getPatient;
export const getBaseline = provider.getBaseline;
export const getNotes = provider.getNotes;
export const addNote = provider.addNote;
export const deleteNote = provider.deleteNote;
export const listAllPatients = provider.listAllPatients;
export const getStays = provider.getStays;
export const admitPatient = provider.admitPatient;
export const enterLabResults = provider.enterLabResults;
export const recordVitals = provider.recordVitals;
export const getAcknowledgements = provider.getAcknowledgements;
export const acknowledge = provider.acknowledge;

/**
 * Whether this build can record nurse-entered vitals. Only the API re-ranks (D4: the frontend
 * never computes a score), so the fixture provider offers no entry; it rejects the call.
 */
export const canRecordVitals = !USE_FIXTURES;
export const extractLabsFromFile = provider.extractLabsFromFile;
export const extractPatientFromFile = provider.extractPatientFromFile;

/** Whether this build talks to the API (and so checks a passphrase) or serves the fixture. */
export const usesServer = !USE_FIXTURES;

/**
 * Sign in. With the API: asks for a token with the passphrase, and rejects with an ApiError when
 * refused (401 or 429). With fixtures there is no server and nothing is checked: it only sets
 * the clinician filter, whatever the passphrase is.
 */
export async function signIn(clinicianId: string, passphrase: string): Promise<void> {
  if (USE_FIXTURES) {
    const m = await fixtures();
    m.setActiveClinician(clinicianId);
  } else {
    await openSession(clinicianId, passphrase);
  }
}

/**
 * Re-arms a session found in sessionStorage after a reload; false when it cannot be. Fixtures keep
 * the clinician filter only in memory, so it is set again. The API token is held in memory only
 * (never stored): after a reload there is none, and the clinician must sign in again.
 */
export function restoreSession(clinicianId: string): boolean {
  if (!USE_FIXTURES) return getAuthToken() !== null;
  void fixtures().then((m) => m.setActiveClinician(clinicianId));
  return true;
}

export function clearSession(): void {
  if (USE_FIXTURES) {
    void fixtures().then((m) => m.setActiveClinician(null));
  } else {
    setAuthToken(null);
  }
}

export { setAuthToken, getAuthToken, ApiError } from "./client";
export type { ApiProvider } from "./contract";
