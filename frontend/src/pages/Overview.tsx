import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Session, WardOverview } from "../types";
import { ApiError, getOverview, hasOverview } from "../api";
import { SimulatedNote } from "../components/LabFrame";
import { ROLE_LABELS, SessionMenu } from "../components/SessionMenu";
import { formatSimClock, simStatus } from "../components/format";
import research from "./Research.module.css";
import styles from "./Overview.module.css";

const when = (iso: string) => `${iso.slice(0, 16).replace("T", " ")} UTC`;

/**
 * What an administrator sees of the ward. It reads once and again on Refresh, never on a timer:
 * every view writes a row to the audit log, so a poll would bury the real activity.
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
  const byClinician = new Map<string, WardOverview["activity"][number][]>();
  for (const a of data?.activity ?? []) byClinician.set(a.clinician_id, [...(byClinician.get(a.clinician_id) ?? []), a]);

  return (
    <div className={styles.page}>
      <div className={styles.bar}>
        <span>BioSentry &middot; Administration</span>
        <SessionMenu session={session} onSignOut={onSignOut} />
      </div>
      <SimulatedNote />
      <main className={research.main}>
        <h1 className={research.title}>Ward overview</h1>

        {!admin ? (
          <p>Not available for this account.</p>
        ) : !hasOverview ? (
          <p>The overview needs the live API. This demo has no server.</p>
        ) : (
          <>
            <p className={research.small}>Read-only. Every view is recorded in the audit log.</p>
            <div className={styles.status}>
              {data && <p>As of {when(data.as_of)}</p>}
              {sim && <p>Simulator: {formatSimClock(sim)}</p>}
              {data && !data.receiving_data && <p className={styles.warn}>Not receiving data</p>}
              <Link to="/ward">Ward list</Link>
              <button type="button" className={styles.refresh} onClick={() => { setLoading(true); show(); }} disabled={loading}>
                Refresh
              </button>
            </div>
            {error && <p role="alert" className={styles.warn}>{error}</p>}
          </>
        )}

        {data && (
          <>
            <section className={research.card} aria-labelledby="patients">
              <h2 id="patients" className={research.heading}>Patients</h2>
              <div className={research.scroll} tabIndex={0} role="region" aria-labelledby="patients">
                <table className={`${research.table} ${styles.text}`}>
                  <thead>
                    <tr><th scope="col">Bed</th><th scope="col">State</th><th scope="col">Nurse</th><th scope="col">Physician</th></tr>
                  </thead>
                  <tbody>
                    {data.patients.map((p) => (
                      <tr key={p.bed}>
                        <th scope="row">{p.bed}</th>
                        <td>
                          {p.state === "ranked" ? `NEWS2 ${p.score}` : `NOT RANKED: ${p.reason}`}
                          {p.synthetic && <span className={research.sub}> Synthetic</span>}
                        </td>
                        <td>{nurses(p.care)}</td>
                        <td>{physicians(p.care)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {chargeAll && <p>The charge nurse is assigned to every patient.</p>}
            </section>

            <section className={research.card} aria-labelledby="clinicians">
              <h2 id="clinicians" className={research.heading}>Clinicians</h2>
              <div className={research.scroll} tabIndex={0} role="region" aria-labelledby="clinicians">
                <table className={`${research.table} ${styles.text}`}>
                  <thead>
                    <tr><th scope="col">Clinician</th><th scope="col">Role</th><th scope="col">Patients</th></tr>
                  </thead>
                  <tbody>
                    {data.clinicians.map((c) => (
                      <tr key={c.id}>
                        <th scope="row">{c.id}</th>
                        <td>{ROLE_LABELS[c.role] ?? c.role}</td>
                        <td>{c.patients}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={research.card} aria-labelledby="activity">
              <h2 id="activity" className={research.heading}>Activity in the last 24 hours</h2>
              <p className={research.small}>
                Activity is shown for oversight, not performance review. Counts only: no patient and no time is shown.
              </p>
              {byClinician.size === 0 && <p>No activity in the last 24 hours.</p>}
              {[...byClinician].map(([id, rows]) => (
                <div key={id} className={styles.group}>
                  <h3>{id}</h3>
                  <ul>
                    {rows.map((a) => (
                      <li key={`${a.action} ${a.outcome}`}>{a.action} {a.outcome}: {a.count}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
