import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError, clearSession, signIn, usesServer } from "../api";
import type { Session, SessionRole } from "../types";
import ch from "../components/Crosshair.module.css";
import styles from "./SignIn.module.css";

// --- Types ---

interface Props {
  onSignIn: (session: Session) => void;
}

type Department = "nursing" | "medical" | "admin";
type Phase = "department" | "profile" | "auth" | "destination";

interface Profile {
  id: string;
  label: string;
  role: string;
  department: Department;
  sessionRole: SessionRole;
  patients: number;
  ward: string;
}

// --- Demo profiles ---

const PROFILES: Profile[] = [
  {
    id: "rn-delacruz",
    label: "Nurse de la Cruz",
    role: "Staff Nurse",
    department: "nursing",
    sessionRole: "rn",
    patients: 6,
    ward: "W4",
  },
  {
    id: "rn-reyes",
    label: "Nurse Reyes",
    role: "Staff Nurse",
    department: "nursing",
    sessionRole: "rn",
    patients: 6,
    ward: "W4",
  },
  {
    id: "rn-manalo",
    label: "Nurse Manalo",
    role: "Charge Nurse",
    department: "nursing",
    sessionRole: "charge_rn",
    patients: 12,
    ward: "W4",
  },
  {
    id: "md-santos",
    label: "Dr. Santos",
    role: "Attending Physician",
    department: "medical",
    sessionRole: "md",
    patients: 5,
    ward: "W4",
  },
  {
    id: "md-aquino",
    label: "Dr. Aquino",
    role: "Attending Physician",
    department: "medical",
    sessionRole: "md",
    patients: 5,
    ward: "W4",
  },
];

// --- Messages ---

// The API answers every failed sign-in the same way (401), so the page cannot say which part was
// wrong; 429 says the clinician is locked for a while.
function refusal(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "Sign-in failed. Check the passphrase, or ask whoever set up your account.";
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Too many failed attempts for this clinician. Wait 15 minutes and try again.";
  }
  return "Cannot reach the ward server. Try again in a moment.";
}

// --- Component ---

