/**
 * Owns: the shape every data provider must satisfy.
 * Must never: contain implementation, import provider code, or reference env vars.
 * Invariants: I1 (missing is missing), I2 (provenance on every value), I4 (abstention is a type).
 */

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
  PatientStay,
  VitalsRecorded,
  WardOverview,
  WardResponse,
} from "../types";

export interface ApiProvider {
  getWard(): Promise<WardResponse>;
  getPatient(id: string): Promise<PatientDetail | null>;
  /**
   * The patient's change-from-own-baseline finding (Tier B, research, never ranks). Null when the
   * provider has none to give (fixture mode); a missing or not-yours patient rejects.
   */
  getBaseline(patientId: string): Promise<BaselineResponse | null>;
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
  /** What the ward simulator last published, for the caller. Null when there is no simulator (fixture mode). */
  getLiveWard(): Promise<LiveWard | null>;
  /** The admin's read-only ward overview. Only the API has one; any other caller is refused (403). */
  getOverview(): Promise<WardOverview>;
  admitPatient(input: AdmitPatientInput): Promise<AbstainedRow>;
  enterLabResults(
    patientId: string,
    labs: PartialLabInput,
  ): Promise<LabPanel>;
  recordVitals(patientId: string, input: NurseVitalsInput): Promise<VitalsRecorded>;
  /** The signed-in clinician's acknowledgements: the patient and the NEWS2 score they saw. */
  getAcknowledgements(): Promise<Acknowledgement[]>;
  acknowledge(patientId: string, score: number): Promise<void>;
  /** Asks for one simulated compare run. Only an md or the charge nurse may; the lab worker runs it. */
  requestLabRun(input: NewLabRun): Promise<LabRunItem>;
  getLabRuns(): Promise<LabRunItem[]>;
  getLabRun(id: number): Promise<LabRunDetail>;
  extractLabsFromFile(file: File): Promise<LabExtractionResult>;
  extractPatientFromFile(file: File): Promise<PatientExtractionResult>;
}
