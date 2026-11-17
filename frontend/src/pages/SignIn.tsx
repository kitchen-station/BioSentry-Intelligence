import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError, clearSession, signIn, usesServer } from "../api";
import type { Session, SessionRole } from "../types";
import { ROLE_LABELS } from "../components/SessionMenu";
import { Mark } from "../components/AppShell";
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
    ward: "W4",
  },
  {
    id: "rn-reyes",
    label: "Nurse Reyes",
    role: "Staff Nurse",
    department: "nursing",
    sessionRole: "rn",
    ward: "W4",
  },
  {
    id: "rn-manalo",
    label: "Nurse Manalo",
    role: "Charge Nurse",
    department: "nursing",
    sessionRole: "charge_rn",
    ward: "W4",
  },
  {
    id: "md-santos",
    label: "Dr. Santos",
    role: "Attending Physician",
    department: "medical",
    sessionRole: "md",
    ward: "W4",
  },
  {
    id: "md-aquino",
    label: "Dr. Aquino",
    role: "Attending Physician",
    department: "medical",
    sessionRole: "md",
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
  // `who` is the clinician whose session ended by itself: only that clinician returns to it.
  const { from, who } = (useLocation().state ?? {}) as { from?: unknown; who?: unknown };
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
    const back =
      typeof from === "string" && from.startsWith("/") && from !== "/" && !from.startsWith("//") && !from.includes("\\") && !/\s/.test(from) &&
      (who === undefined || who === profile.id);
    navigate(back ? from : path, { replace: true }); // consumes this entry and the return page it holds
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
        navigate("/overview", { replace: true });
        return;
      }
      const role = await signIn(selected!.id, passphrase);
      // The server's role wins over the card's.
      if (role === "admin") {
        onSignIn({ clinician_id: selected!.id, ward: selected!.ward, role });
        navigate("/overview", { replace: true });
        return;
      }
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

  // The kit stepper: an administrator has no profile card and no destination to choose.
  const steps: [Phase, string][] =
    department === "admin"
      ? [["department", "Department"], ["auth", "Passphrase"]]
      : [["department", "Department"], ["profile", "Profile"], ["auth", "Passphrase"], ["destination", "Destination"]];
  const at = steps.findIndex(([p]) => p === phase);

  const choice = `hw-card hw-card--interactive hw-card--outlined ${styles.choice}`;

  return (
    <main className={styles.page}>
      {/* The front door: what this is, before anyone signs in. The sample build adds what a judge needs: that the data is a
          sample, and the evidence, readable without signing in. */}
      <section className={styles.brand}>
        <div className={styles.markRow}>
          <Mark />
          {!usesServer && (
            <>
              <span className="hw-tag hw-tag--round">Sample data</span>
              <span className="hw-text-muted">Synthetic, not validated.</span>
            </>
          )}
        </div>
        <div className={styles.pitch}>
          <p className="hw-heading hw-heading--2">A ranked early-warning list for hospital wards in the Philippines.</p>
          <p className="hw-text-muted">This is not a diagnosis system. It ranks and displays. A human decides.</p>
          {!usesServer && (
            <p className={styles.evidence}>
              {/* A link in link blue, not the kit's red call to action: red is clinical here. */}
              <Link to="/lab/research">
                See the evidence from the simulated trials <span className="hw-icon fa-solid fa-arrow-right" aria-hidden="true" />
              </Link>
            </p>
          )}
        </div>
      </section>

      <div className={styles.main}>
        <div className={`hw-card ${styles.card}`}>
          <div className={styles.head}>
            <h1 className="hw-heading hw-heading--3">Sign in</h1>
            {phase !== "department" && (
              <button type="button" className="hw-button hw-button--ghost hw-button--sm" onClick={handleBack}>
                <span className="hw-icon fa-solid fa-arrow-left" aria-hidden="true" />
                Back
              </button>
            )}
          </div>

          <ol className={`hw-steps ${styles.steps}`} aria-label="Steps">
            {steps.map(([p, label], i) => (
              <li
                key={p}
                className="hw-steps__step"
                data-status={i < at ? "done" : i === at ? "current" : undefined}
                aria-current={i === at ? "step" : undefined}
              >
                {label}
              </li>
            ))}
          </ol>

          {/* Phase 1: Department */}
          {phase === "department" && (
            <>
              <p className={`hw-text-muted ${styles.sectionLabel}`}>I am a</p>
              <div className={styles.cards}>
                <button type="button" className={choice} onClick={() => handlePickDepartment("nursing")}>
                  <span className="hw-card__title">Nurse</span>
                  <span className="hw-caption">Staff Nurse, Charge Nurse</span>
                </button>
                <button type="button" className={choice} onClick={() => handlePickDepartment("medical")}>
                  <span className="hw-card__title">Physician</span>
                  <span className="hw-caption">Attending Physician, Consultant</span>
                </button>
                {usesServer && (
                  <button type="button" className={choice} onClick={() => handlePickDepartment("admin")}>
                    <span className="hw-card__title">Administration</span>
                    <span className="hw-caption">Every patient on the ward, read only</span>
                  </button>
                )}
              </div>
            </>
          )}

          {/* Phase 2: Profile */}
          {phase === "profile" && (
            <>
              <p className={`hw-text-muted ${styles.sectionLabel}`}>
                {department === "nursing" ? "Select nurse" : "Select physician"}
              </p>
              <div className={styles.cards}>
                {filteredProfiles.map((p) => (
                  <button key={p.id} className={`${choice} ${styles.profile}`} onClick={() => handleSelectProfile(p)} type="button">
                    <span className="hw-avatar hw-avatar--lg">{initial(p)}</span>
                    <span className={styles.who}>
                      <span className="hw-card__title">{p.label}</span>
                      <span className="hw-caption">{p.role}</span>
                    </span>
                    <span className="hw-caption">Ward {p.ward}</span>
                  </button>
                ))}
              </div>
            </>
          )}

          {/* Phase 3: Auth */}
          {phase === "auth" && (selected || department === "admin") && (
            <form onSubmit={handleAuth} className={styles.form}>
              {selected ? (
                <Picked profile={selected} detail={selected.role} />
              ) : (
                <div className="hw-field hw-field--full">
                  <label className="hw-field__label" htmlFor="admin-id">
                    Administrator id
                  </label>
                  <div className="hw-input">
                    <input
                      className="hw-input__control"
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

              <div className="hw-field hw-field--full">
                <label className="hw-field__label" htmlFor="passphrase">
                  Passphrase
                </label>
                <div className="hw-input">
                  <input
                    className="hw-input__control"
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
                <p className="hw-alert hw-alert--danger" role="alert">
                  <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
                  <span className="hw-alert__body">{error}</span>
                </p>
              )}

              <button type="submit" className="hw-button hw-button--primary hw-button--block" disabled={busy}>
                {busy ? "Signing in" : "Continue"}
              </button>

              <p className="hw-caption">
                {usesServer
                  ? "After 5 failed attempts for a clinician, sign-in is locked for 15 minutes. Every attempt is recorded."
                  : "Public demo: any passphrase works. Nothing is kept after a reload."}
              </p>
            </form>
          )}

          {/* Phase 4: Destination */}
          {phase === "destination" && selected && (
            <>
              <Picked profile={selected} detail={ROLE_LABELS[selected.sessionRole]} />

              <p className={`hw-text-muted ${styles.sectionLabel}`}>Choose destination</p>
              <div className={styles.cards}>
                <button type="button" className={choice} onClick={() => signInAndGo(selected, "/ward")}>
                  <span className="hw-card__title">Ward Monitor</span>
                  <span className="hw-caption">Your assigned patients, in priority order.</span>
                </button>
                {(selected.sessionRole === "md" || selected.sessionRole === "charge_rn") && (
                  <button type="button" className={choice} onClick={() => signInAndGo(selected, "/station")}>
                    <span className="hw-card__title">Station</span>
                    <span className="hw-caption">Admit, labs, documents.</span>
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <footer className={`hw-caption ${styles.footer}`}>
        <p>
          <strong>Privacy</strong>{" "}
          {usesServer
            ? "Pilot build: synthetic data only. Not for real patients."
            : "Public demo: synthetic data, no server, no real sign-in"}
        </p>
        <p>Set in HarmonyOS Sans</p>
      </footer>
    </main>
  );
}

const initial = (p: Profile) => p.label.split(" ").pop()?.charAt(0) ?? p.label.charAt(0);

/** The chosen clinician, above the passphrase and the destinations. */
function Picked({ profile, detail }: { profile: Profile; detail: string }) {
  return (
    <div className={styles.picked}>
      <span className="hw-avatar hw-avatar--lg">{initial(profile)}</span>
      <span className={styles.who}>
        <span className="hw-heading hw-heading--4">{profile.label}</span>
        <span className="hw-caption">
          {detail} &middot; Ward {profile.ward}
        </span>
      </span>
    </div>
  );
}
