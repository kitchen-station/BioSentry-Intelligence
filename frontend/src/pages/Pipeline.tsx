import { useEffect, useState } from "react";
import { getWard } from "../api";
import type { Session } from "../types";
import { LabFrame } from "../components/LabFrame";
import { PIPELINE, type StageState } from "../data/pipeline";
import research from "./Research.module.css";
import styles from "./Pipeline.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

type Tiers = readonly { tier: string; live: boolean }[] | "loading" | "failed";

// Live in the ranking reads as done, research only as information; the rest stays grey.
const TAG: Record<StageState, string> = {
  "live in the ranking": "hw-tag hw-tag--success",
  "shown, does not change the ranking": "hw-tag",
  "research only, offline": "hw-tag hw-tag--info",
  planned: "hw-tag",
};

const SOURCE = {
  loading: "Reading the ward's tier status.",
  ok: "NEWS2 state from this ward's tier status. Other states from the page's own record.",
  failed: "Ward read failed: every state is from the page's own record.",
};

/** The eight stages as a vertical stepper, each with what it does today. Only the NEWS2 state is read from the ward. */
export function Pipeline({ session, onSignOut }: Props) {
  const [tiers, setTiers] = useState<Tiers>("loading");

  useEffect(() => {
    let cancelled = false;
    getWard().then(
      (w) => !cancelled && setTiers(Array.isArray(w.tier_status) ? w.tier_status : "failed"),
      () => !cancelled && setTiers("failed"),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const stateOf = (tier: string | undefined, own: StageState): StageState => {
    const found = Array.isArray(tiers) ? tiers.find((t) => t.tier === tier) : undefined;
    if (!found) return own;
    return found.live ? "live in the ranking" : "shown, does not change the ranking";
  };

  return (
    <LabFrame session={session} onSignOut={onSignOut} title="The pipeline">
      <div className={research.main}>
        {/* "Synthetic, not validated." is the research strip's, above. */}
        <h1 className="hw-heading hw-heading--3">The pipeline</h1>
        <p className="hw-caption">
          {tiers === "loading" ? SOURCE.loading : tiers === "failed" ? SOURCE.failed : SOURCE.ok}
        </p>
        <ol role="list" className={styles.stages}>
          {PIPELINE.map((s, i) => {
            const state = stateOf(s.tier, s.state);
            return (
              <li key={s.id} className={styles.stage} data-state={state} data-decides={s.decides}>
                <div className={`hw-card hw-card--outlined ${research.card} ${styles.card}`}>
                  <div className={styles.head}>
                    <h2 className="hw-heading hw-heading--4">
                      <span aria-hidden="true" className={styles.num}>{i + 1}</span>
                      {s.name}
                    </h2>
                    <span className={TAG[state]}>{state}</span>
                  </div>
                  {s.text.map((t) => <p key={t}>{t}</p>)}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </LabFrame>
  );
}
