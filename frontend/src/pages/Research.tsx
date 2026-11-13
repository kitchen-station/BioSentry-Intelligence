import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import type { Session } from "../types";
import { LabFrame } from "../components/LabFrame";
import { LEARNED_RULES, RESEARCH_PROVENANCE as P, RESEARCH_RESULTS, RUN_COMPARISON } from "../data/researchResults";
import { ResearchAgentSection } from "./ResearchAgentSection";
import styles from "./Research.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

const signed = (x: number) => `${x < 0 ? "-" : "+"}${Math.abs(x).toFixed(3)}`;

const Rate = ({ rate, n, d }: { rate: number; n?: number; d?: number }) => (
  <>
    {rate.toFixed(3)}
    {n !== undefined && <> <span className={styles.sub}>({n}/{d})</span></>}
  </>
);

/** What the offline evaluation found. Static: reads no patient data, makes no API call, changes no rank. */
export function Research({ session, onSignOut }: Props) {
  const { hash } = useLocation();
  // React Router does not scroll to a hash by itself.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);
  return (
    <LabFrame session={session} onSignOut={onSignOut}>
      <main className={styles.main}>
        <h1 className={styles.title}>Research numbers</h1>
        <p role="note" className={styles.banner}>
          Synthetic, not validated. These numbers come from a simulator, judged on the same simulator.
          They are not evidence about patients.
        </p>

        <section className={styles.card} aria-labelledby="rules">
          <h2 id="rules" className={styles.heading}>How each alert rule behaved</h2>
          <div className={styles.scroll}>
            <table className={styles.table}>
              <caption>Synthetic simulator, not validated. Alert rules on simulated episodes of deterioration, with the same rule on patients who had none.</caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Alerts per patient per 12-hour shift</th>
                  <th scope="col">Episodes alerting in any bin of the window, including an alert already open (79 not cut short)</th>
                  <th scope="col">Same measure on patients with no episode (&quot;chance&quot;)</th>
                  <th scope="col">Excess over chance</th>
                  <th scope="col">Median lead time, minutes, episodes with a NEW alert only (different measure; negative: after the episode was fully in)</th>
                </tr>
              </thead>
              <tbody>
                {RESEARCH_RESULTS.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{r.name}</th>
                    <td>{r.perShift.toFixed(3)}</td>
                    <td>{r.episodes.rate.toFixed(3)} <span className={styles.sub}>({r.episodes.n}/{r.episodes.d})</span></td>
                    <td>{r.chance.rate.toFixed(3)} <span className={styles.sub}>({r.chance.n}/{r.chance.d})</span></td>
                    <td>{signed(r.excess)}</td>
                    <td>{r.leadMedianMin.toFixed(1)} <span className={styles.sub}>(n={r.leadN} of 91 evaluated)</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className={styles.card} aria-labelledby="learned">
          <h2 id="learned" className={styles.heading}>Learned rules</h2>
          <div className={styles.scroll}>
            <table className={styles.table}>
              <caption>Learned rules. Synthetic simulator, not validated. The learned rules were trained on simulator trials and judged on other trials of the same simulator.</caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Alerts per patient per 12-hour shift</th>
                  <th scope="col">Time in alert</th>
                  <th scope="col">Episodes alerting in any bin of the window, including an alert already open (79 not cut short)</th>
                  <th scope="col">Same measure on patients with no episode (&quot;chance&quot;)</th>
                  <th scope="col">Excess over chance</th>
                  <th scope="col">Median lead time, minutes, episodes with a NEW alert only (negative: after the episode was fully in)</th>
                </tr>
              </thead>
              <tbody>
                {LEARNED_RULES.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{r.name}</th>
                    <td>{r.perShift.toFixed(3)}</td>
                    <td>{r.timeInAlert.toFixed(3)}</td>
                    <td><Rate {...r.episodes} /></td>
                    <td><Rate {...r.chance} /></td>
                    <td>{signed(r.excess)}</td>
                    <td>{r.leadMedianMin.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>None of these learned rules is used by the ward list, the rank or any alert.</p>
          <p>
            The student model without temperature, alone or with NEWS2, has no usable threshold: its alert
            stays open about 80% of the time, so it is not a working rule.
          </p>
        </section>

        <section className={styles.card} aria-labelledby="compare">
          <h2 id="compare" className={styles.heading}>Before and after the simulator&apos;s temperature change</h2>
          <div className={styles.scroll}>
            <table className={styles.table}>
              <caption>Synthetic simulator, not validated. Each rule on ward-v1 and on ward-v2, held-out seeds 11 to 30. Between them the fixed temperature rise was removed and temperature noise went from 0.02 to 0.1 C.</caption>
              <thead>
                <tr>
                  <th scope="col">Rule</th>
                  <th scope="col">Alerts per patient per 12-hour shift, ward-v1</th>
                  <th scope="col">Same, ward-v2</th>
                  <th scope="col">Episodes alerting in any bin, all 91 evaluated, with 95% interval, ward-v1</th>
                  <th scope="col">Same, ward-v2</th>
                  <th scope="col">Excess over chance, 79 episodes not cut short (no interval), ward-v1</th>
                  <th scope="col">Same, ward-v2</th>
                </tr>
              </thead>
              <tbody>
                {RUN_COMPARISON.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">{r.name}</th>
                    <td>{r.v1.perShift.toFixed(3)}</td>
                    <td>{r.v2.perShift.toFixed(3)}</td>
                    <td>{r.v1.anyAll.toFixed(3)} <span className={styles.sub}>({r.v1.lo.toFixed(3)} to {r.v1.hi.toFixed(3)})</span></td>
                    <td>{r.v2.anyAll.toFixed(3)} <span className={styles.sub}>({r.v2.lo.toFixed(3)} to {r.v2.hi.toFixed(3)})</span></td>
                    <td>{signed(r.v1.excess)}</td>
                    <td>{signed(r.v2.excess)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className={styles.card} aria-labelledby="read">
          <h2 id="read" className={styles.heading}>How to read this</h2>
          <ul>
            <li>Alerts are frequent, so a rule often alerts on a patient who has no episode.</li>
            <li>Only the excess over that chance rate can be credited to the rule.</li>
            <li>
              Do not compare excess between rules with very different chance rates: it cannot be
              more than 1 minus the chance rate.
            </li>
            <li>Time in alert: the share of patient time with an alert open.</li>
            <li>Lead time is counted only on episodes where the rule gave a new alert.</li>
          </ul>
        </section>

        <section className={styles.card} aria-labelledby="found">
          <h2 id="found" className={styles.heading}>What we found</h2>
          <p>
            The simulator used to add a fixed temperature rise, a value with no cited source, to every episode.
            That rise has been removed, and temperature noise went from 0.02 to 0.1 C. These numbers come from
            trials made again with that simulator (ward-v2), with the same seeds, the same evaluation and the
            same rules.
          </p>
          <p>
            On ward-v2, no rule here adds anything measurable to NEWS2. Added to NEWS2, the own baseline,
            the student and the reward-learned policy each catch a few more episodes, and every interval overlaps
            NEWS2&apos;s.
          </p>
          <p>
            Alone, the own baseline and the student catch fewer episodes than NEWS2, with far fewer alerts. The
            reward-learned policy alone catches about as many as NEWS2 with about a third of the alerts, but
            later: its median lead time is negative.
          </p>
          <p>
            Their excess over chance is higher than NEWS2&apos;s only alongside much lower chance rates. Excess
            cannot be more than 1 minus the chance rate, so it does not rank these rules.
          </p>
          <p>
            NEWS2 gave a new alert in only 2 of 27 hypotensive episodes (all evaluated episodes, not the
            table&apos;s measure). In 17 of 27, no cuff reading reached NEWS2 inside the window, so it saw no
            new blood pressure.
          </p>
          <p>The gains seen on ward-v1 went away on ward-v2. Two things changed (the temperature rise was removed and temperature noise went from 0.02 to 0.1 C), and this run cannot say which one caused it. The table above shows both runs.</p>
          <p>
            What exists and works: features with no look-ahead, clean splits between training and held-out
            trials, and a student model that exports as ONNX, the form Huawei&apos;s converter reads (conversion
            not yet done). Every model was re-run on the new trials with the same harness, and nothing was tuned
            to rescue a result.
          </p>
        </section>

        <ResearchAgentSection />

        <p className={styles.small}>
          Source: evaluation at commit {P.commit}, {P.date}, simulator commit {P.simCommit}, scenario {P.scenario}. {P.trials}. {P.tuning} Student model numbers: commit {P.studentCommit}. Reward-learned policy numbers: commit {P.rewardCommit}. Earlier run: {P.previous}. NEWS2 itself is never
          changed by any of this.
        </p>
      </main>
    </LabFrame>
  );
}
