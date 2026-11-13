import { Link } from "react-router-dom";
import type { Session } from "../types";
import { LabFrame } from "../components/LabFrame";
import research from "./Research.module.css";

/** The lab's front page. It reads nothing from the ward and writes nothing. */
export function Lab({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  // shortcut: only the one-address image (VITE_API_URL=/api) serves /simulator/; upgrade to an explicit VITE_SIMULATOR_PATH when deploy/ changes.
  const simulatorServed = import.meta.env.VITE_API_URL === "/api";
  return (
    <LabFrame session={session} onSignOut={onSignOut}>
      <main className={research.main}>
        <h1 className={research.title}>Simulation lab</h1>
        <ul>
          <li><Link to="/lab/research">Research numbers</Link></li>
          <li><Link to="/lab/research#agent">The research agent&apos;s questions and answers</Link></li>
          <li><Link to="/lab/pipeline">The pipeline</Link></li>
        </ul>
        {simulatorServed && (
          <section aria-labelledby="outside">
            <h2 id="outside" className={research.heading}>Outside the lab</h2>
            <p>
              The ward simulator draws the ward that is running now. A physician or charge nurse signed in there
              can change that ward, and so the ranks on the ward list. It opens in a new tab and asks for its own sign-in.
            </p>
            <a href="/simulator/" target="_blank" rel="noopener">Open the ward simulator</a>
          </section>
        )}
      </main>
    </LabFrame>
  );
}
