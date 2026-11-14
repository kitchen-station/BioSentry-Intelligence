import { Link } from "react-router-dom";
import type { Session } from "../types";
import { hasLabQuestions } from "../api";
import { LabFrame } from "../components/LabFrame";
import { LabRuns } from "./LabRuns";
import research from "./Research.module.css";

/** The lab's front page. A physician or charge nurse reaches it as "What-if runs" (/what-if); an administrator as the "Simulation lab" (/lab). It writes nothing to the live ward; its two writes are a request for a simulated run and a question for the research agent. */
export function Lab({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  // shortcut: only the one-address image (VITE_API_URL=/api) serves /simulator/; upgrade to an explicit VITE_SIMULATOR_PATH when deploy/ changes.
  const admin = session.role === "admin";
  const simulatorServed = admin && import.meta.env.VITE_API_URL === "/api"; // a developer tool: no word of it on the clinician's page
  return (
    <LabFrame session={session} onSignOut={onSignOut} whatIf={!admin}>
      <main className={research.main}>
        <h1 className={research.title}>{admin ? "Simulation lab" : "What-if runs"}</h1>
        <ul>
          {hasLabQuestions && !admin && <li><Link to="/ask">Ask the research agent</Link></li>}
          {admin && (
            <>
              <li><Link to="/lab/research">Research numbers</Link></li>
              <li><Link to="/lab/research#agent">The research agent&apos;s questions and answers</Link></li>
              <li><Link to="/lab/pipeline">The pipeline</Link></li>
            </>
          )}
        </ul>
        <LabRuns role={session.role} onSignOut={onSignOut} />
        {simulatorServed && (
          <section aria-labelledby="outside">
            <h2 id="outside" className={research.heading}>Outside the lab</h2>
            <p>
              The ward simulator runs the live ward: a change made there changes the ranks on the ward list. It opens in a new tab and asks for its own sign-in.
            </p>
            <a href="/simulator/" target="_blank" rel="noopener">Open the ward simulator</a>
          </section>
        )}
      </main>
    </LabFrame>
  );
}
