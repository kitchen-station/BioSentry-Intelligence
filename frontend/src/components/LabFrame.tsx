import type { ReactNode } from "react";
import type { Session } from "../types";
import { SessionMenu } from "./SessionMenu";
import styles from "./LabFrame.module.css";

/** The frame every lab page sits in. It differs from the ward by its bar and its words. */
export function LabFrame({ session, onSignOut, children }: { session: Session; onSignOut: () => void; children: ReactNode }) {
  return (
    <div className={styles.page}>
      <div className={styles.bar}>
        <span>BioSentry &middot; Simulation lab</span>
        <SessionMenu session={session} onSignOut={onSignOut} />
      </div>
      <p role="note" className={styles.label}>
        Simulation lab: not the ward. Nothing here changes a patient&apos;s rank.
      </p>
      {children}
    </div>
  );
}

/** One line under the top bar of the ward pages. True on fixture, seeded and live runs. */
export function SimulatedNote() {
  return (
    <p role="note" className={styles.quiet}>
      Simulated patients until a hospital is connected.
    </p>
  );
}
