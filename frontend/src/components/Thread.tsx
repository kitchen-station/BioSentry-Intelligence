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
  if (template === undefined && tool === undefined && args === undefined) return <p className="hw-caption">The stored record names no experiment.</p>;
  return (
    <>
      {template !== undefined && <p className="hw-caption">Template: {template}</p>}
      {tool !== undefined && <p className="hw-caption">Tool: {tool}</p>}
      {args !== undefined && (
        <>
          <p className="hw-caption">Arguments:</p>
          <pre className={styles.pre}>{args}</pre>
        </>
      )}
    </>
  );
}

function Basis({ scope, q, report }: { scope: Scope; q: QuestionItem; report: unknown }) {
  return (
    <details className={`hw-accordion hw-accordion__item ${styles.basis}`}>
      <summary className="hw-accordion__summary">What this is based on</summary>
      <div className={`hw-accordion__body ${styles.basisBody}`}>
        {scope.kind === "ward" ? (
          <>
            <Experiment report={report} />
            {rec(report).grounding === "ok" && <p className="hw-caption">Every number in the answer was checked against this run&apos;s result.</p>}
            <pre className={styles.pre} tabIndex={0} aria-label={`Stored record of question ${q.id}`}>{JSON.stringify(report, null, 2)}</pre>
          </>
        ) : (
          <p className="hw-caption">Answered from this patient&apos;s data.</p>
        )}
      </div>
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
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <button type="button" className="hw-button hw-button--sm" onClick={refresh}>
          <span className="hw-icon fa-solid fa-rotate" aria-hidden="true" />
          Refresh
        </button>
      </div>
      {error && (
        <p role="alert" className="hw-alert hw-alert--danger">
          <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
          <span className="hw-alert__body">{error}</span>
        </p>
      )}
      {turns.length === 0 ? (
        <div className="hw-empty">
          <span className="hw-empty__art" aria-hidden="true"><span className="hw-icon fa-regular fa-comments" /></span>
          <p className="hw-empty__title">No question yet.</p>
        </div>
      ) : (
        <ul role="list" className={styles.turns}>
          {turns.map((q, i) => {
            const d = details[q.id];
            return (
              <li key={q.id} className={styles.turn} ref={i === turns.length - 1 ? end : undefined}>
                <div className={styles.question}>
                  <p className={styles.who}>{q.requested_by === clinicianId ? "You" : q.requested_by} &middot; {clock(q.requested_at)}</p>
                  <p className={styles.text}>{q.question}</p>
                </div>
                <div className={styles.answer}>
                  <p className={styles.agent}>Research agent</p>
                  {/* Only the state line is announced: an answer is long, and the reader chose to be here. */}
                  <p className={styles.state} aria-live="polite">{stateLine(q, now, otherRunning)}</p>
                  {q.state === "done" &&
                    (d ? (
                      <>
                        {d.answer ? <pre className={styles.reply}>{d.answer}</pre> : <p className="hw-caption">No answer text was stored.</p>}
                        <Basis scope={scope} q={q} report={d.report} />
                        <p className="hw-caption">Synthetic, not validated.</p>
                      </>
                    ) : (
                      <button type="button" className={`hw-button hw-button--sm ${styles.show}`} onClick={() => void loadAnswer(q.id)}>Show answer</button>
                    ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <form className={styles.composer} onSubmit={send}>
        <p className="hw-alert hw-alert--warning">
          <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
          {/* The bold word and the text share one parent, so the sentence reads as one. */}
          <span className="hw-alert__body">
            <strong>Warning. </strong>
            {scope.kind === "ward"
              ? "Do not type anything about a patient: no name, no bed, no id. Every clinician of this ward can read each question, and it is kept."
              : "The agent sees this patient's data only, with no name. Do not type a name in your question."}
          </span>
        </p>
        {scope.kind === "ward" && (
          <p className="hw-caption">
            The agent answers from simulated trials only. For example: What if we alerted at a NEWS2 total of 7 or more?
          </p>
        )}
        <div className={styles.row}>
          <div className={`hw-field hw-field--full ${styles.field}`}>
            <label className="hw-field__label" htmlFor={`${id}-q`}>Your question</label>
            <span className="hw-input">
              <input id={`${id}-q`} className="hw-input__control" type="text" maxLength={MAX} value={text} disabled={mine} onChange={(e) => setText(e.target.value)} />
              <span className="hw-input__affix hw-caption" aria-hidden="true">{text.length}/{MAX}</span>
            </span>
          </div>
          <button type="submit" className="hw-button hw-button--primary" disabled={!question || sending || mine}>Send</button>
        </div>
        {mine && <p className="hw-caption">Wait for the answer to your last question.</p>}
      </form>
    </div>
  );
}
