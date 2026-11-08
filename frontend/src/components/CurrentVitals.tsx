import type { ReactElement } from "react";
import type { Vitals } from "../types";
import styles from "../pages/PatientDetail.module.css";
import { formatBloodPressure, formatOxygen, formatSpo2Scale } from "./format";
import { MeasurementDisplay, type MeasurementProps } from "./MeasurementDisplay";
import { news2Points, type Points } from "./news2";

type Primitive = string | number | boolean;
type RowProps<T> = MeasurementProps<T> & { label: string; points?: Points };

function Row<T>({
  label,
  measurement,
  unit,
  format,
  points,
}: RowProps<T> & { format?: (value: T) => string }): ReactElement {
  return (
    <div className={styles.vitalRow} data-points={points}>
      <span className={styles.vitalLabel}>{label}</span>
      <span className={styles.vitalValue}>
        {/* The overloads on VitalRow enforce a format() for structured values. */}
        <MeasurementDisplay
          measurement={measurement}
          unit={unit}
          format={format as (value: T) => string}
        />
      </span>
      {points !== undefined && points > 0 && (
        <span className={styles.vitalPoints} title="NEWS2 points for this parameter">
          +{points}
        </span>
      )}
    </div>
  );
}

export const VitalRow = Row as {
  <T extends Primitive>(props: RowProps<T> & { format?: (value: T) => string }): ReactElement;
  <T>(props: RowProps<T> & { format: (value: T) => string }): ReactElement;
};

/** The CURRENT VITALS block of the patient detail page. */
export function CurrentVitals({ vitals }: { vitals: Vitals }): ReactElement {
  const p = news2Points(vitals);
  return (
    <>
      <VitalRow label="Heart rate" measurement={vitals.heart_rate} unit="bpm" points={p.heart_rate} />
      <VitalRow label="Respiratory rate" measurement={vitals.respiratory_rate} unit="/min" points={p.respiratory_rate} />
      <VitalRow label="SpO2" measurement={vitals.spo2} unit="%" points={p.spo2} />
      <VitalRow label="Temperature" measurement={vitals.temperature} unit={"°C"} points={p.temperature} />
      <VitalRow
        label="Blood pressure"
        measurement={vitals.blood_pressure}
        format={formatBloodPressure}
        points={p.blood_pressure}
      />
      <VitalRow label="Consciousness" measurement={vitals.consciousness} points={p.consciousness} />
      <VitalRow label="Oxygen" measurement={vitals.supplemental_o2} format={formatOxygen} points={p.supplemental_o2} />
      <VitalRow label="SpO2 scale" measurement={vitals.spo2_scale} format={formatSpo2Scale} />
    </>
  );
}
