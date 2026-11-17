import { matchPath, useLocation } from "react-router-dom";

/** What the assistant is asked about: one patient, the ward, or nothing (the sign-in page). */
export type AskScope = { kind: "patient"; id: string } | { kind: "ward" } | { kind: "none" };

/** One rule for the scope: a patient's page (or Ask page) is that patient, the sign-in page is none, every other page is the ward. */
export function askScopeOf(pathname: string): AskScope {
  if (pathname === "/") return { kind: "none" };
  let path = pathname;
  try { path = decodeURI(pathname); } catch { /* a malformed escape stays as typed */ }
  const id = (matchPath("/patient/:id", path) ?? matchPath("/ask/patient/:id", path))?.params.id; // a trailing slash matches
  return id ? { kind: "patient", id } : { kind: "ward" };
}

export const useAskScope = (): AskScope => askScopeOf(useLocation().pathname);
