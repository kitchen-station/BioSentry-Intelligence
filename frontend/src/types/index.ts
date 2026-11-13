/**
 * Contract types for the BioSentry dashboard.
 *
 * ./generated.ts is the API contract, generated from the backend's Pydantic schemas
 * (`make contract`). Do not hand-edit it.
 *
 * The only hand-written shapes here are the generic Measurement<T> / MeasurementValue<T>
 * wrappers components use. The assertions at the bottom fail typecheck if they drift from
 * the generated concrete measurement types.
 *
 * Invariants: I1 (missing is missing), I2 (provenance on every value),
 * I3 (tier separation), I4 (abstention is not a score), I6 (synthetic flag).
 */

import type {
  Acvpu,
  AcvpuMeasurement,
  BloodPressure,
  BloodPressureMeasurement,
  FlagMeasurement,
  NumberMeasurement,
  Provenance,
  Spo2Scale,
  Spo2ScaleMeasurement,
} from "./generated";

export type {
  AbstainedRow,
  Acknowledgement,
  Acvpu,
  AdmissionCondition,
  AdmissionRoute,
  AdmitPatientInput,
  BaselineComputed,
  BaselineForming,
  BaselineResponse,
  BaselineUnavailable,
  BloodPressure,
  CivilStatus,
  ClinicalNote,
  EscalationReason,
  EscalationRule,
  FindingDirection,
  LabExtractionResult,
  LabPanel,
  LiveWard,
  NurseVitalsInput,
  PartialAdmitPatientInput,
  PartialLabInput,
  PatientDetail,
  PatientExtractionResult,
  Provenance,
  ProvenanceSource,
  QualityLevel,
  ScoreRow,
  Session,
  SessionRole,
  Sex,
  Spo2Scale,
  Tier,
  TierAScore,
  TierBData,
  TierBFinding,
  TierBreakdown,
  TierCAttribution,
  TierCData,
  TierCInput,
  TierStatus,
  Trend,
  VitalHistory,
  VitalHistoryFull,
  VitalTrends,
  WardOverview,
  Vitals,
  VitalsRecorded,
  PatientStay,
  WardResponse,
} from "./generated";

// --- Measurement primitives (generic wrappers over the generated concrete types) ---

export type MeasurementValue<T> =
  | { readonly present: true; readonly value: T }
  | { readonly present: false; readonly reason: string };

export interface Measurement<T> {
  readonly data: MeasurementValue<T>;
  readonly provenance: Provenance;
}

// --- Drift guard: each generated measurement must match Measurement<its value type> ---

type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type MustBeTrue<T extends true> = T;

export type MeasurementContractChecks = [
  MustBeTrue<Same<NumberMeasurement, Measurement<number>>>,
  MustBeTrue<Same<FlagMeasurement, Measurement<boolean>>>,
  MustBeTrue<Same<AcvpuMeasurement, Measurement<Acvpu>>>,
  MustBeTrue<Same<Spo2ScaleMeasurement, Measurement<Spo2Scale>>>,
  MustBeTrue<Same<BloodPressureMeasurement, Measurement<BloodPressure>>>,
];
