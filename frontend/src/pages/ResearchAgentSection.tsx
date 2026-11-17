import { RESEARCH_AGENT, type AgentExport, type AgentProposal } from "../data/researchAgent";
import styles from "./Research.module.css";

const signOff = ({ state, approvedBy, approvedAt }: AgentProposal) => {
  if (state === "proposed") return "Waiting for a person to sign off. Nothing applied.";
  const by = `Signed off by ${approvedBy}${approvedAt ? ` on ${approvedAt}` : ""}`;
  return state === "applied" ? `${by}, then applied to the models folder that run was given.` : `${by}. Not applied yet.`;
};

/** What the research agent recorded. Read-only: reads a committed export, makes no API call. */
export function ResearchAgentSection({ data = RESEARCH_AGENT }: { data?: AgentExport }) {
  const { provenance: P, questions, proposals } = data;
  return (
    <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="agent">
      <h2 id="agent" className="hw-heading hw-heading--4">Research agent: questions asked on the simulated ward</h2>
      <p>
        Answers recorded from a program run by hand. This page cannot ask it anything yet.
      </p>
      <p>
        Synthetic, not validated. These answers use tuning seeds 1 to 10, not the held-out seeds 11 to 30 in
        the tables above, so the counts differ. The baseline&apos;s k and p were chosen on these same seeds, so
        its numbers here flatter it.
      </p>
      <p className="hw-caption">
        Source: {P.source} {P.records} ledger records, {P.exportedBy}, {P.date}.
      </p>
      {questions.length === 0 ? (
        <p>No questions are recorded in this export.</p>
      ) : (
        <ol className={styles.entries}>
          {questions.map((q) => (
            <li key={q.id}>
              <p className={styles.question}>&ldquo;{q.question}&rdquo;</p>
              <p className="hw-caption">{q.askedAt ? `Asked ${q.askedAt}` : "Time not recorded"}</p>
              <p><span className={q.status === "answered" ? "hw-tag hw-tag--success" : "hw-tag"}>{q.status === "answered" ? "Answered" : "Refused, nothing was run"}</span></p>
              {q.answer.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </li>
          ))}
        </ol>
      )}
      <h3 className="hw-heading hw-heading--4">Proposals</h3>
      {proposals.length === 0 ? (
        <p>No proposals are recorded in this export.</p>
      ) : (
        <ul className={styles.entries}>
          {proposals.map((p) => (
            <li key={p.id}>
              <p className={styles.question}>Proposal {p.id}</p>
              <p>{signOff(p)}</p>
              <p>{p.conclusion}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
