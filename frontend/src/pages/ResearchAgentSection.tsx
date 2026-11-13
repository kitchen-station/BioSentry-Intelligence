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
    <section className={styles.card} aria-labelledby="agent">
      <h2 id="agent" className={styles.heading}>Research agent: questions asked on the simulated ward</h2>
      <p>
        The agent is a program run by hand from a terminal. This page cannot ask it anything yet: it only
        shows what was recorded. The agent matches a question to one of three fixed experiments, runs it on
        simulated trials, and answers in fixed sentences filled with the result&apos;s numbers, or refuses and
        runs nothing. Every step goes to a ledger. A change it proposes is applied only after a sign-off is
        recorded.
      </p>
      <p>
        Synthetic, not validated. These answers use tuning seeds 1 to 10, not the held-out seeds 11 to 30 in
        the tables above, so the counts differ. The baseline&apos;s k and p were chosen on these same seeds, so
        its numbers here flatter it. The rules are judged on the simulator that made the data.
      </p>
      <p className={styles.small}>
        Source: {P.source} {P.records} ledger records, {P.exportedBy}, {P.date}.
      </p>
      {questions.length === 0 ? (
        <p>No questions are recorded in this export.</p>
      ) : (
        <ol>
          {questions.map((q) => (
            <li key={q.id}>
              <p>&ldquo;{q.question}&rdquo;</p>
              <p>{q.askedAt ? `Asked ${q.askedAt}` : "Time not recorded in the transcript"}</p>
              <p>{q.status === "answered" ? "Answered" : "Refused, nothing was run"}</p>
              {q.answer.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </li>
          ))}
        </ol>
      )}
      <h3 className={styles.heading}>Proposals</h3>
      {proposals.length === 0 ? (
        <p>No proposals are recorded in this export.</p>
      ) : (
        <ul>
          {proposals.map((p) => (
            <li key={p.id}>
              <p>Proposal {p.id}</p>
              <p>{signOff(p)}</p>
              <p>{p.conclusion}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
