import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { closeDialog } from "hw-ui";
import type { Session } from "../types";
import { PageLinks, SessionMenu } from "./SessionMenu";
import styles from "./AppShell.module.css";

interface Props {
  session: Session;
  onSignOut: (explicit?: boolean) => void;
  /** What the bar says this page is (e.g. "Ward W4"). Plain text: the page keeps its own h1. */
  title?: ReactNode;
  /** Live facts beside the title: ward clock, last read, signal. */
  status?: ReactNode;
  children: ReactNode;
}

export function Mark() {
  return (
    <span className={styles.mark}>
      <span className={styles.markIcon} aria-hidden="true"><span className="fa-solid fa-heart-pulse" /></span>
      BioSentry
    </span>
  );
}

/**
 * The frame of every signed-in page. Desk: brand and page links on the left, a sticky bar with the
 * page's title, status and the account. Phone (< 992px): the bar gains a menu button that opens the
 * page links in a drawer (a native <dialog>, so focus is held and Esc closes it).
 */
export function AppShell({ session, onSignOut, title, status, children }: Props) {
  const [drawer, setDrawer] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    if (drawer && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [drawer]);

  // A link in the drawer changes the page: the drawer has done its job.
  useEffect(() => {
    void closeDialog(dialog.current);
  }, [pathname]);

  return (
    <div className={styles.shell}>
      <div className={styles.brand}><Mark /></div>
      <button
        type="button"
        className={`hw-appbar__action ${styles.menu}`}
        aria-label="Open menu"
        aria-expanded={drawer}
        onClick={() => setDrawer(true)}
      >
        <span className="hw-icon fa-solid fa-bars" aria-hidden="true" />
      </button>
      <header className={styles.bar}>
        {title !== undefined && <span className={styles.title}>{title}</span>}
        {status}
      </header>
      <SessionMenu session={session} onSignOut={onSignOut} />
      <main className={styles.main}>{children}</main>
      <p className={styles.credit}>Set in HarmonyOS Sans</p>

      {drawer && (
        <dialog
          ref={dialog}
          className="hw-dialog hw-dialog--nav"
          aria-label="Main menu"
          onClose={() => setDrawer(false)}
          onCancel={(e) => {
            e.preventDefault(); // Esc: animate out, then close
            void closeDialog(e.currentTarget);
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) void closeDialog(e.currentTarget); // the backdrop
          }}
        >
          <div className="hw-dialog__header">
            <Mark />
            <button type="button" className="hw-close" aria-label="Close menu" onClick={(e) => void closeDialog(e.currentTarget.closest("dialog"))}>
              <span className="hw-icon fa-solid fa-xmark" aria-hidden="true" />
            </button>
          </div>
          <PageLinks session={session} />
        </dialog>
      )}
    </div>
  );
}
