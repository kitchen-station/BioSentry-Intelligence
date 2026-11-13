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

const SOURCE = {
  loading: "Reading the ward's tier status.",
  ok: "NEWS2 state from this ward's tier status. Other states from the page's own record.",
  failed: "Ward read failed: every state is from the page's own record.",
};

/** The eight stages and what each one does today. Only the NEWS2 state is read from the ward. */
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
    <LabFrame session={session} onSignOut={onSignOut}>
      <main className={research.main}>
        <h1 className={research.title}>The pipeline</h1>
        <p role="note" className={research.banner}>Synthetic, not validated.</p>
        <p className={research.small}>
          {tiers === "loading" ? SOURCE.loading : tiers === "failed" ? SOURCE.failed : SOURCE.ok}
        </p>
        <ol role="list" className={styles.stages}>
          {PIPELINE.map((s, i) => (
            <li key={s.id} className={`${research.card} ${s.decides ? styles.decides : ""}`}>
              <h2 className={research.heading}>
                <span aria-hidden="true" className={styles.num}>{i + 1}</span>
                {s.name}
              </h2>
              <span className={styles.state}>{stateOf(s.tier, s.state)}</span>
              {s.text.map((t) => <p key={t}>{t}</p>)}
            </li>
          ))}
        </ol>
      </main>
    </LabFrame>
  );
}
