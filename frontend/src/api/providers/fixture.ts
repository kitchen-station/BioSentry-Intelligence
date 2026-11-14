/**
 * Owns: adapting data/fixtures.ts to the ApiProvider interface.
 * Must never: contain business logic or data. Pure delegation.
 * Invariants: inherits from fixtures (I1, I2, I4, I6).
 */

import type { ApiProvider } from "../contract";
import * as fixtures from "../../data/fixtures";

export const setActiveClinician = fixtures.setActiveClinician;

export const fixtureProvider: ApiProvider = {
  getWard: fixtures.getWard,
  getPatient: fixtures.getPatient,
  // The frontend never computes a finding (D4), so fixture mode has none to show.
  getBaseline: () => Promise.resolve(null),
  getNotes: fixtures.getNotes,
  addNote: fixtures.addNote,
  deleteNote: fixtures.deleteNote,
  listAllPatients: fixtures.listAllPatients,
  getStays: fixtures.getStays,
  // No simulator behind the fixture, so no live view.
  getLiveWard: () => Promise.resolve(null),
  getOverview: () => Promise.reject(new Error("The overview needs the API")),
  requestLabRun: () => Promise.reject(new Error("Simulated runs need the server")),
  getLabRuns: () => Promise.reject(new Error("Simulated runs need the server")),
  getLabRun: () => Promise.reject(new Error("Simulated runs need the server")),
  askPatient: () => Promise.reject(new Error("Patient questions need the server")),
  getPatientQuestions: () => Promise.reject(new Error("Patient questions need the server")),
  getPatientQuestion: () => Promise.reject(new Error("Patient questions need the server")),
  askLab: () => Promise.reject(new Error("Lab questions need the server")),
  getLabQuestions: () => Promise.reject(new Error("Lab questions need the server")),
  getLabQuestion: () => Promise.reject(new Error("Lab questions need the server")),
  getPatientHistory: () => Promise.reject(new Error("The stay history needs the server")),
  admitPatient: fixtures.admitPatient,
  enterLabResults: fixtures.enterLabResults,
  getAcknowledgements: fixtures.getAcknowledgements,
  acknowledge: fixtures.acknowledge,
  // The frontend never computes a score (D4), so fixture mode cannot re-rank a patient.
  recordVitals: () =>
    Promise.reject(new Error("Recording vitals needs the API: the frontend never computes a score")),
  extractLabsFromFile: fixtures.extractLabsFromFile,
  extractPatientFromFile: fixtures.extractPatientFromFile,
};
