import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import type { Session } from "../types";
import { ApiError, getWard, hasLabQuestions, hasPatientQuestions } from "../api";
import { SimulatedNote } from "../components/LabFrame";
import { AppShell } from "../components/AppShell";
import { Thread } from "../components/Thread";
import { useAskScope } from "../components/useAskScope";
import styles from "./AskChat.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

// A thread is a pill link; the current one (aria-current) gets its heavier edge in the module.
const PICK = `hw-button hw-button--sm ${styles.pick}`;

function Chat({ session, onSignOut }: Props) {
  const scope = useAskScope();
  const patientId = scope.kind === "patient" ? scope.id : undefined;
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
      <nav aria-label="Threads" className={styles.threads}>
        <ul className={styles.picker}>
          <li><NavLink to="/ask" end className={PICK}>The ward</NavLink></li>
          {patients?.map((p) => (
            <li key={p.id}><NavLink to={`/ask/patient/${p.id}`} className={PICK}>{p.name}</NavLink></li>
          ))}
        </ul>
        {failed && <p className="hw-caption">The patient list could not be loaded.</p>}
      </nav>
      <div className={`hw-card hw-card--outlined ${styles.chat}`}>
        {/* key: a new thread starts with its own state. */}
        <Thread
          key={patientId ?? "ward"}
          scope={scope.kind === "patient" ? scope : { kind: "ward" }}
          clinicianId={session.clinician_id}
          onSignOut={onSignOut}
        />
      </div>
    </>
  );
}

/** The chat: the ward's thread at /ask and one patient's at /ask/patient/:id. Nothing here writes to the live ward. */
export function AskChat({ session, onSignOut }: Props) {
  const connected = useAskScope().kind === "patient" ? hasPatientQuestions : hasLabQuestions;
  return (
    <AppShell session={session} onSignOut={onSignOut} title="Ask" status={`Ward ${session.ward}`}>
      <SimulatedNote />
      <div className={styles.container}>
        <h1 className="hw-heading hw-heading--3">Ask</h1>
        {connected ? (
          <Chat session={session} onSignOut={onSignOut} />
        ) : (
          <p className="hw-text-muted">Getting ready: the chat is not connected in this demo.</p>
        )}
      </div>
    </AppShell>
  );
}
