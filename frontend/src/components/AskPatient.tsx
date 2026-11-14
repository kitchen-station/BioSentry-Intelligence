import { useCallback, useId } from "react";
import { askPatient, getPatientQuestion, getPatientQuestions } from "../api";
import { MAX, clock, stateText, useQuestions } from "./useQuestions";
import styles from "./AskPatient.module.css";

const GONE = "This patient is no longer on your list.";
const ERRORS = {
  401: "Your session has ended. Sign in again.",
  403: "You cannot ask about this patient.",
  404: GONE,
  422: "The question could not be sent. Use one line, up to 500 characters.",
  429: "Question limit reached. Try again later.",
};

/**
 * A clinician's questions about this one patient. The agent answers by code from a frozen copy of the patient's data. It reads on
 * mount, after a send and on Refresh, and on a timer only while a question is queued or running. The page does not render it for an
 * admin or in the fixture build.
 */
export function AskPatient({ patientId, clinicianId, onSignOut }: { patientId: string; clinicianId: string; onSignOut: () => void }) {
  const heading = useId();
  const list = useCallback(() => getPatientQuestions(patientId), [patientId]);
  const ask = useCallback((q: string) => askPatient(patientId, q), [patientId]);
  const read = useCallback((id: number) => getPatientQuestion(patientId, id), [patientId]);
  const { items, text, setText, sending, error, details, shown, mine, question, send, toggle, refresh } = useQuestions({
    list, ask, read, clinicianId, onSignOut, errors: ERRORS,
  });

  return (
    <section aria-labelledby={heading}>
      <h2 id={heading} className={styles.heading}>Ask about this patient</h2>
      <p className={styles.quiet}>
        The agent sees this patient&apos;s data only, with no name. Do not type a name in your question. Synthetic, not validated.
      </p>
      <form className={styles.form} onSubmit={send}>
        <label className={styles.field}>
          Your question
          <input type="text" maxLength={MAX} value={text} onChange={(e) => setText(e.target.value)} />
          <span className={styles.count} aria-hidden="true">{text.length}/{MAX}</span>
        </label>
        <button type="submit" className={styles.button} disabled={!question || sending || mine}>Ask</button>
      </form>
      {mine && (
        <p className={styles.quiet}>Wait for the answer to your last question. A question that is not taken within one hour fails by itself.</p>
      )}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <button type="button" className={styles.button} onClick={refresh}>Refresh</button>
      {items.length === 0 ? (
        <p className={styles.quiet}>No question yet.</p>
      ) : (
        <ul className={styles.list}>
          {items.map((q) => {
            const region = `${heading}-answer-${q.id}`;
            const open = shown.includes(q.id);
            return (
              <li key={q.id} className={styles.item}>
                <p>{q.question}</p>
                {/* Only the state line is announced: an opened answer is long, and the reader chose to open it. */}
                <p className={styles.meta} aria-live="polite">
                  {q.requested_by !== clinicianId ? `asked by ${q.requested_by} \u00b7 ` : ""}
                  {clock(q.requested_at)} &middot; {stateText(q)}
                </p>
                {q.state === "done" && (
                  <>
                    <button type="button" className={styles.button} aria-expanded={open} aria-controls={region} onClick={() => toggle(q.id)}>
                      {open ? "Hide answer" : "Show answer"}
                    </button>
                    <div id={region} hidden={!open}>
                      {open &&
                        (details[q.id]?.answer ? (
                          <>
                            <pre className={styles.answer}>{details[q.id]?.answer}</pre>
                            <p className={styles.meta}>Answered from the data as it was at {clock(q.requested_at)}.</p>
                          </>
                        ) : (
                          <p className={styles.meta}>No answer text was stored.</p>
                        ))}
                    </div>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
