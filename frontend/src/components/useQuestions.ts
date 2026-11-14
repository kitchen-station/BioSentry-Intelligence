import { useCallback, useEffect, useRef, useState } from "react";
import type { QuestionDetail, QuestionItem } from "../types";
import { ApiError } from "../api";
import { formatClock } from "./format";

// The same pace as the lab run list.
const POLL_MS = 5_000;
export const MAX = 500;
const AUTO = 5;

export const clock = (iso: string) => formatClock(Date.parse(iso));
const isOpen = (q: QuestionItem) => q.state === "queued" || q.state === "running";
// The worker's own texts already begin with the word, so it is not said twice.
const said = (word: string, reason: string | null) => {
  const lead = `${word.split(" ")[0]}: `;
  const why = reason ?? "no reason given";
  return `${word}: ${why.startsWith(lead) ? why.slice(lead.length) : why}`;
};
export const NOT_TAKEN = "no worker claimed it within an hour";
export const GETTING_READY_AFTER_MS = 3 * 60_000;

/**
 * The line for a queued question. A running question of the thread has the agent busy, whatever the wait. Otherwise the wait is
 * the browser's clock minus the server's `requested_at`: a wrong browser clock can only swap the first two lines, and both say the question waits.
 */
export const queuedLine = (waitedMs: number, otherRunning: boolean): string =>
  otherRunning
    ? "Waiting: the research agent is answering another question."
    : waitedMs < GETTING_READY_AFTER_MS
      ? "Sent. Waiting for the research agent."
      : "Getting ready: the research agent may not be online yet. Your question stays in line for one hour.";

/** What the answer side of a turn says for a question that has no answer text yet (or none at all). */
export const stateLine = (q: QuestionItem, now: number, otherRunning: boolean): string => {
  switch (q.state) {
    case "queued":
      return queuedLine(now - Date.parse(q.requested_at), otherRunning);
    case "running":
      return q.claimed_at ? `Working since ${clock(q.claimed_at)}.` : "Working.";
    case "done":
      return "Answered.";
    case "refused":
      // The long word is for the agent's own refusals, which all begin "Refused: "; the worker's refusals say plain "Refused".
      return said(q.reason?.startsWith("Refused: ") ? "Refused by the research agent" : "Refused", q.reason);
    case "failed":
      return q.reason === NOT_TAKEN ? "Not answered: no research agent took it within an hour. Ask again later." : said("Failed", q.reason);
    default:
      return q.state; // a state from a newer server is shown as it came
  }
};

interface Source {
  list: () => Promise<QuestionItem[]>;
  ask: (question: string) => Promise<QuestionItem>;
  read: (id: number) => Promise<QuestionDetail>;
  clinicianId: string;
  onSignOut: () => void;
  /** The line for each status; any other status says the ward server cannot be reached. */
  errors: Record<number, string>;
}

/**
 * The state of a list of a clinician's questions, for the patient panel and the Ask page. It reads on mount, after a send and
 * on Refresh, and on a timer only while a question is queued or running. `list`, `ask` and `read` must keep their identity while
 * the source stays the same.
 */
export function useQuestions({ list, ask, read, clinicianId, onSignOut, errors }: Source) {
  const [items, setItems] = useState<QuestionItem[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false); // a failed read stops the timer until a read works
  // The answer of each question is read once; `shown` says which are open.
  const [details, setDetails] = useState<Record<number, QuestionDetail>>({});
  // The browser's clock when the list was last read: the timer reads every 5 s while a question waits, so this is the tick.
  const [now, setNow] = useState(() => Date.now());
  const [sent, setSent] = useState(0); // counts the sends whose list has arrived, so the thread can scroll to the new turn

  const signOut = useRef(onSignOut);
  useEffect(() => {
    signOut.current = onSignOut;
  }, [onSignOut]);
  const fail = useCallback(
    (failure: unknown) => {
      const status = failure instanceof ApiError ? failure.status : 0;
      if (status === 401) signOut.current();
      setError(errors[status] ?? "Cannot reach the ward server.");
    },
    [errors],
  );

  const reading = useRef(false);
  const round = useRef(0); // an answer that comes back late must not replace a newer list
  const load = useCallback(() => {
    reading.current = true;
    const n = ++round.current;
    return list()
      .then((rows) => {
        if (n !== round.current) return;
        setItems(rows);
        setNow(Date.now());
        setStalled(false);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (n !== round.current) return;
        setStalled(true);
        fail(failure);
      })
      .finally(() => {
        reading.current = false;
      });
  }, [list, fail]);
  const refresh = useCallback(() => {
    if (!reading.current) void load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadAnswer = useCallback(
    (id: number) =>
      read(id)
        .then((d) => {
          setDetails((a) => ({ ...a, [id]: d }));
          setError(null);
        })
        .catch(fail),
    [read, fail],
  );

  const pending = items.some(isOpen);
  useEffect(() => {
    if (!pending || stalled) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [pending, stalled, refresh]);

  // The 5 newest answers load by themselves, one read each; an older answer waits for its button.
  const asked = useRef(new Set<number>());
  useEffect(() => {
    for (const q of items.filter((i) => i.state === "done").slice(0, AUTO)) {
      if (asked.current.has(q.id)) continue;
      asked.current.add(q.id);
      loadAnswer(q.id);
    }
  }, [items, loadAnswer]);

  const mine = items.some((q) => q.requested_by === clinicianId && isOpen(q));
  const question = text.trim();

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question || sending || mine) return;
    setSending(true);
    ask(question)
      .then(() => {
        setText("");
        setError(null);
        return load().then(() => setSent((n) => n + 1));
      })
      .catch(fail)
      .finally(() => setSending(false));
  };

  // The API lists the newest first; a thread reads oldest first.
  const turns = [...items].reverse();
  return { items, turns, text, setText, sending, error, details, now, mine, question, send, loadAnswer, refresh, sent };
}
