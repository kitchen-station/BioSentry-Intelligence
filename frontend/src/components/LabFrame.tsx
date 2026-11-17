import type { ReactNode } from "react";
import type { Session } from "../types";
import { AppShell } from "./AppShell";
import styles from "./LabFrame.module.css";

/** The frame every lab page sits in. It differs from the ward by its bar and its words. `whatIf` is the clinician's wording for the same frame. */
export function LabFrame({ session, onSignOut, whatIf = false, children }: { session: Session; onSignOut: () => void; whatIf?: boolean; children: ReactNode }) {
  return (
    <AppShell session={session} onSignOut={onSignOut} title={whatIf ? "What-if runs" : "Simulation lab"}>
      <p role="note" className={styles.label}>
        <span className="hw-icon fa-solid fa-flask" aria-hidden="true" />
        {whatIf ? "Simulated trials only." : "Simulation lab: not the ward."} Nothing here changes a patient&apos;s rank.
      </p>
      {children}
    </AppShell>
  );
}

/** One line under the top bar of the ward pages. True on fixture, seeded and live runs. */
export function SimulatedNote() {
  return (
    <p role="note" className={styles.quiet}>
      <span className="hw-icon fa-solid fa-circle-info" aria-hidden="true" />
      Simulated patients until a hospital is connected.
    </p>
  );
}
