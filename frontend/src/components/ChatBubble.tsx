import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { Session } from "../types";
import { hasLabQuestions, hasPatientQuestions } from "../api";
import { Thread } from "./Thread";
import { OPEN_CHAT, asksWard } from "./roles";
import { useAskScope } from "./useAskScope";
import styles from "./ChatBubble.module.css";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary";

/**
 * The chat, one click away on every signed-in page: a button at the bottom right that opens the shared thread in a panel. On a
 * patient's page it is that patient's thread, elsewhere the ward's. It shows only where the reader may ask: a physician or a
 * charge nurse everywhere, a staff nurse on a patient's page, an administrator nowhere. The Ask pages are the chat already.
 */
export function ChatBubble({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const { pathname } = useLocation();
  const scope = useAskScope();
  const patientId = scope.kind === "patient" ? scope.id : undefined;
  // The panel is open on the page it was opened on: leaving that page closes it, and coming back does not reopen it.
  const [openAt, setOpenAt] = useState<string | null>(null);
  if (openAt !== null && openAt !== pathname) setOpenAt(null); // state follows the route, set while rendering, with no effect
  const title = useId();
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  const asks = asksWard(session.role);
  const connected = patientId ? hasPatientQuestions : hasLabQuestions; // the fixture build has neither: no bubble
  const shown = connected && scope.kind !== "none" && !pathname.startsWith("/ask") && (asks || (session.role === "rn" && patientId !== undefined));
  const open = shown && openAt === pathname;

  // The patient page's own button opens the same panel.
  useEffect(() => {
    if (!shown) return;
    const onOpen = () => setOpenAt(pathname);
    window.addEventListener(OPEN_CHAT, onOpen);
    return () => window.removeEventListener(OPEN_CHAT, onOpen);
  }, [shown, pathname]);

  // While it is open the panel holds the focus: Escape closes it and Tab goes round inside it. The listener is on the document, so
  // both still work when the focus has dropped to the page (a click on the panel's text, or a field that turned disabled).
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>("button")?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpenAt(null);
        trigger.current?.focus();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const stops = panel.current.querySelectorAll<HTMLElement>(FOCUSABLE);
      const [first, last] = [stops[0], stops[stops.length - 1]];
      const at = document.activeElement;
      if (!panel.current.contains(at) || at === (e.shiftKey ? first : last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!shown) return null;

  const close = () => {
    setOpenAt(null);
    trigger.current?.focus();
  };

  return (
    <>
      {open && (
        <>
          <div className={styles.backdrop} data-testid="chat-backdrop" onClick={close} />
          {/* The kit's drawer look on React's dialog: the focus, Escape and Tab handling above are its behaviour. */}
          <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={title} className={`hw-dialog hw-dialog--drawer ${styles.panel}`}>
            <div className="hw-dialog__header">
              <h2 id={title} className="hw-dialog__title">{patientId ? "Ask about this patient" : "Ask about the ward"}</h2>
              <button type="button" className="hw-close" aria-label="Close the chat" onClick={close}>
                <span className="hw-icon fa-solid fa-xmark" aria-hidden="true" />
              </button>
            </div>
            <div className={`hw-dialog__body ${styles.body}`}>
              {asks && (
                <p className="hw-caption">
                  <Link to={patientId ? `/ask/patient/${patientId}` : "/ask"}>Open as a page</Link>
                </p>
              )}
              <Thread
                key={patientId ?? "ward"}
                scope={scope.kind === "patient" ? scope : { kind: "ward" }}
                clinicianId={session.clinician_id}
                onSignOut={onSignOut}
              />
            </div>
          </div>
        </>
      )}
      <button
        ref={trigger}
        type="button"
        className={`hw-button hw-button--primary hw-button--lg ${styles.bubble}`}
        aria-expanded={open}
        onClick={() => (open ? close() : setOpenAt(pathname))}
      >
        <span className="hw-icon fa-solid fa-comments" aria-hidden="true" />
        Ask
      </button>
    </>
  );
}
