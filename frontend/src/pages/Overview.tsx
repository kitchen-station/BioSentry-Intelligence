import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Session, WardOverview } from "../types";
import { ApiError, getOverview, hasOverview } from "../api";
import { AppShell } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { ROLE_LABELS } from "../components/SessionMenu";
import { formatSimClock, simStatus } from "../components/format";
import research from "./Research.module.css";
import styles from "./Overview.module.css";

const when = (iso: string) => `${iso.slice(0, 16).replace("T", " ")} UTC`;

// The audit log's event names, in words. An action not listed here reads as its own name with spaces.
const ACTIONS: Record<string, string> = {
  view_ward: "Ward list views",
  view_patient: "Patient views",
  acknowledge: "Alerts acknowledged",
  record_vitals: "Vitals recorded",
  add_note: "Notes added",
  view_notes: "Notes read",
  view_history: "Stay histories read",
  view_ward_research: "Ward research reads",
  admit_patient: "Patients admitted",
  enter_labs: "Lab results entered",
  ask_patient: "Questions about a patient",
  lab_question: "Questions for the research agent",
  lab_request: "Simulated runs asked for",
};
const OUTCOMES: Record<string, string> = { not_found: "not found", denied: "refused", rejected: "rejected", limited: "over the limit" };
const words = (s: string) => s.replaceAll("_", " ");
/** "Patient views", or "Patient views, not found" for an outcome other than ok. */
const activityLabel = (action: string, outcome: string): string =>
  `${ACTIONS[action] ?? words(action).replace(/^./, (c) => c.toUpperCase())}${outcome === "ok" ? "" : `, ${OUTCOMES[outcome] ?? words(outcome)}`}`;

/**
 * What an administrator sees of the ward: four status tiles, who is assigned, and activity as counts. It reads once
 * and again on Refresh, never on a timer: every view writes a row to the audit log, so a poll would bury the real activity.
 */