export function SignIn({ onSignIn }: Props) {
  const navigate = useNavigate();
  // Set when a signed-out link (e.g. a patient page) sent the clinician here: return them to it.
  const from: unknown = (useLocation().state as { from?: unknown } | null)?.from;
  const [department, setDepartment] = useState<Department | null>(null);
  const [selected, setSelected] = useState<Profile | null>(null);
  const [adminId, setAdminId] = useState("");
  // Only the public demo (no server) prefills one: nothing is checked there.
  const [passphrase, setPassphrase] = useState(usesServer ? "" : "anything");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<Phase>("department");

  const signInAndGo = (profile: Profile, path: string) => {
    onSignIn({
      clinician_id: profile.id,
      ward: profile.ward,
      role: profile.sessionRole,
    });
    navigate(typeof from === "string" && from.startsWith("/") && from !== "/" ? from : path);
  };

  const handlePickDepartment = (dept: Department) => {
    setDepartment(dept);
    setSelected(null);
    setPhase(dept === "admin" ? "auth" : "profile");
  };

  const handleSelectProfile = (p: Profile) => {
    setSelected(p);
    setPhase("auth");
  };

  const handleAuth = async (e: FormEvent) => {
    e.preventDefault();
    if ((!selected && department !== "admin") || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (department === "admin") {
        // The role comes only from the server's answer: no card or typed id can make an admin.
        const role = await signIn(adminId, passphrase);
        if (role !== "admin") {
          clearSession();
          setError("This account is not an administrator. Sign in from Nursing or Medical.");
          return;
        }
        onSignIn({ clinician_id: adminId, ward: "W4", role });
        navigate("/overview");
        return;
      }
      const role = await signIn(selected!.id, passphrase);
      // The server's role wins over the card's.
      if (role) setSelected({ ...selected!, sessionRole: role });
      setPhase("destination");
    } catch (failure) {
      setError(refusal(failure));
    } finally {
      if (usesServer) setPassphrase(""); // not kept in the page once it has been sent
      setBusy(false);
    }
  };

  const handleBack = () => {
    if (phase === "profile") {
      setDepartment(null);
      setSelected(null);
      setPhase("department");
    } else if (phase === "auth") {
      setSelected(null);
      setError(null);
      if (department === "admin") setDepartment(null);
      setPhase(department === "admin" ? "department" : "profile");
    } else if (phase === "destination") {
      setPhase("auth");
    }
  };

  const filteredProfiles = department
    ? PROFILES.filter((p) => p.department === department)
    : [];

  return (
    <main className={styles.page}>
      <div className={styles.hero}>
        <p className={styles.eyebrow}>BioSentry</p>
        <h1 className={styles.heading}>Sign in</h1>
      </div>

      <div className={styles.content}>
        {phase !== "department" && (
          <button
            type="button"
            className={styles.backLink}
            onClick={handleBack}
          >
            &larr; Back
          </button>
        )}

        {/* Phase 1: Department */}
        {phase === "department" && (
          <>
            <p className={styles.sectionLabel}>I am a</p>
            <div className={styles.cards}>
              <button
                type="button"
                className={`${ch.box} ${styles.roleCard}`}
                onClick={() => handlePickDepartment("nursing")}
              >
                <div className={ch.inner} />
                <span className={styles.roleTitle}>Nurse</span>
                <span className={styles.roleDesc}>
                  Staff Nurse, Charge Nurse
                </span>
              </button>
              <button
                type="button"
                className={`${ch.box} ${styles.roleCard}`}
                onClick={() => handlePickDepartment("medical")}
              >
                <div className={ch.inner} />
                <span className={styles.roleTitle}>Physician</span>
                <span className={styles.roleDesc}>
                  Attending Physician, Consultant
                </span>
              </button>
              {usesServer && (
                <button
                  type="button"
                  className={`${ch.box} ${styles.roleCard}`}
                  onClick={() => handlePickDepartment("admin")}
                >
                  <div className={ch.inner} />
                  <span className={styles.roleTitle}>Administration</span>
                  <span className={styles.roleDesc}>Ward overview, read only</span>
                </button>
              )}
            </div>
          </>
        )}

        {/* Phase 2: Profile */}
        {phase === "profile" && (
          <>
            <p className={styles.sectionLabel}>
              {department === "nursing" ? "Select nurse" : "Select physician"}
            </p>
            <div className={styles.cards}>
              {filteredProfiles.map((p) => (
                <button
                  key={p.id}
                  className={`${ch.box} ${styles.profileCard}`}
                  onClick={() => handleSelectProfile(p)}
                  type="button"
                >
                  <div className={ch.inner} />
                  <div className={styles.profileHeader}>
                    <span className={styles.profileInitial}>
                      {p.label.split(" ").pop()?.charAt(0) ?? p.label.charAt(0)}
                    </span>
                    <div>
                      <div className={styles.profileName}>{p.label}</div>
                      <div className={styles.profileRole}>{p.role}</div>
                    </div>
                  </div>
                  <div className={styles.profileMeta}>
                    <span>{p.patients} patients</span>
                    <span>Ward {p.ward}</span>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        {/* Phase 3: Auth */}
        {phase === "auth" && (selected || department === "admin") && (
          <form onSubmit={handleAuth} className={styles.form}>
            {selected ? (
              <div className={styles.selectedInfo}>
                <span className={styles.selectedName}>{selected.label}</span>
                <span className={styles.selectedDetail}>
                  {selected.role} &middot; Ward {selected.ward} &middot;{" "}
                  {selected.patients} patients
                </span>
              </div>
            ) : (
              <div className={styles.field}>
                <label className={styles.label} htmlFor="admin-id">
                  Administrator id
                </label>
                <div className={ch.box}>
                  <div className={ch.inner} />
                  <input
                    className={styles.input}
                    id="admin-id"
                    value={adminId}
                    onChange={(e) => setAdminId(e.target.value)}
                    autoComplete="off"
                    maxLength={64}
                    required
                  />
                </div>
              </div>
            )}

            <div className={styles.field}>
              <label className={styles.label} htmlFor="passphrase">
                Passphrase
              </label>
              <div className={ch.box}>
                <div className={ch.inner} />
                <input
                  className={styles.input}
                  id="passphrase"
                  type="password"
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  autoComplete="off"
                  maxLength={256}
                  required={usesServer}
                />
              </div>
            </div>

            {error && (
              <p className={styles.error} role="alert">
                {error}
              </p>
            )}

            <button type="submit" className={styles.btn} disabled={busy}>
              {busy ? "Signing in" : "Continue"}
            </button>

            <p className={styles.authNote}>
              {usesServer
                ? "The ward server checks your passphrase. After 5 failed attempts for a clinician, sign-in is locked for 15 minutes. Every attempt is recorded."
                : "Public demo: there is no server and no real sign-in. Any passphrase is accepted, and nothing you enter is kept after a reload."}
            </p>
          </form>
        )}

        {/* Phase 4: Destination */}
        {phase === "destination" && selected && (
          <>
            <div className={styles.selectedInfo}>
              <span className={styles.selectedName}>{selected.label}</span>
              <span className={styles.selectedDetail}>
                {selected.role} &middot; Ward {selected.ward}
              </span>
            </div>

            <p className={styles.sectionLabel}>Choose destination</p>
            <div className={styles.cards}>
              <button
                type="button"
                className={`${ch.box} ${styles.destCard}`}
                onClick={() => signInAndGo(selected, "/ward")}
              >
                <div className={ch.inner} />
                <span className={styles.destTitle}>Ward Monitor</span>
                <span className={styles.destDesc}>
                  View your assigned patients, ranked by deterioration risk.
                </span>
              </button>
              {(selected.sessionRole === "md" || selected.sessionRole === "charge_rn") && (
                <button
                  type="button"
                  className={`${ch.box} ${styles.destCard}`}
                  onClick={() => signInAndGo(selected, "/station")}
                >
                  <div className={ch.inner} />
                  <span className={styles.destTitle}>Station</span>
                  <span className={styles.destDesc}>
                    Admit patients, enter lab results, upload documents.
                  </span>
                </button>
              )}
            </div>
          </>
        )}
      </div>

      <footer className={styles.footer}>
        <div className={styles.footerGrid}>
          <div className={styles.footerItem}>
            <span className={styles.footerLabel}>Privacy</span>
            <span>
              {usesServer
                ? "Pilot build: synthetic data only. Not for real patients."
                : "Public demo: synthetic data, no server, no real sign-in"}
            </span>
          </div>
        </div>
      </footer>
    </main>
  );
}
