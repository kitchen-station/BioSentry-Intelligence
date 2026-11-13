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
  "student not loaded": "the model file is not loaded",
  "calibration not loaded": "the model file is not loaded",
  "policy not loaded": "the model file is not loaded",
  "model error": "the model could not run",
};
const ENTRIES = [
  ["prediction", "Prediction model"],
  ["alert_policy", "Alert policy (learned by reward)"],
] as const;

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null;
const ms = (x: unknown) => (typeof x === "string" && Number.isFinite(Date.parse(x)) ? Date.parse(x) : null);
const text = (x: unknown) => (typeof x === "string" && x !== "" ? x : null);

function ModelRecord({ provenance }: { provenance: unknown }) {
  const items = Array.isArray(provenance) ? provenance.filter(isObj) : [];
  if (items.length === 0) return null;
  return (
    <details className={styles.record}>
      <summary>Model record</summary>
      {items.map((p, i) => {
        const sha = text(p.file_sha256);
        const lines = [
          ["Model", text(p.model_id)],
          ["Variant", text(p.variant)],
          ["File", sha && sha.slice(0, 12)],
          ["Run", text(p.run_id)],
        ].filter(([, v]) => v !== null);
        if (lines.length === 0) return null; // an item with nothing to name
        return (
          <p key={i} className={styles.sub}>
            {lines.map(([k, v]) => `${k}: ${v}`).join(" · ")}
          </p>
        );
      })}
    </details>
  );
}

function Entry({ name, s, checked }: { name: string; s: unknown; checked: boolean }) {
  const state = isObj(s) ? s.state : null;
  let body: string | null = null;
  if (typeof state === "string" && Object.hasOwn(WORDS, state)) body = WORDS[state];
  else if (state === "not_computed" && isObj(s)) {
    const reason = text(s.reason);
    if (reason !== null) body = `Not computed: ${Object.hasOwn(REASONS, reason) ? REASONS[reason] : reason}`;
  }
  const computed = isObj(s) && typeof state === "string" && Object.hasOwn(WORDS, state);
  const from = computed ? ms(s.bin_start) : null;
  const to = computed ? ms(s.bin_end) : null;
  return (
    <li className={styles.item}>
      <span className={styles.name}>{name}</span> · {body ?? "Not available."}
      {state === "not_sure" && checked && <span className={styles.sub}>{NOT_SURE}</span>}
      {from !== null && to !== null && (
        <span className={styles.sub}>From the 15 minutes {formatClock(from)} to {formatClock(to)}.</span>
      )}
      {computed && <ModelRecord provenance={s.provenance} />}
    </li>
  );
}

export function ResearchModels({ models }: { models: unknown }) {
  const heading = useId();
  if (models === undefined) return null; // an older server sends no models
  const m = isObj(models) ? models : {};
  return (
    <section aria-labelledby={heading} className={styles.panel}>
      <h2 id={heading} className={styles.heading}>Research models</h2>
      <p className={styles.label}>{NOTE}</p>
      <ul role="list" className={styles.list}>
        {ENTRIES.map(([key, name]) => (
          <Entry key={key} name={name} s={m[key]} checked={key === "prediction"} /* only this model has the confidence check */ />
        ))}
      </ul>
    </section>
  );
}
