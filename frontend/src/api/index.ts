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
import type { SessionRole } from "../types";
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
    getLiveWard: () => fixtures().then((m) => m.fixtureProvider.getLiveWard()),
    getOverview: () => fixtures().then((m) => m.fixtureProvider.getOverview()),
    admitPatient: (input) => fixtures().then((m) => m.fixtureProvider.admitPatient(input)),
    enterLabResults: (patientId, labs) =>
      fixtures().then((m) => m.fixtureProvider.enterLabResults(patientId, labs)),
    recordVitals: (patientId, input) =>
      fixtures().then((m) => m.fixtureProvider.recordVitals(patientId, input)),
    getAcknowledgements: () => fixtures().then((m) => m.fixtureProvider.getAcknowledgements()),
    acknowledge: (patientId, score) =>
      fixtures().then((m) => m.fixtureProvider.acknowledge(patientId, score)),
    requestLabRun: (input) => fixtures().then((m) => m.fixtureProvider.requestLabRun(input)),
    getLabRuns: () => fixtures().then((m) => m.fixtureProvider.getLabRuns()),
    getLabRun: (id) => fixtures().then((m) => m.fixtureProvider.getLabRun(id)),
    askPatient: (patientId, question) => fixtures().then((m) => m.fixtureProvider.askPatient(patientId, question)),
    getPatientQuestions: (patientId) => fixtures().then((m) => m.fixtureProvider.getPatientQuestions(patientId)),
    getPatientQuestion: (patientId, questionId) =>
      fixtures().then((m) => m.fixtureProvider.getPatientQuestion(patientId, questionId)),
    askLab: (question) => fixtures().then((m) => m.fixtureProvider.askLab(question)),
    getLabQuestions: () => fixtures().then((m) => m.fixtureProvider.getLabQuestions()),
    getLabQuestion: (id) => fixtures().then((m) => m.fixtureProvider.getLabQuestion(id)),
    getPatientHistory: (patientId) => fixtures().then((m) => m.fixtureProvider.getPatientHistory(patientId)),
    getWardResearch: () => fixtures().then((m) => m.fixtureProvider.getWardResearch()),
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
export const getLiveWard = provider.getLiveWard;
export const getOverview = provider.getOverview;
export const admitPatient = provider.admitPatient;
export const enterLabResults = provider.enterLabResults;
export const recordVitals = provider.recordVitals;
export const getAcknowledgements = provider.getAcknowledgements;
export const acknowledge = provider.acknowledge;
export const requestLabRun = provider.requestLabRun;
export const getLabRuns = provider.getLabRuns;
export const getLabRun = provider.getLabRun;
export const askPatient = provider.askPatient;
export const getPatientQuestions = provider.getPatientQuestions;
export const getPatientQuestion = provider.getPatientQuestion;
export const askLab = provider.askLab;
export const getLabQuestions = provider.getLabQuestions;
export const getLabQuestion = provider.getLabQuestion;
export const getPatientHistory = provider.getPatientHistory;
export const getWardResearch = provider.getWardResearch;

/**
 * Whether this build can record nurse-entered vitals. Only the API re-ranks (D4: the frontend
 * never computes a score), so the fixture provider offers no entry; it rejects the call.
 */
export const canRecordVitals = !USE_FIXTURES;
/** Whether this build can show the admin overview. Only the API knows who is assigned and what was done. */
export const hasOverview = !USE_FIXTURES;
// Only the API stores lab runs; a worker beside it runs them.
export const hasLabRuns = !USE_FIXTURES;
// Only the API stores patient questions; a worker beside it answers them.
export const hasPatientQuestions = !USE_FIXTURES;
// Only the API stores the lab's questions; a worker beside it answers them.
export const hasLabQuestions = !USE_FIXTURES;
// Only the API rebuilds a stay; each read is one audit row.
export const hasHistory = !USE_FIXTURES;
// Only the API holds the whole ward's numbers; each read is one audit row.
export const hasWardResearch = !USE_FIXTURES;
export const extractLabsFromFile = provider.extractLabsFromFile;
export const extractPatientFromFile = provider.extractPatientFromFile;

/** Whether this build talks to the API (and so checks a passphrase) or serves the fixture. */
export const usesServer = !USE_FIXTURES;

/**
 * Sign in. With the API: asks for a token with the passphrase, and rejects with an ApiError when
 * refused (401 or 429). With fixtures there is no server and nothing is checked: it only sets
 * the clinician filter, whatever the passphrase is. Returns the role the server holds for the
 * clinician (null with fixtures); the page must use it, never its own idea of the role.
 */
export async function signIn(clinicianId: string, passphrase: string): Promise<SessionRole | null> {
  if (USE_FIXTURES) {
    const m = await fixtures();
    m.setActiveClinician(clinicianId);
    return null;
  }
  return openSession(clinicianId, passphrase);
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
