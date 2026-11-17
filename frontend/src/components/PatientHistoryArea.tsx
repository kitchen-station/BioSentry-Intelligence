import { useEffect, useId, useState } from "react";
import { ApiError, getAssistantHistory, getPatientRun, getPatientRuns } from "../api";
import type { AssistantMessage, LabRunDetail, LabRunItem } from "../types";
import thread from "./Thread.module.css";

type Props = { patientId: string; onSignOut: () => void };
const when = (iso: string) => `${iso.slice(0, 16).replace("T", " ")} UTC`;

function Empty({ children }: { children: string }) {
  return <div className="hw-empty"><p className="hw-empty__title">{children}</p></div>;
}

function RunResult({ patientId, runId, onSignOut }: Props & { runId: number }) {
  const [detail, setDetail] = useState<LabRunDetail | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    getPatientRun(patientId, runId).then((value) => {
      if (active) setDetail(value);
    }, (error: unknown) => {
      if (!active) return;
      if (error instanceof ApiError && error.status === 401) onSignOut();
      setFailed(true);
    });
    return () => { active = false; };
  }, [patientId, runId, onSignOut]);
  return (
    <section className="hw-card hw-card--outlined hw-stack" aria-label={`Simulated result ${runId}`}>
      <h4 className="hw-heading hw-heading--4">Simulated result {runId}</h4>
      <p className="hw-caption">Synthetic, not validated.</p>
      {failed ? <p role="alert">The simulated result could not be read.</p> : detail ? (
        <>
          <p>{detail.baseline} compared with {detail.change}. Seed {detail.seed}, {detail.days} days.</p>
          {detail.report_text ? detail.report_text.split("\n").map((line, i) => <p key={i} className={thread.reply}>{line}</p>) : <p>No result text stored.</p>}
        </>
      ) : <p role="status">Loading simulated result…</p>}
    </section>
  );
}

/** Explicit reads only: opening the patient or pressing Refresh records an audited view. */
export function PatientHistoryArea({ patientId, onSignOut }: Props) {
  const id = useId();
  const [data, setData] = useState<{ messages: AssistantMessage[]; runs: LabRunItem[] } | null>(null);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    let active = true;
    Promise.all([getAssistantHistory(patientId), getPatientRuns(patientId)]).then(([messages, runs]) => {
      if (active) setData({ messages, runs });
    }, (error: unknown) => {
      if (!active) return;
      if (error instanceof ApiError && error.status === 401) onSignOut();
      setFailed(true);
    });
    return () => { active = false; };
  }, [patientId, onSignOut, revision]);

  return (
    <section className="hw-card hw-card--outlined hw-stack" aria-labelledby={`${id}-title`}>
      <div className="hw-cluster">
        <h2 id={`${id}-title`} className="hw-heading hw-heading--3">History</h2>
        <button className="hw-button hw-button--sm" type="button" onClick={() => {
          setData(null); setFailed(false); setSelected(null); setRevision((v) => v + 1);
        }}>Refresh history</button>
      </div>
      {failed ? <p role="alert">History could not be read. Try Refresh history.</p> : !data ? <p role="status">Loading history…</p> : (
        <>
          <section className="hw-stack" aria-labelledby={`${id}-chat`}>
            <h3 id={`${id}-chat`} className="hw-heading hw-heading--4">Assistant chat</h3>
            <p className="hw-caption">Only your messages and answers, kept for 90 days.</p>
            {data.messages.length === 0 ? <Empty>No assistant messages in the last 90 days.</Empty> : (
              <ol className="hw-timeline">
                {data.messages.map((message) => <li key={message.id} className="hw-timeline__item">
                  <time className="hw-timeline__time" dateTime={message.created_at}>{when(message.created_at)}</time>
                  <strong>{message.role === "user" ? "You" : "Assistant"}</strong>
                  {message.text.split("\n").map((line, i) => <p key={i} className={thread.reply}>{line}</p>)}
                </li>)}
              </ol>
            )}
          </section>
          <section className="hw-stack" aria-labelledby={`${id}-runs`}>
            <h3 id={`${id}-runs`} className="hw-heading hw-heading--4">Simulated runs</h3>
            <p className="hw-caption">Simulated results that include this patient. Synthetic, not validated.</p>
            {data.runs.length === 0 ? <Empty>No simulated runs linked to this patient yet.</Empty> : (
              <ol className="hw-timeline">
                {data.runs.map((run) => <li key={run.id} className="hw-timeline__item">
                  <time className="hw-timeline__time" dateTime={run.requested_at}>{when(run.requested_at)}</time>
                  <span className="hw-tag hw-tag--info">Simulated</span>
                  <p>{run.baseline} compared with {run.change}</p>
                  <button type="button" className="hw-button hw-button--sm" onClick={() => setSelected(run.id)}>View simulated run {run.id}</button>
                </li>)}
              </ol>
            )}
            {selected !== null && <RunResult key={selected} patientId={patientId} runId={selected} onSignOut={onSignOut} />}
          </section>
        </>
      )}
    </section>
  );
}
