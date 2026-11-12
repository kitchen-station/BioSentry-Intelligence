import { Link } from "react-router-dom";
import type { Session } from "../types";
import { SessionMenu } from "../components/SessionMenu";
import { LEARNED_RULES, RESEARCH_PROVENANCE as P, RESEARCH_RESULTS } from "../data/researchResults";
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
  return (
    <div className={styles.page}>
      <div className={styles.bar}>
        <Link to="/ward">BioSentry &middot; Ward {session.ward}</Link>
        <SessionMenu session={session} onSignOut={onSignOut} />
      </div>
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
                    <td>{r.leadMedianMin.toFixed(1)} <span className={styles.sub}>(n={r.leadN})</span></td>
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
            stays open about half the time, so it is not a working rule.
          </p>
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
            Much of the own-baseline rule&apos;s gain depends on a fixed temperature rise the simulator
            adds to every episode, a value with no cited source.
          </p>
          <p>
            Without temperature, &quot;NEWS2 or own baseline&quot; has less excess (+0.213) than NEWS2 alone
            (+0.237). So here, adding the baseline to NEWS2 gives no excess over NEWS2 alone (the excess has no
            interval).
          </p>
          <p>
            NEWS2 gave a new alert in only 3 of 27 hypotensive episodes (all evaluated episodes, not the
            table&apos;s measure). In 17 of 27, no cuff reading reached NEWS2 inside the window, so it saw no
            new blood pressure.
          </p>
          <p>
            With temperature, the small student model has more excess over chance than NEWS2 alone (+0.539
            against +0.237). The simple own-baseline rule does better than the student; the student is ahead
            only on lead time.
          </p>
          <p>
            The reward-learned policy did not beat the untrained own-baseline rule. Its final numbers are within
            noise of that rule. Generation 0 is the own-baseline rule rewritten as a weighted sum, the starting
            point of learning; it alerts more than twice as often, so most of the later fall in alerts undoes
            that translation. The path between generations is mostly noise.
          </p>
          <p>Without temperature, no learned rule adds anything measurable to NEWS2 on this simulator.</p>
          <p>
            Every gain so far depends on a fixed temperature rise the simulator adds to every episode, a value
            with no cited source.
          </p>
          <p>
            What exists and works: features with no look-ahead, clean splits between training and held-out
            trials, and a student model that exports as ONNX, the form Huawei&apos;s converter reads (conversion
            not yet done). The no-temperature runs show how much of each gain depends on the temperature
            artefact.
          </p>
        </section>

        <p className={styles.small}>
          Source: evaluation at commit {P.commit}, {P.date}, simulator commit {P.simCommit}, scenario {P.scenario}. {P.trials}. {P.tuning} Student model numbers: commit {P.studentCommit}. Reward-learned policy numbers: commit {P.rewardCommit}. NEWS2 itself is never
          changed by any of this.
        </p>
      </main>
    </div>
  );
}
