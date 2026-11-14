import { useId } from "react";
import { OPEN_CHAT } from "./roles";
import styles from "./AskPatient.module.css";

/**
 * Where the patient page says a clinician can ask about this one patient. The thread itself lives in the chat bubble's panel, so the
 * page holds one copy of it; this button opens that panel. The page does not render it for an admin or in the fixture build.
 */
export function AskPatient() {
  const heading = useId();
  return (
    <section aria-labelledby={heading}>
      <h2 id={heading} className={styles.heading}>Ask about this patient</h2>
      <button type="button" className={styles.open} onClick={() => window.dispatchEvent(new Event(OPEN_CHAT))}>Open the chat</button>
    </section>
  );
}
