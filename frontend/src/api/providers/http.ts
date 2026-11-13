/**
 * Owns: mapping ApiProvider methods to real HTTP endpoints.
 * Must never: hold state, cache, or retry. The fetch wrapper in client.ts handles transport.
 * Invariants: I2 (provenance must arrive from server, not be fabricated here).
 *
 * Endpoints align with the contract defined in prompt 03:
 *   GET  /wards/{ward}/rows, GET /wards/{ward}/live
 *   GET  /patients/{id}
 *   POST /session
 *   GET  /acknowledgements, POST /patients/{id}/acknowledgement
 *   plus notes, labs, and extraction sub-resources.
 *
 * Stubs raise -- they do not return plausible values (house rule).
 */

import type { ApiProvider } from "../contract";
import type {
  AbstainedRow,
  Acknowledgement,
  AdmitPatientInput,
  BaselineResponse,
  ClinicalNote,
  LabExtractionResult,
  LabPanel,
  LabRunDetail,
  LabRunItem,
  LiveWard,
  NewLabRun,
  NurseVitalsInput,
  PartialLabInput,
  PatientDetail,
  PatientExtractionResult,
  VitalsRecorded,
  WardOverview,
  PatientStay,
  WardResponse,
} from "../../types";
import {
  ApiError,
  deleteRequest,
  fetchJson,
  postJson,
  uploadFile,
} from "../client";

export const httpProvider: ApiProvider = {
  getWard(): Promise<WardResponse> {
    return fetchJson<WardResponse>("/wards/W4/rows");
  },

  getStays(): Promise<PatientStay[]> {
    return fetchJson<PatientStay[]>("/wards/W4/stays");
  },

  getLiveWard(): Promise<LiveWard> {
    return fetchJson<LiveWard>("/wards/W4/live");
  },

  getOverview(): Promise<WardOverview> {
    return fetchJson<WardOverview>("/wards/W4/overview");
  },

  requestLabRun(input: NewLabRun): Promise<LabRunItem> {
    return postJson<LabRunItem>("/wards/W4/lab/runs", input);
  },

  getLabRuns(): Promise<LabRunItem[]> {
    return fetchJson<LabRunItem[]>("/wards/W4/lab/runs");
  },

  getLabRun(id: number): Promise<LabRunDetail> {
    return fetchJson<LabRunDetail>(`/wards/W4/lab/runs/${id}`);
  },

  async getPatient(id: string): Promise<PatientDetail | null> {
    // The server answers 404 for a patient that does not exist and for one that is not the
    // caller's, identically. The page treats both as "not found".
    try {
      return await fetchJson<PatientDetail>(`/patients/${id}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },

  getBaseline(patientId: string): Promise<BaselineResponse> {
    // No 404 mapping, unlike getPatient: the page shows "could not be loaded" for it, and null
    // is kept for "this provider has no finding" (fixture mode). The id is encoded here (the
    // other calls keep the existing raw convention): a `?` or `#` in it would otherwise send the
    // request to the patient route, whose different answer is not a baseline.
    return fetchJson<BaselineResponse>(`/patients/${encodeURIComponent(patientId)}/baseline`);
  },

  getNotes(patientId: string): Promise<ClinicalNote[]> {
    return fetchJson<ClinicalNote[]>(`/patients/${patientId}/notes`);
  },

  addNote(
    patientId: string,
    author: string,
    role: string,
    content: string,
  ): Promise<ClinicalNote> {
    return postJson<ClinicalNote>(`/patients/${patientId}/notes`, {
      author,
      role,
      content,
    });
  },

  deleteNote(patientId: string, noteId: string): Promise<void> {
    return deleteRequest(`/patients/${patientId}/notes/${noteId}`);
  },

  listAllPatients(): Promise<
    Array<{ id: string; name: string; bed: string; ranked: boolean }>
  > {
    return fetchJson(`/patients`);
  },

  admitPatient(input: AdmitPatientInput): Promise<AbstainedRow> {
    return postJson<AbstainedRow>(`/patients`, input);
  },

  enterLabResults(
    patientId: string,
    labs: PartialLabInput,
  ): Promise<LabPanel> {
    return postJson<LabPanel>(`/patients/${patientId}/labs`, labs);
  },

  recordVitals(patientId: string, input: NurseVitalsInput): Promise<VitalsRecorded> {
    return postJson<VitalsRecorded>(`/patients/${patientId}/vitals`, input);
  },

  getAcknowledgements(): Promise<Acknowledgement[]> {
    return fetchJson<Acknowledgement[]>(`/acknowledgements`);
  },

  acknowledge(patientId: string, score: number): Promise<void> {
    return postJson<void>(`/patients/${patientId}/acknowledgement`, { score });
  },

  extractLabsFromFile(file: File): Promise<LabExtractionResult> {
    return uploadFile<LabExtractionResult>(`/extract/labs`, file);
  },

  extractPatientFromFile(file: File): Promise<PatientExtractionResult> {
    return uploadFile<PatientExtractionResult>(`/extract/patient`, file);
  },
};
