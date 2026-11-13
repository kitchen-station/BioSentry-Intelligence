import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { PatientQuestionItem } from "../types";
import { ApiError, askPatient, getPatientQuestion, getPatientQuestions } from "../api";
import { formatClock } from "./format";
import styles from "./AskPatient.module.css";

// The same pace as the lab run list.
const POLL_MS = 5_000;
const MAX = 500;
const GONE = "This patient is no longer on your list.";

const clock = (iso: string) => formatClock(Date.parse(iso));
const isOpen = (q: PatientQuestionItem) => q.state === "queued" || q.state === "running";
// The worker's own texts already begin with the word, so it is not said twice.
const said = (word: string, reason: string | null) => `${word}: ${(reason ?? "no reason given").replace(`${word}: `, "")}`;
const stateText = (q: PatientQuestionItem): string =>
  ({
    queued: "Waiting",
    running: q.claimed_at ? `Working since ${clock(q.claimed_at)}` : "Working",
    done: "Answered",
    refused: said("Refused", q.reason),
    failed: said("Failed", q.reason),
  })[q.state] ?? q.state; // a state from a newer server is shown as it came

/**
 * A clinician's questions about this one patient. The agent answers by code from a frozen copy of the patient's data. It reads on
 * mount, after a send and on Refresh, and on a timer only while a question is queued or running. The page does not render it for an
 * admin or in the fixture build.
 */
export function AskPatient({ patientId, clinicianId, onSignOut }: { patientId: string; clinicianId: string; onSignOut: () => void }) {
  const heading = useId();
  const [items, setItems] = useState<PatientQuestionItem[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false); // a failed read stops the timer until a read works
  // The answer of each question is read once; `shown` says which are open.
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [shown, setShown] = useState<number[]>([]);

  const signOut = useRef(onSignOut);
  useEffect(() => {
    signOut.current = onSignOut;
  }, [onSignOut]);
  const fail = useCallback((failure: unknown) => {
    const status = failure instanceof ApiError ? failure.status : 0;
    if (status === 401) signOut.current();
    setError(
      {
        401: "Your session has ended. Sign in again.",
        403: "You cannot ask about this patient.",
        404: GONE,
        422: "The question could not be sent. Use one line, up to 500 characters.",
        429: "Question limit reached. Try again later.",
      }[status] ?? "Cannot reach the ward server.",
    );
  }, []);

  const reading = useRef(false);
  const round = useRef(0); // an answer that comes back late must not replace a newer list
  const load = useCallback(() => {
    reading.current = true;
    const n = ++round.current;
    return getPatientQuestions(patientId)
      .then((rows) => {
        if (n !== round.current) return;
        setItems(rows);
        setStalled(false);
        setError(null);
      })
      .catch((failure: unknown) => {
        setStalled(true);
        fail(failure);
      })
      .finally(() => {
        reading.current = false;
      });
  }, [patientId, fail]);
  const refresh = useCallback(() => {
    if (!reading.current) void load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = items.some(isOpen);
  useEffect(() => {
    if (!pending || stalled) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [pending, stalled, refresh]);

  const mine = items.some((q) => q.requested_by === clinicianId && isOpen(q));
  const question = text.trim();

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question || sending || mine) return;
    setSending(true);
    askPatient(patientId, question)
      .then(() => {
        setText("");
        setError(null);
        return load();
      })
      .catch(fail)
      .finally(() => setSending(false));
  };

  const toggle = (id: number) => {
    if (shown.includes(id)) return setShown(shown.filter((s) => s !== id));
    if (id in answers) return setShown([...shown, id]);
    getPatientQuestion(patientId, id)
      .then((d) => {
        setAnswers((a) => ({ ...a, [id]: d.answer ?? "" }));
        setShown((s) => [...s, id]);
        setError(null);
      })
      .catch(fail);
  };

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
                        (answers[q.id] ? (
                          <>
                            <pre className={styles.answer}>{answers[q.id]}</pre>
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
