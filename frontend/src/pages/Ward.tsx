import { useCallback, useId, useState } from "react";
import { Link } from "react-router-dom";
import type { AbstainedRow as AbstainedRowType, ScoreRow, Session, VitalsRecorded } from "../types";
import { canRecordVitals } from "../api";
import { AppShell } from "../components/AppShell";
import { Flip, useMoveOnReorder } from "../components/Flip";
import { formatBloodPressure, formatClock, formatSimClock } from "../components/format";
import { SimulatedNote } from "../components/LabFrame";
import { MeasurementDisplay } from "../components/MeasurementDisplay";
import { band, obsDue } from "../components/risk";
import { Score } from "../components/Score";
import { VitalsEntry } from "../components/VitalsEntry";
import { news2Points } from "../components/news2";
import { sentence, standing } from "../components/vitalsDraft";
import { DataAge, WardRow, responseLine } from "../components/WardRow";
import { useWardFeed, type WardFeed } from "../components/wardFeed";
import { WardFeedProvider } from "../components/WardFeedProvider";
import { since, useWardNow } from "../components/wardNow";
import { NotRankedSection } from "./NotRankedSection";
import styles from "./Ward.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

type Filter = "all" | "high" | "medium" | "rising" | "nr" | "overdue";

const FILTERS: readonly (readonly [Filter, string])[] = [
  ["all", "All"], ["high", "High"], ["medium", "Medium"], ["rising", "Rising"], ["nr", "Not ranked"], ["overdue", "Overdue"],
];

const NO_MATCH: Record<Exclude<Filter, "all">, string> = {
  high: "No patient is in the high band",
  medium: "No patient is in the medium band",
  rising: "No patient’s NEWS2 is rising",
  nr: "Every patient is ranked",
  overdue: "No observations are overdue",
};

/** Two or more exclusive choices as the kit's segmented control: real radios under a named group. */
function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void;
}) {
  const name = useId();
  return (
    <fieldset className="hw-segmented hw-segmented--sm hw-segmented--accent">
      <legend className="hw-visually-hidden">{label}</legend>
      {options.map(([v, text]) => (
        <label key={v}>
          <input type="radio" className="hw-segmented__input" name={name} checked={value === v} onChange={() => onChange(v)} />
          <span className="hw-segmented__option">{text}</span>
        </label>
      ))}
    </fieldset>
  );
}

/** A NOT RANKED patient in the table: its reason in place of a next step, and Record vitals opening under the row. */
function NotRankedTableRow({ row, now, canRecord, offline, clinicianId, onRecorded }: {
  row: AbstainedRowType; now: number; canRecord: boolean; offline: boolean; clinicianId: string;
  onRecorded: (row: AbstainedRowType, recorded: VitalsRecorded) => void;
}) {
  const [entering, setEntering] = useState(false);
  const v = row.vitals;
  return (
    <>
      <tr>
        <td>--</td>
        <td>{row.bed}</td>
        <td className={styles.who}><Link to={`/patient/${row.id}`}>{row.name}</Link> <span className={styles.sub}>{row.age ?? "--"}{row.sex ? ` ${row.sex}` : ""}</span></td>
        <td><span className="hw-tag hw-tag--dashed">NOT RANKED</span></td>
        <td><strong>{sentence(row.reason)}</strong> {sentence(row.instruction)}</td>
        <td>{v ? <MeasurementDisplay now={now} measurement={v.heart_rate} /> : "--"}</td>
        <td>{v ? <MeasurementDisplay now={now} measurement={v.respiratory_rate} /> : "--"}</td>
        <td>{v ? <MeasurementDisplay now={now} measurement={v.spo2} format={(n) => `${n}%`} /> : "--"}</td>
        <td>{v ? <MeasurementDisplay now={now} measurement={v.blood_pressure} format={formatBloodPressure} /> : "--"}</td>
        <td>{v ? <MeasurementDisplay now={now} measurement={v.consciousness} /> : "--"}</td>
        <td>--</td>
        <td>
          {canRecord && (
            <button
              type="button"
              className={entering ? "hw-button hw-button--sm" : "hw-button hw-button--accent hw-button--sm"}
              aria-expanded={entering}
              aria-label={`${entering ? "Close" : "Record vitals"} for ${row.name}`}
              disabled={offline && !entering}
              onClick={() => setEntering((o) => !o)}
            >
              {entering ? "Close" : "Record vitals"}
            </button>
          )}
        </td>
      </tr>
      {canRecord && entering && (
        <tr>
          <td colSpan={12}>
            <VitalsEntry
              patientId={row.id}
              patientName={row.name}
              clinicianId={clinicianId}
              onRecorded={(recorded) => onRecorded(row, recorded)}
              onClose={() => setEntering(false)}
            />
          </td>
        </tr>
      )}
    </>
  );
}

