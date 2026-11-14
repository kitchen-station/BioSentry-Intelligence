import { useId } from "react";
import { formatClock } from "./format";
import styles from "./ResearchModels.module.css";

const NOTE = "Synthetic, not validated. A flag adds a reason line. It never changes the NEWS2 score or the order. No flag is not an all-clear: NEWS2 alone sets the next step.";
const NOT_SURE = "The model's first check was met, but its confidence check could not confirm a flag. This is not an all-clear.";
const WORDS: Record<string, string> = { flag: "Flagged", no_flag: "No flag", not_sure: "Not sure" };
const REASONS: Record<string, string> = {
  "baseline forming": "the patient's own baseline is still forming",
  "latest bin not scored": "the last 15-minute set gave no NEWS2 score",
  "earlier bin not stored": "an earlier 15-minute set cannot be rebuilt",
  "a vital is not counted": "the patch is not streaming, or a value was typed",
  "stay past 672 bins": "the stay is longer than 7 days",
  "no stored bins": "no 15-minute set is stored yet",
  "vital series not on one grid": "the stored sets do not line up",
  "impossible value in a lag or baseline bin": "a stored value is not possible",
  "impossible value in a baseline bin": "a stored value is not possible",
  "feature out of range": "an input is out of range",
};
// Reasons that mean the model is not running at all: the panel says "getting ready", not the reason.
// An odd or malformed entry reads the same. An unknown text reason is a model that ran and has no result.
const NOT_RUNNING = new Set(["student not loaded", "calibration not loaded", "policy not loaded", "model error"]);
export const ENTRIES = [
  ["prediction", "Prediction model"],
  ["alert_policy", "Alert policy"],
] as const;

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null;
const ms = (x: unknown) => (typeof x === "string" && Number.isFinite(Date.parse(x)) ? Date.parse(x) : null);
const text = (x: unknown) => (typeof x === "string" && x !== "" ? x : null);

// The one sentence for a model: what the patient page and the ward table both show.
// The model runs and has no result for this patient now: the reason from the map, or a plain line
// for a reason the page does not know. Not loaded, an error and an odd entry mean it is not running.
export function modelWords(s: unknown): string {
  const state = isObj(s) ? s.state : null;
  if (typeof state === "string" && Object.hasOwn(WORDS, state)) return WORDS[state];
  const reason = state === "not_computed" && isObj(s) ? text(s.reason) : null;
  if (reason === null || NOT_RUNNING.has(reason)) return "getting ready";
  return Object.hasOwn(REASONS, reason) ? `No result: ${REASONS[reason]}` : "No result for this patient now.";
}

function Entry({ name, s, checked }: { name: string; s: unknown; checked: boolean }) {
  const state = isObj(s) ? s.state : null;
  const body = modelWords(s);
  const computed = isObj(s) && typeof state === "string" && Object.hasOwn(WORDS, state);
  const from = computed ? ms(s.bin_start) : null;
  const to = computed ? ms(s.bin_end) : null;
  return (
    <li className={styles.item}>
      <span className={styles.name}>{name}</span> · {body}
      {state === "not_sure" && checked && <span className={styles.sub}>{NOT_SURE}</span>}
      {from !== null && to !== null && (
        <span className={styles.sub}>From the 15 minutes {formatClock(from)} to {formatClock(to)}.</span>
      )}
    </li>
  );
}

const running = (s: unknown) => {
  if (!isObj(s)) return false;
  if (typeof s.state === "string" && Object.hasOwn(WORDS, s.state)) return true;
  const reason = s.state === "not_computed" ? text(s.reason) : null;
  return reason !== null && !NOT_RUNNING.has(reason);
};

export function ResearchModels({ models }: { models: unknown }) {
  const heading = useId();
  if (models === undefined) return null; // an older server sends no models
  const m = isObj(models) ? models : {};
  const none = !ENTRIES.some(([key]) => running(m[key]));
  return (
    <section aria-labelledby={heading} className={styles.panel}>
      <h2 id={heading} className={styles.heading}>Prediction models</h2>
      {none ? (
        <p className={styles.label}>Prediction models: getting ready.</p>
      ) : (
        <>
          <p className={styles.label}>{NOTE}</p>
          <ul role="list" className={styles.list}>
            {ENTRIES.map(([key, name]) => (
              <Entry key={key} name={name} s={m[key]} checked={key === "prediction"} /* only this model has the confidence check */ />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
