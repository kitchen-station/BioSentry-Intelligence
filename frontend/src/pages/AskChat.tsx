import { useEffect, useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import type { Session } from "../types";
import { ApiError, getWard, hasLabQuestions, hasPatientQuestions } from "../api";
import { SimulatedNote } from "../components/LabFrame";
import { SessionMenu } from "../components/SessionMenu";
import { Thread } from "../components/Thread";
import ward from "./Ward.module.css";
import styles from "./AskChat.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

function Chat({ session, onSignOut, patientId }: Props & { patientId?: string }) {
  // The reader's patients, ranked and not ranked, for the picker. Without them the ward's thread still works.
  const [patients, setPatients] = useState<{ id: string; name: string }[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    getWard().then(
      (w) => setPatients([...w.ranked, ...w.abstained].map(({ id, name }) => ({ id, name }))),
      (e: unknown) => {
        if (e instanceof ApiError && e.status === 401) onSignOut();
        setFailed(true);
      },
    );
  }, [onSignOut]);

  return (
    <>
      <nav aria-label="Threads">
        <ul className={styles.picker}>
          <li><NavLink to="/ask" end className={styles.pick}>The ward</NavLink></li>
          {patients?.map((p) => (
            <li key={p.id}><NavLink to={`/ask/patient/${p.id}`} className={styles.pick}>{p.name}</NavLink></li>
          ))}
        </ul>
        {failed && <p className={styles.quiet}>The patient list could not be loaded.</p>}
      </nav>
      {/* key: a new thread starts with its own state. */}
      <Thread
        key={patientId ?? "ward"}
        scope={patientId ? { kind: "patient", id: patientId } : { kind: "ward" }}
        clinicianId={session.clinician_id}
        onSignOut={onSignOut}
      />
    </>
  );
}

/** The chat: the ward's thread at /ask and one patient's at /ask/patient/:id. Nothing here writes to the live ward. */
export function AskChat({ session, onSignOut }: Props) {
  const { id } = useParams();
  const connected = id ? hasPatientQuestions : hasLabQuestions;
  return (
    <div className={ward.page}>
      <div className={ward.statusBar}>
        <div className={ward.statusInner}>
          <div className={ward.statusLeft}>
            <span className={ward.statusItem}>
              <span className={ward.statusLabel}>BioSentry</span>
              <span className={ward.statusValue}>Ward {session.ward}</span>
            </span>
          </div>
          <div className={ward.statusRight}>
            <SessionMenu session={session} onSignOut={onSignOut} />
          </div>
        </div>
      </div>
      <SimulatedNote />
      <main className={ward.container}>
        <h1 className={styles.title}>Ask</h1>
        {connected ? (
          <Chat session={session} onSignOut={onSignOut} patientId={id} />
        ) : (
          <p className={styles.quiet}>Getting ready: the chat is not connected in this demo.</p>
        )}
      </main>
    </div>
  );
}
