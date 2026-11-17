import { useId } from "react";
import { Link } from "react-router-dom";
import styles from "./AskPatient.module.css";

/**
 * Where the patient page says a clinician can ask about this one patient: a link to the patient's
 * thread on the Ask page. The page renders it only for a role that can open that page.
 */
export function AskPatient({ patientId }: { patientId: string }) {
  const heading = useId();
  return (
    <section aria-labelledby={heading}>
      <h2 id={heading} className={styles.heading}>Ask about this patient</h2>
      <Link className="hw-button" to={`/ask/patient/${encodeURIComponent(patientId)}`}>
        <span className="hw-icon fa-solid fa-comments" aria-hidden="true" />
        Open the chat
      </Link>
    </section>
  );
}
