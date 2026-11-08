import { useState } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import type { Session } from "./types";
import { initSession, clearSession } from "./api";
import { SignIn } from "./pages/SignIn";
import { Ward } from "./pages/Ward";
import { PatientDetail } from "./pages/PatientDetail";
import { Station } from "./pages/Station";

// Per tab, gone when the tab closes: a reload or a link to a patient keeps the clinician signed
// in, and a shared ward device does not carry one clinician's session into another's tab.
const SESSION_KEY = "biosentry.session";
const ROLES = new Set(["rn", "md", "charge_rn"]);

function loadSession(): Session | null {
  try {
    const s: unknown = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null");
    if (
      s !== null && typeof s === "object" &&
      "clinician_id" in s && typeof s.clinician_id === "string" &&
      "ward" in s && typeof s.ward === "string" &&
      "role" in s && typeof s.role === "string" && ROLES.has(s.role)
    ) {
      return s as Session;
    }
  } catch {
    // Unreadable storage or malformed JSON: treat as signed out.
  }
  return null;
}

function saveSession(s: Session | null): void {
  try {
    if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage blocked: the session lives in memory only, as before.
  }
}

/** Sends a signed-out visitor to sign-in, remembering where they were going. */
function ToSignIn() {
  const location = useLocation();
  return <Navigate to="/" replace state={{ from: location.pathname }} />;
}

export function App() {
  const [session, setSession] = useState<Session | null>(() => {
    const s = loadSession();
    if (s) initSession(s.clinician_id);
    return s;
  });

  const handleSignIn = (s: Session) => {
    initSession(s.clinician_id);
    saveSession(s);
    setSession(s);
  };

  const signOut = () => {
    clearSession();
    saveSession(null);
    setSession(null);
  };

  return (
    <Routes>
      <Route
        path="/"
        element={<SignIn onSignIn={handleSignIn} />}
      />
      <Route
        path="/ward"
        element={session ? <Ward session={session} onSignOut={signOut} /> : <ToSignIn />}
      />
      <Route
        path="/patient/:id"
        element={session ? <PatientDetail session={session} /> : <ToSignIn />}
      />
      <Route
        path="/station"
        element={
          session && (session.role === "md" || session.role === "charge_rn")
            ? <Station session={session} onSignOut={signOut} />
            : session ? <Navigate to="/ward" replace /> : <ToSignIn />
        }
      />
    </Routes>
  );
}
