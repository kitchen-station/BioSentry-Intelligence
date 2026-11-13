import { useId } from "react";
import { Link } from "react-router-dom";
import type { QuestionItem, Session } from "../types";
import { askLab, getLabQuestion, getLabQuestions, hasLabQuestions } from "../api";
import { LabFrame } from "../components/LabFrame";
import { MAX, clock, stateText, useQuestions } from "../components/useQuestions";
import research from "./Research.module.css";
import panel from "../components/AskPatient.module.css";
import styles from "./Ask.module.css";

const ERRORS = {
  401: "Your session has ended. Sign in again.",
  403: "Only a physician or a charge nurse can ask.",
  404: "This ward is not on this server.",
  422: "The question could not be sent. Use one line, up to 500 characters.",
  429: "Question limit reached: one open question at a time, ten a day. Try again later.",
};

// The stored record comes from another machine, so every read here tolerates a missing key or a value of any type.
const rec = (v: unknown): Record<string, unknown> => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const text = (v: unknown): string | undefined => (v == null ? undefined : typeof v === "string" ? v : JSON.stringify(v));

function Experiment({ report }: { report: unknown }) {
  const e = rec(rec(report).experiment);
  const [template, tool, args] = [text(e.template), text(e.tool), text(e.args)];
  return (
    <div>
      <h3 className={styles.sub}>The experiment that ran</h3>
      {template === undefined && tool === undefined && args === undefined ? (
        <p className={panel.meta}>The stored record names no experiment.</p>
      ) : (
        <>
          {template !== undefined && <p className={panel.meta}>Template: {template}</p>}
          {tool !== undefined && <p className={panel.meta}>Tool: {tool}</p>}
          {args !== undefined && (
            <>
              <p className={panel.meta}>Arguments:</p>
              <pre className={styles.pre}>{args}</pre>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Answer({ q, answer, report }: { q: QuestionItem; answer: string | null; report: unknown }) {
  return (
    <>
      {answer ? <pre className={styles.pre}>{answer}</pre> : <p className={panel.meta}>No answer text was stored.</p>}
      <Experiment report={report} />
      {rec(report).grounding === "ok" && <p className={panel.meta}>Every number in the answer was checked against this run&apos;s result.</p>}
      <details>
        <summary>The whole stored record</summary>
        <pre className={styles.pre} tabIndex={0} role="region" aria-label={`Stored record of question ${q.id}`}>{JSON.stringify(report, null, 2)}</pre>
      </details>
      <p className={panel.meta}>Synthetic, not validated.</p>
    </>
  );
}

function AskPanel({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const heading = useId();
  const canAsk = session.role === "md" || session.role === "charge_rn";
  const { items, text: typed, setText, sending, error, details, shown, mine, question, send, toggle, refresh } = useQuestions({
    list: getLabQuestions, ask: askLab, read: getLabQuestion, clinicianId: session.clinician_id, onSignOut, errors: ERRORS,
  });

  return (
    <section aria-labelledby={heading}>
      <h2 id={heading} className={research.heading}>Questions</h2>
      {canAsk ? (
        <form className={panel.form} onSubmit={send}>
          <label className={panel.field}>
            Your question
            <input type="text" maxLength={MAX} value={typed} disabled={mine} onChange={(e) => setText(e.target.value)} />
            <span className={panel.count} aria-hidden="true">{typed.length}/{MAX}</span>
          </label>
          <button type="submit" className={panel.button} disabled={!question || sending || mine}>Ask</button>
        </form>
      ) : (
        <p className={panel.quiet}>Only a physician or a charge nurse can ask. You can read the questions and answers.</p>
      )}
      {mine && (
        <p className={panel.quiet}>Wait for the answer to your last question. A question that is not taken within one hour fails by itself.</p>
      )}
      {error && <p role="alert" className={panel.error}>{error}</p>}
      <button type="button" className={panel.button} onClick={refresh}>Refresh</button>
      {items.length === 0 ? (
        <p className={panel.quiet}>No question yet.</p>
      ) : (
        <ul className={panel.list}>
          {items.map((q) => {
            const region = `${heading}-answer-${q.id}`;
            const open = shown.includes(q.id);
            const d = details[q.id];
            return (
              <li key={q.id} className={panel.item}>
                <p>{q.question}</p>
                {/* Only the state line is announced: an opened answer is long, and the reader chose to open it. */}
                <p className={panel.meta} aria-live="polite">
                  {q.requested_by !== session.clinician_id ? `asked by ${q.requested_by} · ` : ""}
                  {clock(q.requested_at)} &middot; {stateText(q, "Refused by the research agent")}
                </p>
                {q.state === "done" && (
                  <>
                    <button type="button" className={panel.button} aria-expanded={open} aria-controls={region} onClick={() => toggle(q.id)}>
                      {open ? "Hide answer" : "Show answer"}
                    </button>
                    <div id={region} hidden={!open}>
                      {open && d && <Answer q={q} answer={d.answer} report={d.report} />}
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

/** Ask the research agent a question that is not about a patient. Nothing here writes to the live ward. */
export function Ask({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  return (
    <LabFrame session={session} onSignOut={onSignOut}>
      <main className={research.main}>
        <p><Link to="/lab">Back to the Simulation lab</Link></p>
        <h1 className={research.title}>Ask the research agent</h1>
        <p role="note">
          The agent answers from simulated trials only. It runs one of its own fixed experiments and checks every number in its answer against that run. Synthetic, not validated.
        </p>
        <p className={styles.warning}>
          <strong>Warning. </strong>Do not type anything about a patient: no name, no bed, no id. Every clinician of this ward can read each question, and it is kept.
        </p>
        <p>For example: What if we alerted at a NEWS2 total of 7 or more? It refuses a question that fits none of its experiments.</p>
        {!hasLabQuestions ? (
          <p>Questions need the server.</p>
        ) : session.role === "admin" ? (
          <p>Questions are for the ward&apos;s clinicians.</p>
        ) : (
          <AskPanel session={session} onSignOut={onSignOut} />
        )}
      </main>
    </LabFrame>
  );
}
