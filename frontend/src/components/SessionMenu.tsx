import { useId } from "react";
import { useNavigate } from "react-router-dom";
import type { Session } from "../types";
import { usePopover } from "./usePopover";
import { useWardFeed } from "./wardFeed";
import styles from "./SessionMenu.module.css";

interface Props {
  session: Session;
  onSignOut: (explicit?: boolean) => void;
}

export const ROLE_LABELS: Record<string, string> = {
  rn: "Staff Nurse",
  md: "Attending Physician",
  charge_rn: "Charge Nurse",
  admin: "Administrator",
};

// An icon is a span, never <i>: Ward research forbids <b>, <i>, <u> and <s> anywhere on its page.
const Icon = ({ name }: { name: string }) => <span className={`hw-icon fa-solid fa-${name}`} aria-hidden="true" />;

// A save that never answers must not keep a person signed in.
const SAVE_WAIT_MS = 3_000;

/** Who is signed in, and Sign out. A disclosure, not a menu: it holds text as well as the one action. */
export function AccountMenu({ session, onSignOut }: Props) {
  const navigate = useNavigate();
  const { open, setOpen, anchor, trigger } = usePopover();
  const panelId = useId();
  const admin = session.role === "admin";
  const feed = useWardFeed();

  const signOut = () => {
    setOpen(false);
    // An acknowledgement still in its Undo window is saved first, while the session can still sign it.
    const saved = feed ? Promise.race([feed.flush(), new Promise((r) => setTimeout(r, SAVE_WAIT_MS))]) : Promise.resolve();
    void saved.then(() => {
      onSignOut(true);
      navigate("/");
    });
  };

  return (
    <div ref={anchor} className={`hw-menu-anchor ${styles.account}`}>
      <button
        ref={trigger}
        type="button"
        className={styles.trigger}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.avatar} aria-hidden="true"><Icon name="user" /></span>
        <span className={styles.who}>{session.clinician_id}</span>
        <Icon name="chevron-down" />
      </button>

      {open && (
        <div id={panelId} className={`hw-menu ${styles.panel}`} data-align="end">
          <div className={styles.info}>
            <span className={styles.role}>{ROLE_LABELS[session.role] ?? session.role}</span>
            <span className={styles.ward}>Ward {session.ward}</span>
          </div>

          {/* The same switch as the API build in api/index.ts; read here so the lab never reaches the api module. */}
          {import.meta.env.VITE_API_URL && !admin && (
            <p className={styles.notice}>
              A ward administrator can see which beds you are assigned, counts of your recorded actions from the last 24 hours, and every patient's page, including the notes you write.
            </p>
          )}

          <div className="hw-menu__separator" />

          <button type="button" className="hw-menu__item" onClick={signOut}>
            <Icon name="right-from-bracket" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
