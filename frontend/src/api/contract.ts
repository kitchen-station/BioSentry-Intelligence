/**
 * Owns: the shape every data provider must satisfy.
 * Must never: contain implementation, import provider code, or reference env vars.
 * Invariants: I1 (missing is missing), I2 (provenance on every value), I4 (abstention is a type).
 */

import type {
  AbstainedRow,
  Acknowledgement,
  AdmitPatientInput,
  ClinicalNote,
  LabExtractionResult,
  LabPanel,
  NurseVitalsInput,
  PartialLabInput,
  PatientDetail,
  PatientExtractionResult,
  PatientStay,
  VitalsRecorded,
  WardResponse,
} from "../types";

export interface ApiProvider {
  getWard(): Promise<WardResponse>;
  getPatient(id: string): Promise<PatientDetail | null>;
  getNotes(patientId: string): Promise<ClinicalNote[]>;
  addNote(
    patientId: string,
    author: string,
    role: string,
    content: string,
  ): Promise<ClinicalNote>;
  deleteNote(patientId: string, noteId: string): Promise<void>;
  listAllPatients(): Promise<
    Array<{ id: string; name: string; bed: string; ranked: boolean }>
  >;
  /** Every stay the caller may see, on the ward now or already gone. */
  getStays(): Promise<PatientStay[]>;
  admitPatient(input: AdmitPatientInput): Promise<AbstainedRow>;
  enterLabResults(
    patientId: string,
    labs: PartialLabInput,
  ): Promise<LabPanel>;
  recordVitals(patientId: string, input: NurseVitalsInput): Promise<VitalsRecorded>;
  /** The signed-in clinician's acknowledgements: the patient and the NEWS2 score they saw. */
  getAcknowledgements(): Promise<Acknowledgement[]>;
  acknowledge(patientId: string, score: number): Promise<void>;
  extractLabsFromFile(file: File): Promise<LabExtractionResult>;
  extractPatientFromFile(file: File): Promise<PatientExtractionResult>;
}
