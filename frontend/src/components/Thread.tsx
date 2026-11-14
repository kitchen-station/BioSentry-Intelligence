import { useCallback, useEffect, useId, useRef } from "react";
import type { QuestionItem } from "../types";
import { askLab, askPatient, getLabQuestion, getLabQuestions, getPatientQuestion, getPatientQuestions } from "../api";
import { MAX, clock, stateLine, useQuestions } from "./useQuestions";
import styles from "./Thread.module.css";

export type Scope = { kind: "ward" } | { kind: "patient"; id: string };

const COMMON = {
  401: "Your session has ended. Sign in again.",
  422: "The question could not be sent. Use one line, up to 500 characters.",
};
const WARD_ERRORS = {
  ...COMMON,
  403: "Only a physician or a charge nurse can ask.",
  404: "This ward is not on this server.",
  429: "Question limit reached: one open question each, ten a day each, five waiting on the ward. Try again later.",
};
const PATIENT_ERRORS = {
  ...COMMON,
  403: "You cannot ask about this patient.",
  404: "This patient is no longer on your list.",
  429: "Question limit reached. Try again later.",
};

// The stored record comes from another machine, so every read here tolerates a missing key or a value of any type.
const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | undefined => (v == null ? undefined : typeof v === "string" ? v : JSON.stringify(v));

function Experiment({ report }: { report: unknown }) {
  const e = rec(rec(report).experiment);
  const [template, tool, args] = [str(e.template), str(e.tool), str(e.args)];
  if (template === undefined && tool === undefined && args === undefined) return <p className={styles.meta}>The stored record names no experiment.</p>;
  return (
    <>
      {template !== undefined && <p className={styles.meta}>Template: {template}</p>}
      {tool !== undefined && <p className={styles.meta}>Tool: {tool}</p>}
      {args !== undefined && (
        <>
          <p className={styles.meta}>Arguments:</p>
          <pre className={styles.pre}>{args}</pre>
        </>
      )}
    </>
  );
}

function Basis({ scope, q, report }: { scope: Scope; q: QuestionItem; report: unknown }) {
  return (
    <details className={styles.basis}>
      <summary>What this is based on</summary>
      {scope.kind === "ward" ? (
        <>
          <Experiment report={report} />
          {rec(report).grounding === "ok" && <p className={styles.meta}>Every number in the answer was checked against this run&apos;s result.</p>}
          <pre className={styles.pre} tabIndex={0} aria-label={`Stored record of question ${q.id}`}>{JSON.stringify(report, null, 2)}</pre>
        </>
      ) : (
        <p className={styles.meta}>Answered from this patient&apos;s data.</p>
      )}
    </details>
  );
}

/**
 * One thread of questions and answers: the ward's own, or one patient's. It is the only implementation: the Ask page and the
 * patient page's panel both render it. The composer sits under the turns, oldest at the top.
 */
export function Thread({ scope, clinicianId, onSignOut }: { scope: Scope; clinicianId: string; onSignOut: () => void }) {
  const id = useId();
  const patientId = scope.kind === "patient" ? scope.id : null;
  // Each call is chosen when it runs, so a build or a test that holds only one kind of question never reads the other.
  const list = useCallback(() => (patientId ? getPatientQuestions(patientId) : getLabQuestions()), [patientId]);
  const ask = useCallback((q: string) => (patientId ? askPatient(patientId, q) : askLab(q)), [patientId]);
  const read = useCallback((n: number) => (patientId ? getPatientQuestion(patientId, n) : getLabQuestion(n)), [patientId]);
  const { items, turns, text, setText, sending, error, details, now, mine, question, send, loadAnswer, refresh, sent } = useQuestions({
    list, ask, read, clinicianId, onSignOut, errors: patientId ? PATIENT_ERRORS : WARD_ERRORS,
  });

  const end = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (sent > 0) end.current?.scrollIntoView?.({ block: "end" }); // jsdom has no scrollIntoView
  }, [sent]);

  const otherRunning = items.some((q) => q.state === "running");

  return (
    <div>
      <button type="button" className={styles.button} onClick={refresh}>Refresh</button>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {turns.length === 0 ? (
        <p className={styles.quiet}>No question yet.</p>
      ) : (
        <ul role="list" className={styles.thread}>
          {turns.map((q, i) => {
            const d = details[q.id];
            return (
              <li key={q.id} className={styles.turn} ref={i === turns.length - 1 ? end : undefined}>
                <div className={styles.question}>
                  <p className={styles.who}>{q.requested_by === clinicianId ? "You" : q.requested_by} &middot; {clock(q.requested_at)}</p>
                  <p className={styles.text}>{q.question}</p>
                </div>
                <div className={styles.answer}>
                  <p className={styles.who}>Research agent</p>
                  {/* Only the state line is announced: an answer is long, and the reader chose to be here. */}
                  <p className={styles.text} aria-live="polite">{stateLine(q, now, otherRunning)}</p>
                  {q.state === "done" &&
                    (d ? (
                      <>
                        {d.answer ? <pre className={styles.reply}>{d.answer}</pre> : <p className={styles.meta}>No answer text was stored.</p>}
                        <Basis scope={scope} q={q} report={d.report} />
                        <p className={styles.meta}>Synthetic, not validated.</p>
                      </>
                    ) : (
                      <button type="button" className={styles.button} onClick={() => void loadAnswer(q.id)}>Show answer</button>
                    ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <form className={styles.composer} onSubmit={send}>
        <p className={styles.warning}>
          <strong>Warning. </strong>
          {scope.kind === "ward"
            ? "Do not type anything about a patient: no name, no bed, no id. Every clinician of this ward can read each question, and it is kept."
            : "The agent sees this patient's data only, with no name. Do not type a name in your question."}
        </p>
        {scope.kind === "ward" && (
          <p className={styles.quiet}>
            The agent answers from simulated trials only. For example: What if we alerted at a NEWS2 total of 7 or more?
          </p>
        )}
        <div className={styles.row}>
          <label className={styles.field} htmlFor={`${id}-q`}>
            Your question
            <input id={`${id}-q`} type="text" maxLength={MAX} value={text} disabled={mine} onChange={(e) => setText(e.target.value)} />
            <span className={styles.count} aria-hidden="true">{text.length}/{MAX}</span>
          </label>
          <button type="submit" className={styles.button} disabled={!question || sending || mine}>Send</button>
        </div>
        {mine && <p className={styles.quiet}>Wait for the answer to your last question.</p>}
      </form>
    </div>
  );
}
