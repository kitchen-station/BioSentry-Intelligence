import { useCallback, useEffect, useState } from "react";
import type { Session, WardOverview } from "../types";
import { ApiError, getOverview, hasOverview } from "../api";
import { SimulatedNote } from "../components/LabFrame";
import { SessionMenu } from "../components/SessionMenu";
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
  const [loading, setLoading] = useState(hasOverview);

  const show = useCallback(() => {
    getOverview()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((failure: unknown) => {
        setError(
          failure instanceof ApiError && failure.status === 403
            ? "Not available for this account."
            : "The overview could not be loaded. Try Refresh.",
        );
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (hasOverview) show();
  }, [show]);

  const sim = data ? simStatus(data.shared) : null;
  const role = (id: string) => data?.clinicians.find((c) => c.id === id)?.role;
  const carers = (care: readonly string[], r: string) => care.filter((id) => role(id) === r).join(", ") || "None";
  const charge = data?.patients.some((p) => p.care.some((id) => role(id) === "charge_rn"));
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
        <p className={research.small}>Read-only. Every view is recorded in the audit log.</p>

        {!hasOverview ? (
          <p>The overview needs the live API. This demo has no server.</p>
        ) : (
          <>
            <div className={styles.status}>
              {data && <p>As of {when(data.as_of)}</p>}
              {sim && <p>Simulator: {formatSimClock(sim)}</p>}
              {data && !data.receiving_data && <p className={styles.warn}>Not receiving data</p>}
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
              <div className={research.scroll}>
                <table className={research.table}>
                  <thead>
                    <tr><th>Bed</th><th>Patient id</th><th>State</th><th>Nurse</th><th>Physician</th></tr>
                  </thead>
                  <tbody>
                    {data.patients.map((p) => (
                      <tr key={p.id}>
                        <th scope="row">{p.bed}</th>
                        <td>
                          {p.id}
                          {p.synthetic && <span className={research.sub}> Synthetic</span>}
                        </td>
                        <td>{p.state === "ranked" ? `NEWS2 ${p.score}` : `NOT RANKED: ${p.reason}`}</td>
                        <td>{carers(p.care, "rn")}</td>
                        <td>{carers(p.care, "md")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {charge && <p>The charge nurse is assigned to every patient.</p>}
            </section>

            <section className={research.card} aria-labelledby="clinicians">
              <h2 id="clinicians" className={research.heading}>Clinicians</h2>
              <div className={research.scroll}>
                <table className={research.table}>
                  <thead>
                    <tr><th>Clinician</th><th>Role</th><th>Patients</th></tr>
                  </thead>
                  <tbody>
                    {data.clinicians.map((c) => (
                      <tr key={c.id}>
                        <th scope="row">{c.id}</th>
                        <td>{c.role}</td>
                        <td>{c.patients}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={research.card} aria-labelledby="activity">
              <h2 id="activity" className={research.heading}>Activity</h2>
              {byClinician.size === 0 && <p>No activity in the last 24 hours.</p>}
              {[...byClinician].map(([id, rows]) => (
                <div key={id} className={styles.group}>
                  <h3>{id}</h3>
                  <ul>
                    {rows.map((a) => (
                      <li key={a.seq}>{when(a.at)} {a.action} {a.resource} {a.outcome}</li>
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
