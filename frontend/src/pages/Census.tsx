import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { columns } from "hw-ui";
import type { PatientStay, Session } from "../types";
import { getStays, getWard, usesServer } from "../api";
import { AppShell } from "../components/AppShell";
import { SimulatedNote } from "../components/LabFrame";
import { formatClock, formatDay } from "../components/format";
import { syncWardNow, useWardNow } from "../components/wardNow";
import { bedRows, dayTicks, FLOW_DAYS, flowStats, formatStay, occupancy, recentMoves } from "./flowMath";
import styles from "./Census.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

const RECENT = 12;
const DAY_MS = 86_400_000;
const OUTCOME = { discharged: "Discharged", higher_care: "Higher care", transferred: "Transferred", died: "Died" } as const;
type Outcome = keyof typeof OUTCOME;
const OUTCOMES = Object.keys(OUTCOME) as Outcome[];

const when = (iso: string) => `${formatDay(Date.parse(iso))}, ${formatClock(Date.parse(iso))}`;
const status = (stay: PatientStay) => (stay.outcome ? OUTCOME[stay.outcome] : "On the ward");
const place = (left: number, width?: number) =>
  ({ "--hw-timeline-beds-left": `${left}%`, ...(width !== undefined && { "--hw-timeline-beds-width": `${width}%` }) }) as CSSProperties;
const patients = (n: number) => `${n} ${n === 1 ? "patient" : "patients"}`;

