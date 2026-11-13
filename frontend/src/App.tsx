import { useState } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import type { Session } from "./types";
import { restoreSession, clearSession } from "./api";
import { SignIn } from "./pages/SignIn";
import { Ward } from "./pages/Ward";
import { PatientDetail } from "./pages/PatientDetail";
import { Station } from "./pages/Station";
import { Pipeline } from "./pages/Pipeline";
import { Research } from "./pages/Research";
import { Lab } from "./pages/Lab";
import { Overview } from "./pages/Overview";

// Per tab, gone when the tab closes. Only who is signed in is kept here, never a token: with the
// API the token lives in memory, so a reload returns to sign-in (restoreSession says false). With
// fixtures (no server) a reload keeps the clinician signed in.
const SESSION_KEY = "biosentry.session";
const ROLES = new Set(["rn", "md", "charge_rn", "admin"]);

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
    if (s && !restoreSession(s.clinician_id)) {
      saveSession(null); // nothing to sign requests with: never show a signed-in screen without it
      return null;
    }
    return s;
  });

  // SignIn has already signed in with the API (the passphrase step); this only records who.
  const handleSignIn = (s: Session) => {
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
        element={session ? <PatientDetail session={session} onSignOut={signOut} /> : <ToSignIn />}
      />
      <Route
        path="/station"
        element={
          session && (session.role === "md" || session.role === "charge_rn")
            ? <Station session={session} onSignOut={signOut} />
            : session ? <Navigate to={session.role === "admin" ? "/overview" : "/ward"} replace /> : <ToSignIn />
        }
      />
      <Route
        path="/overview"
        element={
          session ? <Overview session={session} onSignOut={signOut} /> : <ToSignIn />
        }
      />
      <Route
        path="/lab"
        element={session ? <Lab session={session} onSignOut={signOut} /> : <ToSignIn />}
      />
      <Route
        path="/lab/research"
        element={session ? <Research session={session} onSignOut={signOut} /> : <ToSignIn />}
      />
      <Route
        path="/lab/pipeline"
        element={session ? <Pipeline session={session} onSignOut={signOut} /> : <ToSignIn />}
      />
      <Route path="/research" element={session ? <Navigate to="/lab/research" replace /> : <ToSignIn />} />
      <Route path="/pipeline" element={session ? <Navigate to="/lab/pipeline" replace /> : <ToSignIn />} />
    </Routes>
  );
}
