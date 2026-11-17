import { useState } from "react";
import { Routes, Route, Navigate, useLocation, useSearchParams } from "react-router-dom";
import type { Session } from "./types";
import { restoreSession, clearSession, hasWardResearch, usesServer } from "./api";
import { SignIn } from "./pages/SignIn";
import { Ward } from "./pages/Ward";
import { PatientDetail } from "./pages/PatientDetail";
import { Admit } from "./pages/Admit";
import { Census } from "./pages/Census";
import { Pipeline } from "./pages/Pipeline";
import { Research } from "./pages/Research";
import { Lab } from "./pages/Lab";
import { AskChat } from "./pages/AskChat";
import { Overview } from "./pages/Overview";
import { WardResearch } from "./pages/WardResearch";
import { ChatBubble } from "./components/ChatBubble";
import { asksWard } from "./components/roles";
import { WardFeedProvider } from "./components/WardFeedProvider";

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

/**
 * Sends a signed-out visitor to sign-in, remembering where they were going. `gone` says why they are
 * signed out: undefined = no session in this tab yet (a deep link: any person may return to the page),
 * null = they signed out themselves (no return page), an id = that clinician's session ended by itself
 * (only the same clinician returns to the page).
 */
function ToSignIn({ gone }: { gone: string | null | undefined }) {
  const location = useLocation();
  return <Navigate to="/" replace state={gone === null ? null : { from: location.pathname, who: gone }} />;
}

/** The old Station, kept for one release: its Patient flow tab (?tab=flow) is Census now, the rest is Admit. */
function FromStation() {
  return <Navigate to={useSearchParams()[0].get("tab") === "flow" ? "/census" : "/admit"} replace />;
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

  const [gone, setGone] = useState<string | null | undefined>(undefined);

  // SignIn has already signed in with the API (the passphrase step); this only records who.
  const handleSignIn = (s: Session) => {
    saveSession(s);
    setGone(undefined);
    setSession(s);
  };

  // The menu passes true for a sign-out the person chose; a page that gets a 401 calls it with nothing.
  const signOut = (explicit?: boolean) => {
    setGone(explicit === true ? null : session?.clinician_id);
    clearSession();
    saveSession(null);
    setSession(null);
  };

  // The owner's rule: a clinician has no developer view. The research numbers and the pipeline open for an admin only.
  // The lab's own tools (simulated runs, a question for the research agent) open for those who can use them; a staff nurse cannot.
  // A physician or charge nurse sees them as "What-if runs" at /what-if; /lab is the administrator's "Simulation lab".
  // The fixture build (the public demo: no server, no real clinician) opens them for every demo role, so a judge can read them.
  const developer = session?.role === "admin" || !usesServer;
  const clinician = asksWard(session?.role); // the roles that may ask
  const toWard = <Navigate to="/ward" replace />;

  const routes = (
    <Routes>
      <Route
        path="/"
        element={<SignIn onSignIn={handleSignIn} />}
      />
      <Route
        path="/ward"
        element={session ? <Ward session={session} onSignOut={signOut} /> : <ToSignIn gone={gone} />}
      />
      {hasWardResearch && (
        <Route
          path="/ward/research"
          element={session ? (session.role === "md" || session.role === "charge_rn" ? <WardResearch session={session} onSignOut={signOut} /> : toWard) : <ToSignIn gone={gone} />}
        />
      )}
      <Route
        path="/patient/:id"
        element={session ? <PatientDetail session={session} onSignOut={signOut} /> : <ToSignIn gone={gone} />}
      />
      {/* Census and Admit, for a physician and the charge nurse; an administrator goes to the overview, a staff nurse to the ward. */}
      {([["/census", Census], ["/admit", Admit]] as const).map(([path, Page]) => (
        <Route
          key={path}
          path={path}
          element={
            session && (session.role === "md" || session.role === "charge_rn")
              ? <Page session={session} onSignOut={signOut} />
              : session ? <Navigate to={session.role === "admin" ? "/overview" : "/ward"} replace /> : <ToSignIn gone={gone} />
          }
        />
      ))}
      <Route path="/station" element={<FromStation />} />
      <Route
        path="/overview"
        element={
          session ? <Overview session={session} onSignOut={signOut} /> : <ToSignIn gone={gone} />
        }
      />
      {["/ask", "/ask/patient/:id"].map((path) => (
        <Route
          key={path}
          path={path}
          element={session ? (clinician ? <AskChat session={session} onSignOut={signOut} /> : toWard) : <ToSignIn gone={gone} />}
        />
      ))}
      <Route
        path="/what-if"
        element={session ? (clinician ? <Lab session={session} onSignOut={signOut} /> : toWard) : <ToSignIn gone={gone} />}
      />
      <Route
        path="/lab"
        element={session ? (developer ? <Lab session={session} onSignOut={signOut} /> : clinician ? <Navigate to="/what-if" replace /> : toWard) : <ToSignIn gone={gone} />}
      />
      <Route path="/lab/ask" element={session ? (clinician ? <Navigate to="/ask" replace /> : toWard) : <ToSignIn gone={gone} />} />
      <Route
        path="/lab/research"
        element={session ? (developer ? <Research session={session} onSignOut={signOut} /> : toWard) : <ToSignIn gone={gone} />}
      />
      <Route
        path="/lab/pipeline"
        element={session ? (developer ? <Pipeline session={session} onSignOut={signOut} /> : toWard) : <ToSignIn gone={gone} />}
      />
      <Route path="/research" element={session ? <Navigate to="/lab/research" replace /> : <ToSignIn gone={gone} />} />
      <Route path="/pipeline" element={session ? <Navigate to="/lab/pipeline" replace /> : <ToSignIn gone={gone} />} />
      <Route path="*" element={<Navigate to={session ? "/ward" : "/"} replace />} />
    </Routes>
  );

  // A signed-in clinician's pages share one read of the ward: the Ward rows and the bell on every page show the same alerts.
  // An administrator reads the ward only on the Ward page (it brings its own read), never in the background.
  // The assistant's panel, once around every page: the top bar's Assistant button opens it.
  const pages = session ? <ChatBubble session={session} onSignOut={signOut}>{routes}</ChatBubble> : routes;
  return session && session.role !== "admin" ? <WardFeedProvider key={session.clinician_id} session={session}>{pages}</WardFeedProvider> : pages;
}