/** Census: who came, who left, and how the beds were used. Scoped by the server, like the ward. */
export function Census({ session, onSignOut }: Props) {
  const [stays, setStays] = useState<PatientStay[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState<"all" | Outcome>("all");
  const [, render] = useState(0); // a ward read that lands after the stays moves the page's time
  const now = useWardNow(); // ward time from the newest ward or patient read
  const pollMs = usesServer ? 5_000 : 15_000; // the API changes as the simulator runs; the fixture never does
  const ids = useId();

  useEffect(() => {
    // The stays carry no time of their own: the ward is read for its time at every refresh, so at 10x or
    // 60x a new stay never starts after the page's clock (a negative length).
    const refresh = () =>
      Promise.all([
        getWard().then((w) => syncWardNow(w.now, !usesServer)),
        getStays().then(setStays),
      ]).then(
        () => {
          setFailed(false);
          render((n) => n + 1);
        },
        () => setFailed(true),
      );
    void refresh();
    const timer = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(timer);
  }, [pollMs]);

  const ready = stays !== null && now > 0;
  // The movement over the timeline's window, newest first; the outcome filter counts and narrows it.
  const moves = ready ? recentMoves(stays, now - FLOW_DAYS * DAY_MS) : [];
  const count = (k: "all" | Outcome) => (k === "all" ? moves.length : moves.filter((s) => s.outcome === k).length);
  const shown = (filter === "all" ? moves : moves.filter((s) => s.outcome === filter)).slice(0, RECENT);

  return (
    <AppShell session={session} onSignOut={onSignOut} title="Census">
      <SimulatedNote />
      <div className={styles.page} data-hw-space="clinical">
        <header>
          <h1 className="hw-heading hw-heading--2">Census</h1>
          <p className="hw-text-muted">Ward {session.ward} · who came, who left, and how the beds were used</p>
        </header>
        {failed && (
          <p role="alert" className={`hw-alert hw-alert--warning ${styles.alert}`}>
            <span className="hw-alert__icon hw-icon fa-solid fa-triangle-exclamation" aria-hidden="true" />
            <span className="hw-alert__body">Could not refresh. Showing the last stays received.</span>
          </p>
        )}
        {!ready ? (
          !failed && <p className="hw-text-muted">Loading...</p>
        ) : (
          <>
            <Today stays={stays} now={now} />

            <div className={styles.split}>
              <section aria-label="Occupancy by day" className="hw-card">
                <Occupancy days={occupancy(stays, now)} />
              </section>

              <section aria-labelledby={`${ids}-mv`} className={`hw-card ${styles.stack}`}>
                <h2 id={`${ids}-mv`} className="hw-heading hw-heading--4">Recent movement</h2>
                <div className="hw-filter" role="group" aria-label="Filter by outcome">
                  {(["all", ...OUTCOMES] as const).map((k) => (
                    <button key={k} type="button" className="hw-filter__item" aria-pressed={filter === k} onClick={() => setFilter(k)}>
                      {k === "all" ? "All" : OUTCOME[k]}{" "}
                      <span className="hw-filter__count" data-count={count(k)}>{count(k)}</span>
                    </button>
                  ))}
                </div>
                {moves.length === 0 ? (
                  <p className="hw-text-muted">No arrivals or departures yet.</p>
                ) : shown.length === 0 ? (
                  <p className="hw-text-muted">No stay ended this way in the last {FLOW_DAYS} days.</p>
                ) : (
                  <div className="hw-table-wrap" tabIndex={0} role="region" aria-label="Recent movement table">
                    <table className={`hw-table ${styles.table}`}>
                      <thead>
                        <tr><th>Patient</th><th>Bed</th><th>Status</th><th>When</th><th className="hw-table__num">Stay</th></tr>
                      </thead>
                      <tbody>
                        {shown.map((s) => (
                          <tr key={s.id}>
                            <td>{s.left_at === null ? <Link to={`/patient/${s.id}`}>{s.name}</Link> : s.name}</td>
                            <td>{s.bed}</td>
                            <td>{status(s)}</td>
                            <td>{when((s.left_at ?? s.admitted_at)!)}</td>
                            <td className="hw-table__num">
                              {s.admitted_at ? formatStay((s.left_at ? Date.parse(s.left_at) : now) - Date.parse(s.admitted_at)) : "--"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {shown.length < count(filter) && (
                  <p className="hw-caption">The newest {RECENT} of {count(filter)}.</p>
                )}
              </section>
            </div>

            <section aria-labelledby={`${ids}-beds`} className={`hw-card ${styles.stack}`}>
              <h2 id={`${ids}-beds`} className="hw-heading hw-heading--4">Bed use, last {FLOW_DAYS} days</h2>
              <div className="hw-timeline-beds">
                <ul className="hw-timeline-beds__legend" aria-label="Legend">
                  <li className="hw-timeline-beds__key"><span className="hw-timeline-beds__swatch" aria-hidden="true" />On the ward</li>
                  {OUTCOMES.map((k) => (
                    <li key={k} className="hw-timeline-beds__key">
                      <span className="hw-timeline-beds__mark" data-outcome={k} aria-hidden="true" />{OUTCOME[k]}
                    </li>
                  ))}
                </ul>
                <div className="hw-timeline-beds__scroll" tabIndex={0} role="region" aria-label="Bed timeline">
                  <div className="hw-timeline-beds__chart">
                    {bedRows(stays, now).map(({ bed, bars }) => (
                      <div className="hw-timeline-beds__row" key={bed}>
                        <span className="hw-timeline-beds__bed">{bed}</span>
                        <div className="hw-timeline-beds__track">
                          {bars.map(({ stay, left, width }) => (
                            <span
                              key={stay.id}
                              className="hw-timeline-beds__stay"
                              data-outcome={stay.outcome ?? undefined}
                              style={place(left, width)}
                              title={`${stay.name}, ${status(stay)}`}
                              role="img"
                              aria-label={`${stay.name}, ${status(stay)}`}
                            >
                              {width >= 8 && <span className="hw-timeline-beds__name">{stay.name.split(",")[0]}</span>}
                              {stay.outcome && <span className="hw-timeline-beds__mark" data-outcome={stay.outcome} aria-hidden="true" />}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                    <div className="hw-timeline-beds__axis" aria-hidden="true">
                      {dayTicks(now).map((t) => (
                        <span key={t.at} className="hw-timeline-beds__tick" style={place(t.left)}>{formatDay(t.at)}</span>
                      ))}
                      <span className="hw-timeline-beds__now">Now</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}

/** The four numbers. Each <dt> is followed directly by its <dd>: a reader moves from the name to the number. */
function Today({ stays, now }: { stays: PatientStay[]; now: number }) {
  const stats = flowStats(stays, now);
  return (
    <dl className={styles.stats}>
      <div className="hw-stat"><dt className="hw-stat__label">On the ward</dt><dd className="hw-stat__value">{stats.onWard}</dd></div>
      <div className="hw-stat"><dt className="hw-stat__label">In, last 24 h</dt><dd className="hw-stat__value">{stats.in24h}</dd></div>
      <div className="hw-stat"><dt className="hw-stat__label">Out, last 24 h</dt><dd className="hw-stat__value">{stats.out24h}</dd></div>
      <div className="hw-stat"><dt className="hw-stat__label">Average stay</dt><dd className="hw-stat__value">{stats.averageMs === null ? "--" : formatStay(stats.averageMs)}</dd></div>
    </dl>
  );
}

/**
 * Occupancy by day on the kit's columns graph, with its finding as the title. Only the days the stays can count
 * (see occupancy); fewer than two and the graph is left out, saying why.
 */
function Occupancy({ days }: { days: { at: number; count: number }[] }) {
  const root = useRef<HTMLDivElement>(null);
  // Rebuilt only when what it shows changes: a timed read with the same counts leaves the reader's pick alone.
  const spec = JSON.stringify({ x: days.map((d) => formatDay(d.at)), values: days.map((d) => d.count) });
  useEffect(() => {
    if (!root.current) return;
    const { x, values } = JSON.parse(spec) as { x: string[]; values: number[] };
    const most = Math.max(...values);
    const g = columns(root.current, {
      title:
        Math.min(...values) === most
          ? `The ward held ${patients(most)} on each of the last ${values.length} days`
          : `The ward was fullest on ${x[values.lastIndexOf(most)]}, with ${patients(most)}`,
      subtitle:
        `Patients on the ward at this time of day, ${x[0]} to ${x[x.length - 1]}.` +
        (values.length < FLOW_DAYS ? ` Earlier days are left out: an arrival before ${x[0]} is not recorded.` : ""),
      x,
      values,
      averageLabel: `${values.length}-day average`,
      noun: ["patient", "patients"],
      level: 2,
    });
    return () => g.destroy();
  }, [spec]);
  if (days.length < 2) {
    return (
      <>
        <h2 className="hw-heading hw-heading--4">Occupancy by day</h2>
        <p className="hw-text-muted">
          Not shown. A daily count needs the arrival of every patient on the ward that day, and an arrival in the last{" "}
          {FLOW_DAYS} days is not recorded.
        </p>
      </>
    );
  }
  return <div ref={root} />;
}
