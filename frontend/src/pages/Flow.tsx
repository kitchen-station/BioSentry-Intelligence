import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { PatientStay } from "../types";
import { getStays } from "../api";
import { bedRows, dayTicks, FLOW_DAYS, flowStats, formatStay, recentMoves } from "./flowMath";
import styles from "./Flow.module.css";

const POLL_MS = 15_000;
const RECENT = 12;
const OUTCOME = { discharged: "Discharged", higher_care: "Higher care" } as const;

const when = (iso: string) =>
  new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const day = (ms: number) => new Date(ms).toLocaleDateString([], { day: "numeric", month: "short" });
const status = (stay: PatientStay) => (stay.outcome ? OUTCOME[stay.outcome] : "On the ward");

/** The Station's admin view of who came and went. Scoped by the server, like the ward. */
export function FlowSection() {
  const [stays, setStays] = useState<PatientStay[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const refresh = () =>
      getStays()
        .then((all) => {
          setStays(all);
          setNow(Date.now());
          setFailed(false);
        })
        .catch(() => setFailed(true));
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, []);

  const stats = stays && flowStats(stays, now);
  const recent = stays ? recentMoves(stays, RECENT) : [];
  return (
    <div className={styles.flow}>
      <header>
        <h1 className={styles.title}>Patient flow</h1>
        <p className={styles.sub}>Admissions, departures and bed use. Synthetic data.</p>
      </header>
      {failed && <p role="alert" className={styles.alert}>Could not refresh. Showing the last stays received.</p>}
      {stays === null || stats === null ? (
        !failed && <p className={styles.sub}>Loading...</p>
      ) : (
        <>
          <dl className={styles.stats}>
            <div><dt>On the ward</dt><dd>{stats.onWard}</dd></div>
            <div><dt>In, last 24 h</dt><dd>{stats.in24h}</dd></div>
            <div><dt>Out, last 24 h</dt><dd>{stats.out24h}</dd></div>
            <div><dt>Average stay</dt><dd>{stats.averageMs === null ? "--" : formatStay(stats.averageMs)}</dd></div>
          </dl>

          <section aria-label="Recent moves" className={styles.card}>
            <h2 className={styles.heading}>Recent movement</h2>
            {recent.length === 0 ? (
              <p className={styles.sub}>No arrivals or departures yet.</p>
            ) : (
              <table className={styles.table}>
                <thead>
                  <tr><th>Patient</th><th>Bed</th><th>Status</th><th>When</th><th>Stay</th></tr>
                </thead>
                <tbody>
                  {recent.map((s) => (
                    <tr key={s.id}>
                      <td>{s.left_at === null ? <Link to={`/patient/${s.id}`}>{s.name}</Link> : s.name}</td>
                      <td className={styles.mono}>{s.bed}</td>
                      <td><span className={`${styles.chip} ${styles[s.outcome ?? "open"]}`}>{status(s)}</span></td>
                      <td>{when((s.left_at ?? s.admitted_at)!)}</td>
                      <td className={styles.mono}>
                        {s.admitted_at
                          ? formatStay((s.left_at ? Date.parse(s.left_at) : now) - Date.parse(s.admitted_at))
                          : "--"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section aria-label="Bed timeline" className={styles.card}>
            <div className={styles.cardHead}>
              <h2 className={styles.heading}>Bed use, last {FLOW_DAYS} days</h2>
              <ul className={styles.key}>
                <li><i className={styles.open} /> On the ward</li>
                <li><i className={styles.discharged} /> Discharged</li>
                <li><i className={styles.higher_care} /> Higher care</li>
              </ul>
            </div>
            <div className={styles.row}>
              <span />
              <div className={styles.axis}>
                {dayTicks(now).map((t) => (
                  <span key={t.at} style={{ left: `${t.left}%` }}>{day(t.at)}</span>
                ))}
              </div>
            </div>
            {bedRows(stays, now).map(({ bed, bars }) => (
              <div className={styles.row} key={bed}>
                <span className={styles.mono}>{bed}</span>
                <div className={styles.track}>
                  {bars.map(({ stay, left, width }) => {
                    const text = `${stay.name}, ${status(stay)}`;
                    return (
                      <span
                        key={stay.id}
                        className={`${styles.stay} ${styles[stay.outcome ?? "open"]}`}
                        style={{ left: `${left}%`, width: `${width}%` }}
                        title={text}
                        role="img"
                        aria-label={text}
                      />
                    );
                  })}
                </div>
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
