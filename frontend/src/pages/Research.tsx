import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import type { Session } from "../types";
import { LabFrame } from "../components/LabFrame";
import { CONFIDENCE_CHECK as C, LEARNED_RULES, MODEL_FACTS as M, RESEARCH_PROVENANCE as P, RESEARCH_RESULTS, RUN_COMPARISON } from "../data/researchResults";
import { ResearchAgentSection } from "./ResearchAgentSection";
import styles from "./Research.module.css";

interface Props {
  session: Session;
  onSignOut: () => void;
}

const num = (x: number) => x.toLocaleString("en-US");
const rule = (id: string) => LEARNED_RULES.find((r) => r.id === id)!;
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
        </section>

        <section className={styles.card} aria-labelledby="check">
          <h2 id="check" className={styles.heading}>Confidence check on the student</h2>
          <p>
            The check marks a student alert &quot;not sure&quot; when the student&apos;s score is too low to
            support it. A &quot;not sure&quot; bin never alerts. The target was 1 false alert in {C.targetOneIn} checks
            on patients who were not starting to deteriorate, on average over stays. On the held-out trials,
            inside the simulator, the target was met ({C.verdict}): the achieved share was {C.achievedPct.toFixed(2)}%
            (95% interval {C.achievedLoPct.toFixed(2)}% to {C.achievedHiPct.toFixed(2)}%).
          </p>
          <p>
            What it removes: {num(C.notSureBins)} of the student&apos;s {num(C.studentAlertingBins)} alerting bins
            ({C.notSureShare.toFixed(3)}) became not sure, leaving {num(C.checkedAlertingBins)}. New alerts fell from {num(C.studentNewAlerts)} to {num(C.checkedNewAlerts)}.
            What it costs: {C.lostAny} of {C.evaluated} episodes the student caught in some bin are no longer
            caught in any bin, and {C.lostEarly} of the episodes the student caught early are not caught early.
          </p>
          <p>
            The target bounds false alerts among checks of patients who were not starting to deteriorate, on
            average over stays. It does not bound the share of alerts that are true, and most alerts can still be
            false. It is a result inside the simulator, not a clinical guarantee.
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
          <p>The student and the reward-learned policy were trained again after this change, so they are not compared across the two simulators here.</p>
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
            The prediction model is a small MindSpore network (one hidden layer, {num(M.studentParameters)} parameters)
            fitted to the outputs of a larger one ({num(M.teacherParameters)} parameters). On validation its log loss
            ({M.studentValLogLoss}) is not better than the same small network fitted on the true labels
            ({M.hardLabelValLogLoss}), so the larger network bought nothing measurable.
          </p>
          <p>
            On held-out trials the student alerts about as often as NEWS2 ({rule("student").perShift.toFixed(3)} against {rule("ref_news2_chart_trigger").perShift.toFixed(3)} alerts
            per patient per 12 hours). It alerts in {rule("student").episodes.n} of {rule("student").episodes.d} episode windows
            against NEWS2&apos;s {rule("ref_news2_chart_trigger").episodes.n} of {rule("ref_news2_chart_trigger").episodes.d}, and the intervals overlap. Its chance rate is lower
            ({rule("student").chance.rate.toFixed(3)} against {rule("ref_news2_chart_trigger").chance.rate.toFixed(3)}) and so is its time in alert
            ({rule("student").timeInAlert.toFixed(3)} against {rule("ref_news2_chart_trigger").timeInAlert.toFixed(3)}). Added to NEWS2 the gain is not beyond noise.
          </p>
          <p>
            The reward-learned policy was searched with {M.rewardCandidates} candidates per generation. A winner was kept
            only if it also beat the current policy on a second batch of new trials. {M.rewardAcceptedSteps} step was accepted,
            and the outer loop stopped after {M.rewardRounds} rounds. Added to NEWS2 it gives no gain beyond noise. Alone it is a
            quiet rule ({rule("reward_final").perShift.toFixed(3)} alerts per patient per 12 hours), with a median lead time that is
            negative ({rule("reward_final").leadMedianMin.toFixed(1)} minutes). The own baseline alone also catches fewer
            episodes than NEWS2, with far fewer alerts.
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
          <p>The gains the own baseline showed on ward-v1 went away on ward-v2. Two things changed (the temperature rise was removed and temperature noise went from 0.02 to 0.1 C), and this run cannot say which one caused it. The table above shows both runs.</p>
          <p>
            What exists and works: features with no look-ahead, clean splits between training and held-out
            trials, and a student model trained with MindSpore that exports as ONNX. {P.heldOutRead} Nothing was
            tuned on the held-out trials.
          </p>
        </section>

        <ResearchAgentSection />

        <p className={styles.small}>
          Source: evaluation on {P.date}, simulator commit {P.simCommit}, scenario {P.scenario}. {P.trials}. {P.tuning} {P.heldOutRead} Student model (run {P.studentRun}): {P.studentData}. Reward-learned policy (run {P.rewardRun}): {P.rewardData}. Confidence check (run {P.checkRun}): {P.checkData}. Earlier run: {P.previous}. NEWS2 itself is never
          changed by any of this.
        </p>
      </main>
    </LabFrame>
  );
}
