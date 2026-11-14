import { useId } from "react";
import { Link } from "react-router-dom";
import type { SessionRole } from "../types";
import { Thread } from "./Thread";
import styles from "./AskPatient.module.css";

/**
 * A clinician's questions about this one patient: the shared thread, here beside the patient. The agent answers by code from a frozen
 * copy of the patient's data. The page does not render it for an admin or in the fixture build.
 */
export function AskPatient({ patientId, clinicianId, role, onSignOut }: { patientId: string; clinicianId: string; role: SessionRole; onSignOut: () => void }) {
  const heading = useId();
  return (
    <section aria-labelledby={heading}>
      <h2 id={heading} className={styles.heading}>Ask about this patient</h2>
      {(role === "md" || role === "charge_rn") && (
        <p className={styles.link}><Link to={`/ask/patient/${patientId}`}>Open in Ask</Link></p>
      )}
      <Thread scope={{ kind: "patient", id: patientId }} clinicianId={clinicianId} onSignOut={onSignOut} />
    </section>
  );
}
