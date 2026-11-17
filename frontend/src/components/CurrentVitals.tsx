import type { ReactElement } from "react";
import type { Vitals } from "../types";
import styles from "../pages/PatientDetail.module.css";
import { enteredByNurse, formatBloodPressure, formatOxygen, formatSpo2Scale } from "./format";
import { Flip } from "./Flip";
import { MeasurementDisplay, type MeasurementProps } from "./MeasurementDisplay";
import { news2Points, type Points } from "./news2";

type Primitive = string | number | boolean;
type RowProps<T> = MeasurementProps<T> & { label: string; points?: Points; flip?: boolean };

function Row<T>({
  label,
  measurement,
  unit,
  format,
  now,
  points,
  flip,
}: RowProps<T> & { format?: (value: T) => string }): ReactElement {
  return (
    <div className={styles.vitalRow} data-points={points}>
      <dt className={styles.vitalLabel}>{label}</dt>
      <dd className={styles.vitalValue}>
        {/* The overloads on VitalRow enforce a format() for structured values. */}
        <Flip still={!flip}>
          <MeasurementDisplay
            measurement={measurement}
            unit={unit}
            now={now}
            format={format as (value: T) => string}
          />
        </Flip>
        {measurement.data.present && measurement.provenance.source === "nurse" && (
          <span className={styles.nurseNote}>
            {" "}{enteredByNurse(Date.parse(measurement.provenance.observed_at), now)}
          </span>
        )}
      </dd>
      {points !== undefined && points > 0 && (
        <dd className={styles.vitalPoints}>
          <span className="hw-tag hw-tag--danger hw-tag--round" title="NEWS2 points for this parameter">
            +{points}
          </span>
        </dd>
      )}
    </div>
  );
}

export const VitalRow = Row as {
  <T extends Primitive>(props: RowProps<T> & { format?: (value: T) => string }): ReactElement;
  <T>(props: RowProps<T> & { format: (value: T) => string }): ReactElement;
};

/** The CURRENT VITALS block of the patient detail page: rows of a <dl>, which the page provides. */
export function CurrentVitals({ vitals, now }: { vitals: Vitals; now: number }): ReactElement {
  const p = news2Points(vitals);
  return (
    <>
      <VitalRow now={now} label="Heart rate" measurement={vitals.heart_rate} unit="bpm" flip points={p.heart_rate} />
      <VitalRow now={now} label="Respiratory rate" measurement={vitals.respiratory_rate} unit="/min" flip points={p.respiratory_rate} />
      <VitalRow now={now} label="SpO2" measurement={vitals.spo2} unit="%" flip points={p.spo2} />
      <VitalRow now={now} label="Temperature" measurement={vitals.temperature} unit={"°C"} flip points={p.temperature} />
      <VitalRow
        now={now}
        label="Blood pressure"
        measurement={vitals.blood_pressure}
        format={formatBloodPressure}
        flip points={p.blood_pressure}
      />
      <VitalRow now={now} label="Consciousness" measurement={vitals.consciousness} flip points={p.consciousness} />
      <VitalRow now={now} label="Oxygen" measurement={vitals.supplemental_o2} format={formatOxygen} flip points={p.supplemental_o2} />
      <VitalRow now={now} label="SpO2 scale" measurement={vitals.spo2_scale} format={formatSpo2Scale} />
      <Rhythm rhythm={vitals.rhythm} />
    </>
  );
}

/** Display only: never scored. The source text is on screen and in the accessible label. */
export function Rhythm({ rhythm }: { rhythm: Vitals["rhythm"] }): ReactElement {
  const shown = rhythm.data.present ? (rhythm.data.value === "af" ? "AF" : "Sinus") : "--";
  return (
    <div className={styles.vitalRow}>
      <dt className={styles.vitalLabel}>Rhythm</dt>
      <dd className={styles.vitalValue} aria-label={`Rhythm ${shown === "--" ? "not recorded" : shown}. ${rhythm.source_text}`}>
        {shown}
        <span className={styles.nurseNote}> {rhythm.source_text}</span>
      </dd>
    </div>
  );
}
