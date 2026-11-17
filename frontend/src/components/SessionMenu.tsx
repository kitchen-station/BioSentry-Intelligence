import { useEffect, useId, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import type { Session } from "../types";
import styles from "./SessionMenu.module.css";

interface Props {
  session: Session;
  onSignOut: (explicit?: boolean) => void;
}

const ANALYTICS_PATH = /^\/(?![/\\])[^\s\\]*$/; // same-origin path only: "//x" and "/\x" leave the site

export const ROLE_LABELS: Record<string, string> = {
  rn: "Staff Nurse",
  md: "Attending Physician",
  charge_rn: "Charge Nurse",
  admin: "Administrator",
};

// An icon is a span, never <i>: Ward research forbids <b>, <i>, <u> and <s> anywhere on its page.
const Icon = ({ name }: { name: string }) => <span className={`hw-icon fa-solid fa-${name}`} aria-hidden="true" />;

function Place({ to, icon, label, end }: { to: string; icon: string; label: string; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className="hw-sidenav__link">
      <Icon name={icon} />
      <span className="hw-sidenav__label">{label}</span>
    </NavLink>
  );
}

/** The pages this role can use. The side bar on a desk; the menu drawer on a phone. */
export function PageLinks({ session, className }: { session: Session; className?: string }) {
  const admin = session.role === "admin";
  const station = session.role === "md" || session.role === "charge_rn";
  const analytics: unknown = import.meta.env.VITE_ANALYTICS_URL;
  const live = Boolean(import.meta.env.VITE_API_URL); // the API build switch (api/index.ts); the fixture build has no Ask or What-if

  return (
    <nav aria-label="Pages" className={`hw-sidenav ${className ?? ""}`}>
      {admin && <Place to="/overview" icon="gauge-high" label="Overview" />}
      <Place to="/ward" icon="list-ol" label="Ward" end />
      {station && live && <Place to="/ask" icon="comments" label="Ask" />}
      {station && live && <Place to="/what-if" icon="flask" label="What-if runs" />}
      {station && <Place to="/station" icon="hospital-user" label="Station" />}
      {/* The same switch as the API build (hasWardResearch in api/index.ts), read here as the notice below reads it. */}
      {station && live && <Place to="/ward/research" icon="table" label="Ward research" />}
      {typeof analytics === "string" && ANALYTICS_PATH.test(analytics) && (
        <a href={analytics} target="_blank" rel="noopener noreferrer" className="hw-sidenav__link">
          <Icon name="chart-line" />
          <span className="hw-sidenav__label">Analytics</span>
        </a>
      )}
    </nav>
  );
}

/** Who is signed in, and Sign out. A disclosure, not a menu: it holds text as well as the one action. */
export function AccountMenu({ session, onSignOut }: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const admin = session.role === "admin";

  // Esc and a press anywhere outside close it; Esc gives focus back to the trigger.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    const onPress = (e: PointerEvent) => {
      if (!anchor.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPress);
    };
  }, [open]);

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
        {session.clinician_id}
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

          <button
            type="button"
            className="hw-menu__item"
            onClick={() => {
              setOpen(false);
              onSignOut(true);
              navigate("/");
            }}
          >
            <Icon name="right-from-bracket" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/** The page links and the account, as siblings: the app shell's grid places each. */
export function SessionMenu({ session, onSignOut }: Props) {
  return (
    <>
      <PageLinks session={session} className={styles.nav} />
      <AccountMenu session={session} onSignOut={onSignOut} />
    </>
  );
}
