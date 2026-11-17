import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { Session } from "../types";
import { AppShell, PublicShell, researchItems } from "./AppShell";
import styles from "./LabFrame.module.css";

/**
 * The research space: every page behind the Research (or Lab) menu sits in it. An info-tinted strip says nothing here
 * changes a rank, and links the pages this role can open (the menu's own list); graphs inside highlight in info blue
 * (data-hw-space="research"). With no session (the sample build's evidence, read before sign-in) it has no account
 * and no page links, only the way back to sign-in.
 */
export function LabFrame({ session, onSignOut, title, status, children }: {
  session: Session | null;
  onSignOut: () => void;
  /** What this page is: the phone's app bar shows it. */
  title: ReactNode;
  /** Live facts for the bar, as AppShell takes them. */
  status?: ReactNode;
  children: ReactNode;
}) {
  const items = session ? researchItems(session.role) : [];
  const space = (
    <div data-hw-space="research">
      <div className={styles.strip}>
        <p role="note" className={styles.label}>
          <span className="hw-icon fa-solid fa-flask" aria-hidden="true" />
          <span><strong>Research</strong> &middot; Synthetic, not validated. Nothing here changes a patient&apos;s rank.</span>
        </p>
        {items.length > 0 && (
          <nav aria-label={session?.role === "admin" ? "Lab pages" : "Research pages"} className={styles.pages}>
            {items.map((it) => (
              // /lab is the lab's front page: its own pages below it are not it.
              <NavLink key={it.to} to={it.to} end={it.to === "/lab"} className={`hw-button hw-button--sm hw-button--ghost ${styles.page}`}>
                {it.title}
              </NavLink>
            ))}
          </nav>
        )}
      </div>
      {children}
    </div>
  );
  return session ? <AppShell session={session} onSignOut={onSignOut} title={title} status={status}>{space}</AppShell> : <PublicShell>{space}</PublicShell>;
}

/** One line under the top bar of the ward pages. True on fixture, seeded and live runs. An administrator's adds read-only. */
export function SimulatedNote({ readOnly = false }: { readOnly?: boolean }) {
  return (
    <p role="note" className={styles.quiet}>
      <span className="hw-icon fa-solid fa-circle-info" aria-hidden="true" />
      Simulated patients until a hospital is connected.
      {readOnly && <strong className={styles.readOnly}>Read-only. Every view is recorded in the audit log.</strong>}
    </p>
  );
}