export function Overview({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [data, setData] = useState<WardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const admin = session.role === "admin";
  const [loading, setLoading] = useState(hasOverview && admin);
  const failed = "The overview could not be loaded. Try Refresh.";

  const show = useCallback(() => {
    getOverview()
      .then((d: unknown) => {
        const o = d as Partial<WardOverview> | null;
        const ok =
          typeof o?.as_of === "string" &&
          Array.isArray(o.patients) &&
          Array.isArray(o.clinicians) &&
          Array.isArray(o.activity) &&
          (o.sim === null || typeof o.sim === "object");
        setData(ok ? (o as WardOverview) : null);
        setError(ok ? null : failed);
      })
      .catch((failure: unknown) => {
        setData(null); // nothing stale stays on screen after a failed read
        const status = failure instanceof ApiError ? failure.status : 0;
        setError(
          status === 403 ? "Not available for this account."
            : status === 401 ? "Your session has ended. Sign in again."
            : failed,
        );
        if (status === 401) onSignOut();
      })
      .finally(() => setLoading(false));
  }, [onSignOut]);

  useEffect(() => {
    document.title = "Ward overview \u00b7 BioSentry";
    return () => {
      document.title = "BioSentry";
    };
  }, []);

  // In development React mounts twice, so the first open writes two audit rows; a production build writes one.
  useEffect(() => {
    if (hasOverview && admin) show();
  }, [show, admin]);

  const sim = data ? simStatus(data.sim) : null;
  const role = (id: string) => data?.clinicians.find((c) => c.id === id)?.role;
  const isCharge = (care: readonly string[]) => care.some((id) => role(id) === "charge_rn");
  const chargeAll = !!data && data.patients.length > 0 && data.patients.every((p) => isCharge(p.care));
  const nurses = (care: readonly string[]) =>
    care.filter((id) => role(id) === "rn" || role(id) === undefined || (role(id) === "charge_rn" && !chargeAll)).join(", ") || "None";
  const physicians = (care: readonly string[]) => care.filter((id) => role(id) === "md").join(", ") || "None";
  // Activity as counts: one row per clinician, one column per kind of event, in the order the server listed them.
  const kinds = [...new Set((data?.activity ?? []).map((a) => activityLabel(a.action, a.outcome)))];
  const counts = new Map<string, Map<string, number>>();
  for (const a of data?.activity ?? []) {
    const row = counts.get(a.clinician_id) ?? new Map<string, number>();
    const kind = activityLabel(a.action, a.outcome);
    counts.set(a.clinician_id, row.set(kind, (row.get(kind) ?? 0) + a.count));
  }
  const notRanked = data?.patients.filter((p) => p.state !== "ranked") ?? [];
  const reasons = [...new Set(notRanked.map((p) => p.reason))].join(" · ");

  return (
    <AppShell session={session} onSignOut={onSignOut} title="Administration">
      <SimulatedNote readOnly={admin} />
      <div className={research.main}>
        <h1 className="hw-heading hw-heading--3">Ward overview</h1>

        {!admin ? (
          <p className="hw-text-muted">Not available for this account.</p>
        ) : !hasOverview ? (
          <p className="hw-text-muted">The overview needs the live API. This demo has no server.</p>
        ) : (
          <>
            <div className={`${research.toolbar} hw-caption`}>
              {data && <p>As of {when(data.as_of)}</p>}
              <p>This page does not refresh by itself: every read is recorded.</p>
              <Link to="/ward">Ward list</Link>
              <button type="button" className="hw-button hw-button--accent hw-button--sm" onClick={() => { setLoading(true); show(); }} disabled={loading}>
                <span className="hw-icon fa-solid fa-rotate" aria-hidden="true" />
                Refresh
              </button>
            </div>
            {error && (
              <p role="alert" className="hw-alert hw-alert--danger">
                <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
                {error}
              </p>
            )}
          </>
        )}

        {data && (
          <>
            <section className={`hw-card hw-card--outlined ${styles.tiles}`} aria-label="Now">
              <div className="hw-stat">
                <span className="hw-stat__label">Patients</span>
                <div className="hw-stat__main"><span className="hw-stat__value">{data.patients.length}</span></div>
              </div>
              <div className="hw-stat">
                <span className="hw-stat__label">Ranked</span>
                <div className="hw-stat__main"><span className="hw-stat__value">{data.patients.length - notRanked.length}</span></div>
              </div>
              <div className="hw-stat">
                <span className="hw-stat__label">NOT RANKED</span>
                <div className="hw-stat__main"><span className="hw-stat__value">{notRanked.length}</span></div>
                {reasons && <span className="hw-stat__note">{reasons}</span>}
              </div>
              <div className="hw-stat">
                <span className="hw-stat__label">Data feed</span>
                {data.receiving_data ? (
                  <span className="hw-stat__change">
                    <span className="hw-icon fa-solid fa-signal" aria-hidden="true" />
                    Receiving data
                  </span>
                ) : (
                  <span className="hw-stat__change">
                    <span className="hw-icon fa-solid fa-ban" aria-hidden="true" />
                    Not receiving data
                  </span>
                )}
                {sim && (
                  <span className="hw-stat__note">
                    Simulator: {formatSimClock(sim)} &middot; {sim.counts.received} received &middot;{" "}
                    {sim.counts.rejected} rejected &middot; {sim.counts.abstained} abstained
                  </span>
                )}
              </div>
            </section>

            <section className={`hw-card hw-card--outlined ${research.card}`} aria-labelledby="patients">
              <h2 id="patients" className="hw-heading hw-heading--4">Patients</h2>
              <div className="hw-table-wrap" tabIndex={0} role="region" aria-labelledby="patients">
                <table className={`hw-table ${research.nowrap}`}>
                  <thead>
                    <tr><th scope="col">Bed</th><th scope="col">State</th><th scope="col">Nurse</th><th scope="col">Physician</th></tr>
                  </thead>
                  <tbody>
                    {data.patients.map((p) => (
                      <tr key={p.bed}>
                        <th scope="row">{p.bed}</th>
                        <td>
                          {p.state === "ranked" ? `NEWS2 ${p.score}` : `NOT RANKED: ${p.reason}`}
                          {p.synthetic && <> <span className="hw-tag">Synthetic</span></>}
                        </td>
                        <td>{nurses(p.care)}</td>
                        <td>{physicians(p.care)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {chargeAll && <p className="hw-caption">The charge nurse is assigned to every patient.</p>}
            </section>

            <section className={`hw-card hw-card--outlined ${research.card}`} aria-labelledby="clinicians">
              <h2 id="clinicians" className="hw-heading hw-heading--4">Clinicians</h2>
              <div className="hw-table-wrap" tabIndex={0} role="region" aria-labelledby="clinicians">
                <table className={`hw-table ${research.nowrap}`}>
                  <thead>
                    <tr><th scope="col">Clinician</th><th scope="col">Role</th><th scope="col" className="hw-table__num">Patients</th></tr>
                  </thead>
                  <tbody>
                    {data.clinicians.map((c) => (
                      <tr key={c.id}>
                        <th scope="row">{c.id}</th>
                        <td>{ROLE_LABELS[c.role] ?? c.role}</td>
                        <td className="hw-table__num">{c.patients}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={`hw-card hw-card--outlined ${research.card}`} aria-labelledby="activity">
              <h2 id="activity" className="hw-heading hw-heading--4">Activity in the last 24 hours</h2>
              <p className="hw-caption">
                Activity is shown for oversight, not performance review. Counts only: no patient and no time is shown.
              </p>
              {kinds.length === 0 ? (
                <p className="hw-text-muted">No activity in the last 24 hours.</p>
              ) : (
                <div className="hw-table-wrap" tabIndex={0} role="region" aria-labelledby="activity">
                  <table className="hw-table">
                    <thead>
                      <tr>
                        <th scope="col">Clinician</th>
                        {kinds.map((k) => <th key={k} scope="col" className="hw-table__num">{k}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {[...counts].map(([id, row]) => (
                        <tr key={id}>
                          <th scope="row">{id}</th>
                          {kinds.map((k) => (
                            <td key={k} className={`hw-table__num ${row.has(k) ? "" : research.sub}`}>{row.get(k) ?? 0}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
