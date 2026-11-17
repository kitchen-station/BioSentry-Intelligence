import { Link } from "react-router-dom";
import type { Session } from "../types";
import { hasLabQuestions } from "../api";
import { LabFrame } from "../components/LabFrame";
import { LabRuns } from "./LabRuns";
import research from "./Research.module.css";

const Place = ({ to, icon, children }: { to: string; icon: string; children: string }) => (
  <li>
    <Link to={to} className="hw-tile">
      <span className={`hw-icon fa-solid fa-${icon}`} aria-hidden="true" />
      {children}
    </Link>
  </li>
);

/** The lab's front page. A physician or charge nurse reaches it as "What-if runs" (/what-if); an administrator as the "Simulation lab" (/lab). It writes nothing to the live ward; its two writes are a request for a simulated run and a question for the research agent. */
export function Lab({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  // shortcut: only the one-address image (VITE_API_URL=/api) serves /simulator/; upgrade to an explicit VITE_SIMULATOR_PATH when deploy/ changes.
  const admin = session.role === "admin";
  const simulatorServed = admin && import.meta.env.VITE_API_URL === "/api"; // a developer tool: no word of it on the clinician's page
  return (
    <LabFrame session={session} onSignOut={onSignOut} whatIf={!admin}>
      <div className={research.main}>
        <h1 className="hw-heading hw-heading--3">{admin ? "Simulation lab" : "What-if runs"}</h1>
        <ul className={research.places}>
          {hasLabQuestions && !admin && <Place to="/ask" icon="comments">Ask the research agent</Place>}
          {admin && (
            <>
              <Place to="/lab/research" icon="chart-line">Research numbers</Place>
              <Place to="/lab/research#agent" icon="comments">The research agent&apos;s questions and answers</Place>
              <Place to="/lab/pipeline" icon="diagram-project">The pipeline</Place>
            </>
          )}
        </ul>
        <LabRuns role={session.role} onSignOut={onSignOut} />
        {simulatorServed && (
          <section aria-labelledby="outside" className={`hw-card hw-card--outlined ${research.card}`}>
            <h2 id="outside" className="hw-heading hw-heading--4">Outside the lab</h2>
            <p>
              The ward simulator runs the live ward: a change made there changes the ranks on the ward list. It opens in a new tab and asks for its own sign-in.
            </p>
            <p><a href="/simulator/" target="_blank" rel="noopener">Open the ward simulator</a></p>
          </section>
        )}
      </div>
    </LabFrame>
  );
}
