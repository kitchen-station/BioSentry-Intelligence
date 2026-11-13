import { useCallback, useEffect, useRef, useState } from "react";
import type { QuestionDetail, QuestionItem } from "../types";
import { ApiError } from "../api";
import { formatClock } from "./format";

// The same pace as the lab run list.
const POLL_MS = 5_000;
export const MAX = 500;

export const clock = (iso: string) => formatClock(Date.parse(iso));
const isOpen = (q: QuestionItem) => q.state === "queued" || q.state === "running";
// The worker's own texts already begin with the word, so it is not said twice.
const said = (word: string, reason: string | null) => {
  const lead = `${word.split(" ")[0]}: `;
  const why = reason ?? "no reason given";
  return `${word}: ${why.startsWith(lead) ? why.slice(lead.length) : why}`;
};
export const stateText = (q: QuestionItem, refused = "Refused"): string =>
  ({
    queued: "Waiting",
    running: q.claimed_at ? `Working since ${clock(q.claimed_at)}` : "Working",
    done: "Answered",
    refused: said(refused, q.reason),
    failed: said("Failed", q.reason),
  })[q.state] ?? q.state; // a state from a newer server is shown as it came

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
 * The state of a list of a clinician's questions, for the patient panel and the lab's Ask page. It reads on mount, after a send and
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
  const [shown, setShown] = useState<number[]>([]);

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
        setStalled(false);
        setError(null);
      })
      .catch((failure: unknown) => {
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

  const pending = items.some(isOpen);
  useEffect(() => {
    if (!pending || stalled) return;
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [pending, stalled, refresh]);

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
        return load();
      })
      .catch(fail)
      .finally(() => setSending(false));
  };

  const toggle = (id: number) => {
    if (shown.includes(id)) return setShown(shown.filter((s) => s !== id));
    if (id in details) return setShown([...shown, id]);
    read(id)
      .then((d) => {
        setDetails((a) => ({ ...a, [id]: d }));
        setShown((s) => [...s, id]);
        setError(null);
      })
      .catch(fail);
  };

  return { items, text, setText, sending, error, details, shown, mine, question, send, toggle, refresh };
}