export function Ward(props: Props) {
  const feed = useWardFeed();
  // A clinician's pages share App's read of the ward; the administrator's Ward page brings its own (once and on Refresh).
  return feed ? (
    <WardPage {...props} feed={feed} />
  ) : (
    <WardFeedProvider session={props.session}>
      <Ward {...props} />
    </WardFeedProvider>
  );
}

function WardPage({ session, onSignOut, feed }: Props & { feed: WardFeed }) {
  const { data, fetchedAt, failedSince, sim, refresh, pollMs, readOnly, alerts, acknowledge, setNotice } = feed;
  const now = useWardNow(); // ward time as served at the last good read
  const [filter, setFilter] = useState<Filter>("all");
  const [density, setDensity] = useState<"comfortable" | "compact">("comfortable");
  const [view, setView] = useState<"list" | "table">("list");
  const [sort, setSort] = useState<"rank" | "bed">("rank");
  const moveRef = useMoveOnReorder(data ? data.ranked.map((r) => r.id) : []);

  // A recorded value changes a patient's standing, which only the server can compute: say
  // what it said, then fetch the ward again so the ranking on screen is the server's.
  const handleRecorded = useCallback((row: AbstainedRowType, recorded: VitalsRecorded) => {
    setNotice(`Recorded for ${row.name}. ${standing(recorded)}`);
    refresh();
  }, [setNotice, refresh]);

  const retry = readOnly ? "Use Refresh to try again." : `Retrying every ${pollMs / 1000} seconds.`;

  if (!data) {
    return (
      <AppShell session={session} onSignOut={onSignOut}>
        <SimulatedNote />
        <div className={styles.container}>
          <h1 className={styles.title}>Ward {session.ward}</h1>
          {failedSince ? (
            <>
              <p role="alert" className={`hw-alert hw-alert--danger ${styles.block}`}>The ward server cannot be reached. {retry}</p>
              {readOnly && <button type="button" className="hw-button hw-button--sm" onClick={refresh}>Refresh</button>}
            </>
          ) : (
            <div role="status" className={styles.box}>
              <span className="hw-visually-hidden">Loading ward…</span>
              {[38, 30, 42, 34, 28, 36].map((w) => (
                <div key={w} className={styles.skeletonRow} aria-hidden="true">
                  <span className={`hw-skeleton ${styles.skeletonTile}`} />
                  <span className={styles.skeletonText}>
                    <span className="hw-skeleton hw-skeleton--title" style={{ width: `${w}%` }} />
                    <span className="hw-skeleton" style={{ width: `${w + 16}%` }} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </AppShell>
    );
  }

  const offline = failedSince !== null;
  const canRecord = canRecordVitals && !readOnly;
  const ageOffsetS = Math.max(0, Math.floor((now - Date.parse(data.now)) / 1000)); // time since the read, on the ward's base
  const match: Record<Exclude<Filter, "nr">, (r: ScoreRow) => boolean> = {
    all: () => true,
    high: (r) => band(r) === "high",
    medium: (r) => band(r) === "medium" || band(r) === "low-medium",
    rising: (r) => r.trend === "rising",
    overdue: (r) => {
      const due = obsDue(r);
      return due !== "continuous" && due <= now;
    },
  };
  const count = (f: Filter) =>
    f === "all" ? data.ranked.length + data.abstained.length : f === "nr" ? data.abstained.length : data.ranked.filter(match[f]).length;
  const ranked = filter === "nr" ? [] : data.ranked.filter(match[filter]);
  const unranked = filter === "all" || filter === "nr" ? data.abstained : [];
  const empty = data.ranked.length === 0 && data.abstained.length === 0;
  const alertIds = new Set(alerts.map((a) => a.id));
  const tiers = data.tier_status.filter((t) => t.live).map((t) => t.label).join(", ") || "NEWS2";
  const total = count("all");
  const ack = (row: ScoreRow) => (alertIds.has(row.id) && !readOnly ? () => acknowledge(row) : undefined);

  const status = (
    <>
      <span className={styles.statusItem} data-tone={!data.receiving_data || offline ? "warn" : undefined}>
        <span className="hw-icon fa-solid fa-signal" aria-hidden="true" />
        {!data.receiving_data
          ? "No signal"
          : failedSince
            ? `Offline since ${formatClock(since(failedSince))}`
            : `Updated ${fetchedAt ? formatClock(fetchedAt) : "--"}`}
      </span>
      {sim !== null && (
        <span className={styles.statusItem} role="group" aria-label="Ward clock">
          <span className={styles.statusLabel}>Ward clock</span>
          <span className={styles.statusValue}><Flip>{formatSimClock(sim)}</Flip></span>
        </span>
      )}
    </>
  );

  const tableRows: (ScoreRow | AbstainedRowType)[] = [...unranked, ...ranked];
  if (sort === "bed") tableRows.sort((a, b) => a.bed.localeCompare(b.bed, undefined, { numeric: true }));

  return (
    <AppShell session={session} onSignOut={onSignOut} title={`Ward ${data.ward}`} status={status}>
      {failedSince && (
        <div className={`hw-banner hw-banner--inverse ${styles.offline}`} role="alert">
          <span className="hw-icon fa-solid fa-ban" aria-hidden="true" />
          <strong className="hw-banner__title">Offline since {formatClock(since(failedSince))}</strong>
          <span>
            Cannot reach the ward server. Showing data from {fetchedAt ? formatClock(fetchedAt) : "--"}; ages below stand at that time. {retry}
            {!readOnly && " Acknowledging and recording wait until the connection is back."}
          </span>
        </div>
      )}
      <SimulatedNote />
      <div className={styles.container}>
        <div className={styles.header}>
          <div>
            <h1 className={styles.title}>Ward {data.ward}</h1>
            <p className={styles.countLine}>
              {total} {total === 1 ? "patient" : "patients"} &middot; {data.ranked.length} ranked on {tiers}
            </p>
          </div>
          <div className={styles.switches}>
            <Segmented label="Row density" value={density} onChange={setDensity} options={[["comfortable", "Comfortable"], ["compact", "Compact"]]} />
            <Segmented label="View" value={view} onChange={setView} options={[["list", "List"], ["table", "Table"]]} />
          </div>
        </div>

        {readOnly && (
          <p className={`hw-alert ${styles.block}`}>
            <span className="hw-alert__body">Read-only. Views are recorded in the audit log.</span>{" "}
            <button type="button" className="hw-button hw-button--sm" onClick={refresh}>Refresh</button>
          </p>
        )}

        <div className={`hw-filter ${styles.filters}`} role="group" aria-label="Filter patients">
          {FILTERS.map(([f, label]) => {
            const n = count(f);
            const tone = n > 0 && f === "high" ? "emergency" : n > 0 && f === "medium" ? "urgent" : undefined;
            return (
              <button key={f} type="button" className="hw-filter__item" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {label}{" "}
                <span className="hw-filter__count" data-tone={tone} data-count={n}><Flip>{n}</Flip></span>
              </button>
            );
          })}
        </div>

        {view === "list" && (
          <NotRankedSection rows={unranked} clinicianId={session.clinician_id} readOnly={readOnly} offline={offline} onRecorded={handleRecorded} />
        )}

        {view === "list" && ranked.length > 0 && (
          <section aria-label="Ranked patients" className={`hw-ranklist-wrap ${styles.panel}`}>
            <div className="hw-ranklist__head" aria-hidden="true">
              <span>Rank</span><span>NEWS2</span><span>Patient and next step</span><span>Heart rate</span><span>Resp. rate</span>
              <span>SpO2</span><span>BP</span><span>ACVPU</span><span>Data</span><span />
            </div>
            <ol className="hw-ranklist" data-density={density}>
              {ranked.map((row) => (
                <WardRow
                  key={row.id}
                  ref={moveRef(row.id)}
                  row={row}
                  role={session.role}
                  now={now}
                  ageOffsetS={ageOffsetS}
                  compact={density === "compact"}
                  alert={alertIds.has(row.id)}
                  offline={offline}
                  onAcknowledge={ack(row)}
                />
              ))}
            </ol>
          </section>
        )}

        {view === "table" && tableRows.length > 0 && (
          <div className={`hw-table-wrap ${styles.panel}`}>
            <table className="hw-table">
              <caption className="hw-visually-hidden">Patients on Ward {data.ward}</caption>
              <thead>
                <tr>
                  {(["rank", "bed"] as const).map((k) => (
                    <th key={k} aria-sort={sort === k ? "ascending" : undefined}>
                      <button type="button" className={styles.sort} onClick={() => setSort(k)}>{k === "rank" ? "Rank" : "Bed"}</button>
                    </th>
                  ))}
                  <th>Patient</th><th>NEWS2</th><th>Next step</th><th>HR</th><th>RR</th><th>SpO2</th><th>BP</th><th>ACVPU</th><th>Data age</th>
                  <th><span className="hw-visually-hidden">Action</span></th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((r) => {
                  if (!("rank" in r)) {
                    return (
                      <NotRankedTableRow key={r.id} row={r} now={now} canRecord={canRecord} offline={offline} clinicianId={session.clinician_id} onRecorded={handleRecorded} />
                    );
                  }
                  const b = band(r);
                  const p = news2Points(r.vitals);
                  const onAck = ack(r);
                  return (
                    <tr key={r.id} data-alert={alertIds.has(r.id) || undefined}>
                      <td>{r.rank}</td>
                      <td>{r.bed}</td>
                      <td className={styles.who}><Link to={`/patient/${r.id}`}>{r.name}</Link> <span className={styles.sub}>{r.age ?? "--"}{r.sex ? ` ${r.sex}` : ""}</span></td>
                      <td><Score score={r.score} band={b} rising={r.trend === "rising"} small /></td>
                      <td className={styles.next} data-band={b}>{responseLine(r, b, session.role) ?? "--"}</td>
                      <td className={styles.vital} data-points={p.heart_rate}><MeasurementDisplay now={now} measurement={r.vitals.heart_rate} /></td>
                      <td className={styles.vital} data-points={p.respiratory_rate}><MeasurementDisplay now={now} measurement={r.vitals.respiratory_rate} /></td>
                      <td className={styles.vital} data-points={p.spo2}><MeasurementDisplay now={now} measurement={r.vitals.spo2} format={(n) => `${n}%`} /></td>
                      <td className={styles.vital} data-points={p.blood_pressure}>
                        <MeasurementDisplay now={now} measurement={r.vitals.blood_pressure} format={formatBloodPressure} />
                      </td>
                      <td className={styles.vital} data-points={p.consciousness}><MeasurementDisplay now={now} measurement={r.vitals.consciousness} /></td>
                      <td><DataAge row={r} ageOffsetS={ageOffsetS} offline={offline} /></td>
                      <td>
                        {onAck && (
                          <button
                            type="button"
                            className="hw-button hw-button--accent hw-button--sm"
                            disabled={offline}
                            aria-label={`Acknowledge the alert for ${r.name}`}
                            onClick={onAck}
                          >
                            Acknowledge
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {empty && (
          <div className={`hw-empty ${styles.box}`}>
            <span className="hw-empty__art" aria-hidden="true"><span className="hw-icon fa-solid fa-bed" /></span>
            <p className="hw-empty__title">No patients on Ward {data.ward}</p>
            <p>A patient appears here once admitted, and stays NOT RANKED until the first complete set of observations is in.</p>
            {(session.role === "md" || session.role === "charge_rn") && (
              <Link className="hw-button hw-button--primary" to="/station">Admit a patient</Link>
            )}
          </div>
        )}

        {!empty && filter !== "all" && ranked.length === 0 && unranked.length === 0 && (
          <div className={`hw-empty ${styles.box}`}>
            <p className="hw-empty__title">{NO_MATCH[filter]}</p>
            <p>The ranked list is unchanged. This filter only hides rows.</p>
            <button type="button" className="hw-button" onClick={() => setFilter("all")}>Show all patients</button>
          </div>
        )}

        <p className={styles.footer}>
          Advisory only. BioSentry does not replace a monitor alarm. Views are
          logged for audit under {session.clinician_id}.
        </p>
      </div>
    </AppShell>
  );
}
