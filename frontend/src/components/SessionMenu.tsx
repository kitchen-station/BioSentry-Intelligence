import { useState } from "react";
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

export function SessionMenu({ session, onSignOut }: Props) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const admin = session.role === "admin";
  const station = session.role === "md" || session.role === "charge_rn";
  const analytics: unknown = import.meta.env.VITE_ANALYTICS_URL;

  return (
    <div className={styles.wrapper}>
      <nav aria-label="Pages" className={styles.nav}>
        {admin && <NavLink to="/overview" className={styles.place}>Overview</NavLink>}
        <NavLink to="/ward" end className={styles.place}>Ward</NavLink>
        {station && <NavLink to="/ask" className={styles.place}>Ask</NavLink>}
        {station && <NavLink to="/what-if" className={styles.place}>What-if runs</NavLink>}
        {station && <NavLink to="/station" className={styles.place}>Station</NavLink>}
        {/* The same switch as the API build (hasWardResearch in api/index.ts), read here as the notice below reads it. */}
        {station && import.meta.env.VITE_API_URL && <NavLink to="/ward/research" className={styles.place}>Ward research</NavLink>}
        {typeof analytics === "string" && ANALYTICS_PATH.test(analytics) && (
          <a href={analytics} target="_blank" rel="noopener noreferrer" className={styles.place}>Analytics</a>
        )}
      </nav>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setOpen((v) => !v)}
      >
        {session.clinician_id}
        <span className={styles.caret}>{open ? "\u25B4" : "\u25BE"}</span>
      </button>

      {open && (
        <>
          <div className={styles.backdrop} onClick={() => setOpen(false)} />
          <div className={styles.dropdown}>
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

            <div className={styles.divider} />

            <button
              type="button"
              className={styles.menuItem}
              onClick={() => {
                setOpen(false);
                onSignOut(true);
                navigate("/");
              }}
            >
              Sign out
            </button>
          </div>
        </>
      )}
    </div>
  );
}
