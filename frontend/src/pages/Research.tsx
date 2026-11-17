import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { bars, type BarsConfig } from "hw-ui";
import type { Session } from "../types";
import { LabFrame } from "../components/LabFrame";
import { CONFIDENCE_CHECK as C, HELD_OUT_2 as H2, HELD_OUT_2_ALONE as A, LEARNED_RULES, MODEL_FACTS as M, RESEARCH_PROVENANCE as P, RESEARCH_RESULTS, RUN_COMPARISON } from "../data/researchResults";
import { ResearchAgentSection } from "./ResearchAgentSection";
import styles from "./Research.module.css";

interface Props {
  /** null: the sample build's evidence, read before sign-in. */
  session: Session | null;
  onSignOut: () => void;
}

const num = (x: number) => x.toLocaleString("en-US");
const times = (id: string) => (rule(id).perShift / rule("ref_news2_chart_trigger").perShift).toFixed(2);
const added = (n: number) => n - M.anyAllCounts.news2;
const rule = (id: string) => LEARNED_RULES.find((r) => r.id === id)!;
const signed = (x: number) => `${x < 0 ? "-" : "+"}${Math.abs(x).toFixed(3)}`;
const signed4 = (x: number) => `${x < 0 ? "-" : "+"}${Math.abs(x).toFixed(4)}`;
const pct = (x: number) => Number((x * 100).toFixed(1)); // a share as printed (3 decimals), in percent

const Rate = ({ rate, n, d }: { rate: number; n?: number; d?: number }) => (
  <>
    {rate.toFixed(3)}
    {n !== undefined && <> <span className={styles.sub}>({n}/{d})</span></>}
  </>
);

// The findings, drawn by the kit's bars graph. Every number comes from the data file, which names its source.
const row2 = (id: string) => H2.rows.find((r) => r.id === id)!;
const verdicts = new Map<string, string>(H2.rows.map((r) => [`NEWS2 or ${r.name}`, r.verdict]));
export const HEADLINE: BarsConfig = {
  title: "Added to NEWS2, only the student with the confidence check showed a gain beyond chance",
  subtitle: `Gain in excess over chance against NEWS2 alone, held-out set 2. The unit is the trial; the interval is ${H2.interval}.`,
  level: 2,
  diverging: true,
  zeroLabel: "0 · NEWS2 alone",
  sides: ["a loss", "a gain"],
  intervalLabel: `${H2.interval} interval`,
  format: signed,
  items: ["news2_or_student_checked", "news2_or_student", "news2_or_reward_final"].map((id) => {
    const r = row2(id);
    return { name: `NEWS2 or ${r.name}`, value: r.gain, interval: [r.lo, r.hi] };
  }),
  highlight: 0,
  label: (item) => `${item.name}: ${verdicts.get(item.name)}`,
};

const { student: S, news2: N } = A;
const alone = (title: string, subtitle: string, student: number, news2: number, format: (v: number) => string, unit?: string): BarsConfig => ({
  title, subtitle, level: 3, format, unit, highlight: 0, baseline: 1,
  items: [{ name: "Student model", value: student }, { name: "NEWS2", value: news2 }],
});
// One decimal, as the source prints them; a round scale mark drops its ".0" so the marks stay short on a phone.
const one = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
const minutes = (v: number) => `${one(v)} min`;
export const ALONE: readonly BarsConfig[] = [
  alone("It gave a new alert in more deteriorations", `Episodes with a new alert in the window, of ${num(N.newAlert.d)} evaluated (${num(S.newAlert.n)} against ${num(N.newAlert.n)})`,
    pct(S.newAlert.rate), pct(N.newAlert.rate), (v) => `${one(v)}%`, "%"),
  alone("It spent less than half as long in alert", "Time in alert: the share of patient time with an alert open. Less is a quieter ward.",
    pct(S.timeInAlert), pct(N.timeInAlert), (v) => `${one(v)}%`, "%"),
  alone(`It warned about ${Math.round(S.leadMedianMin - N.leadMedianMin)} minutes earlier`, "Median lead time, minutes, episodes with a new alert only. Longer is earlier warning.",
    S.leadMedianMin, N.leadMedianMin, minutes),
  alone("It needed fewer alerts for each deterioration it caught", "Alerts per episode with a new alert. Fewer is less noise.",
    S.alertsPerCaught, N.alertsPerCaught, one),
];

/** One kit bars graph. The config is a module constant: drawn once, removed with the page. */
function Bars({ config }: { config: BarsConfig }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const g = bars(root.current!, config);
    return () => g.destroy();
  }, [config]);
  return <div ref={root} />;
}

/** What the offline evaluation found. Static: reads no patient data, makes no API call, changes no rank. */
export function Research({ session, onSignOut }: Props) {
  const { hash } = useLocation();
  // React Router does not scroll to a hash by itself.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);
  return (
    <LabFrame session={session} onSignOut={onSignOut} title="Research numbers">
      <div className={styles.main}>
        <div>
          <h1 className="hw-heading hw-heading--3">Research numbers</h1>
          <p className="hw-text-muted">
            Held-out set 2: {H2.trials} trials, {num(A.patients)} patients, {num(H2.episodes)} deterioration episodes. Read once, with its analysis written before the read: the result to quote.
          </p>
        </div>
        <p role="note" className="hw-alert">
          <span className="hw-alert__icon hw-icon fa-solid fa-circle-info" aria-hidden="true" />
          <span className="hw-alert__body">
            Synthetic, not validated. These numbers come from a simulator, judged on the same simulator.
            They are not evidence about patients.
          </span>
        </p>

        <section className={`hw-card ${styles.card} ${styles.headline}`} aria-label="The result to quote">
          <Bars config={HEADLINE} />
          <p className={styles.lede}>
            The line across each bar is its {H2.interval} interval (three comparisons). Only the interval of the student with the confidence check stays right of zero: its gain
            is small and is bought with a few more alerts ({row2("news2_or_student_checked").alertRatio.toFixed(3)} times NEWS2&apos;s). The student without the check and the reward
            policy show a loss; the reward policy&apos;s upper end is at the edge of zero ({signed4(row2("news2_or_reward_final").hi)}). On the ward, NEWS2 alone sets the order.
          </p>
        </section>

        <section aria-labelledby="alone" className={styles.alone}>
          <div>
            <h2 id="alone" className="hw-heading hw-heading--4">What the student model does on its own</h2>
            <p className="hw-text-muted">
              The student, on its own, alerts about as often as NEWS2, in more deteriorations, and for much less time. That is not the test that
              was written before the read: added to NEWS2 it shows a loss ({signed(row2("news2_or_student").gain)}). A flag never moves a patient on the list.
            </p>
          </div>
          <div className={styles.findings}>
            {ALONE.map((c) => (
              <div key={c.title} className={`hw-card ${styles.card}`}><Bars config={c} /></div>
            ))}
          </div>
        </section>

        <p className="hw-caption">
          Source: held-out set 2, ward-v2 seeds 1001 to 1400, read once on {H2.date}. The gains and their {H2.interval} intervals: {H2.source}. The rules
          alone: {A.source}. Every row was re-scored with the backend&apos;s NEWS2 with 0 mismatches.
        </p>

        <details className={`hw-card hw-accordion__item ${styles.tables}`}>
          <summary className="hw-accordion__summary">Full tables</summary>
          <div className={`hw-accordion__body ${styles.inner}`}>
            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="set2">
              <h2 id="set2" className="hw-heading hw-heading--4">Held-out set 2: the student and NEWS2, each alone</h2>
              <div className="hw-table-wrap">
                <table className="hw-table">
                  <caption className={`hw-caption ${styles.caption}`}>Held-out set 2, {num(H2.episodes)} episodes evaluated. Synthetic simulator, not validated.</caption>
                  <thead>
                    <tr><th scope="col">Measure</th><th scope="col" className="hw-table__num">Student model</th><th scope="col" className="hw-table__num">NEWS2 chart trigger</th></tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th scope="row">Episodes with a new alert in the window</th>
                      {[S, N].map((r, i) => <td key={i} className="hw-table__num"><Rate {...r.newAlert} /></td>)}
                    </tr>
                    <tr>
                      <th scope="row">Time in alert</th>
                      {[S, N].map((r, i) => <td key={i} className="hw-table__num">{r.timeInAlert.toFixed(3)}</td>)}
                    </tr>
                    <tr>
                      <th scope="row">Alerts per episode with a new alert</th>
                      {[S, N].map((r, i) => <td key={i} className="hw-table__num">{r.alertsPerCaught.toFixed(1)}</td>)}
                    </tr>
                    <tr>
                      <th scope="row">Median lead time, minutes, episodes with a new alert</th>
                      {[S, N].map((r, i) => <td key={i} className="hw-table__num">{r.leadMedianMin.toFixed(1)}</td>)}
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
            <p className="hw-text-muted">
              The tables and notes below are from the first, small held-out set (seeds 11 to 30, 91 episodes), as this page showed it before set 2 was read:
              that set was too small to tell these rules apart.
            </p>
            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="rules">
              <h2 id="rules" className="hw-heading hw-heading--4">How each alert rule behaved</h2>
              <div className={`hw-table-wrap ${styles.wrap}`}>
                <table className={`hw-table ${styles.table}`}>
                  <caption className={`hw-caption ${styles.caption}`}><span>Synthetic simulator, not validated. Alert rules on simulated episodes of deterioration, with the same rule on patients who had none.</span></caption>
                  <thead>
                    <tr>
                      <th scope="col">Rule</th>
                      <th scope="col" className="hw-table__num">Alerts per patient per 12-hour shift</th>
                      <th scope="col" className="hw-table__num">Episodes alerting in any bin of the window, including an alert already open (79 not cut short)</th>
                      <th scope="col" className="hw-table__num">Same measure on patients with no episode (&quot;chance&quot;)</th>
                      <th scope="col" className="hw-table__num">Excess over chance</th>
                      <th scope="col" className="hw-table__num">Median lead time, minutes, episodes with a NEW alert only (different measure; negative: after the episode was fully in)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {RESEARCH_RESULTS.map((r) => (
                      <tr key={r.id}>
                        <th scope="row">{r.name}</th>
                        <td className="hw-table__num">{r.perShift.toFixed(3)}</td>
                        <td className="hw-table__num">{r.episodes.rate.toFixed(3)} <span className={styles.sub}>({r.episodes.n}/{r.episodes.d})</span></td>
                        <td className="hw-table__num">{r.chance.rate.toFixed(3)} <span className={styles.sub}>({r.chance.n}/{r.chance.d})</span></td>
                        <td className="hw-table__num">{signed(r.excess)}</td>
                        <td className="hw-table__num">{r.leadMedianMin.toFixed(1)} <span className={styles.sub}>(n={r.leadN} of 91 evaluated)</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="learned">
              <h2 id="learned" className="hw-heading hw-heading--4">Learned rules</h2>
              <div className={`hw-table-wrap ${styles.wrap}`}>
                <table className={`hw-table ${styles.table}`}>
                  <caption className={`hw-caption ${styles.caption}`}><span>Learned rules. Synthetic simulator, not validated. The learned rules were trained on simulator trials and judged on other trials of the same simulator.</span></caption>
                  <thead>
                    <tr>
                      <th scope="col">Rule</th>
                      <th scope="col" className="hw-table__num">Alerts per patient per 12-hour shift</th>
                      <th scope="col" className="hw-table__num">Time in alert</th>
                      <th scope="col" className="hw-table__num">Episodes alerting in any bin of the window, including an alert already open (79 not cut short)</th>
                      <th scope="col" className="hw-table__num">Same measure on patients with no episode (&quot;chance&quot;)</th>
                      <th scope="col" className="hw-table__num">Excess over chance</th>
                      <th scope="col" className="hw-table__num">Median lead time, minutes, episodes with a NEW alert only (negative: after the episode was fully in)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {LEARNED_RULES.map((r) => (
                      <tr key={r.id}>
                        <th scope="row">{r.name}</th>
                        <td className="hw-table__num">{r.perShift.toFixed(3)}</td>
                        <td className="hw-table__num">{r.timeInAlert.toFixed(3)}</td>
                        <td className="hw-table__num"><Rate {...r.episodes} /></td>
                        <td className="hw-table__num"><Rate {...r.chance} /></td>
                        <td className="hw-table__num">{signed(r.excess)}</td>
                        <td className="hw-table__num">{r.leadMedianMin.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>The API loads the student with its confidence check: only an alert it supports adds a flag to a ward row as one more reason. A flag never moves a patient on the list. The reward policy is not connected: on the second held-out set it showed a loss.</p>
            </section>

            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="check">
              <h2 id="check" className="hw-heading hw-heading--4">Confidence check on the student</h2>
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
                What it costs: {C.lostAny} of the {C.studentAnyAll} episodes the student was alerting in are no longer
                alerting in any bin, and {C.lostEarly} episodes with a new alert before fully in lose it.
              </p>
              <p>
                The target bounds false alerts among checks of patients who were not starting to deteriorate, on
                average over stays. It does not bound the share of alerts that are true, and most alerts can still be
                false. It is a statement about simulator trials only. It promises nothing about patients.
              </p>
            </section>

            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="compare">
              <h2 id="compare" className="hw-heading hw-heading--4">Before and after the simulator&apos;s temperature change</h2>
              <div className={`hw-table-wrap ${styles.wrap}`}>
                <table className={`hw-table ${styles.table}`}>
                  <caption className={`hw-caption ${styles.caption}`}><span>Synthetic simulator, not validated. Each rule on ward-v1 and on ward-v2, held-out seeds 11 to 30. Between them the fixed temperature rise was removed and temperature noise went from 0.02 to 0.1 C.</span></caption>
                  <thead>
                    <tr>
                      <th scope="col">Rule</th>
                      <th scope="col" className="hw-table__num">Alerts per patient per 12-hour shift, ward-v1</th>
                      <th scope="col" className="hw-table__num">Same, ward-v2</th>
                      <th scope="col" className="hw-table__num">Episodes alerting in any bin, all 91 evaluated, with 95% interval, ward-v1</th>
                      <th scope="col" className="hw-table__num">Same, ward-v2</th>
                      <th scope="col" className="hw-table__num">Excess over chance, 79 episodes not cut short (no interval), ward-v1</th>
                      <th scope="col" className="hw-table__num">Same, ward-v2</th>
                    </tr>
                  </thead>
                  <tbody>
                    {RUN_COMPARISON.map((r) => (
                      <tr key={r.id}>
                        <th scope="row">{r.name}</th>
                        <td className="hw-table__num">{r.v1.perShift.toFixed(3)}</td>
                        <td className="hw-table__num">{r.v2.perShift.toFixed(3)}</td>
                        <td className="hw-table__num">{r.v1.anyAll.toFixed(3)} <span className={styles.sub}>({r.v1.lo.toFixed(3)} to {r.v1.hi.toFixed(3)})</span></td>
                        <td className="hw-table__num">{r.v2.anyAll.toFixed(3)} <span className={styles.sub}>({r.v2.lo.toFixed(3)} to {r.v2.hi.toFixed(3)})</span></td>
                        <td className="hw-table__num">{signed(r.v1.excess)}</td>
                        <td className="hw-table__num">{signed(r.v2.excess)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>The student and the reward-learned policy were trained again after this change, so they are not compared across the two simulators here.</p>
            </section>

            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="read">
              <h2 id="read" className="hw-heading hw-heading--4">How to read this</h2>
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

            <section className={`hw-card hw-card--outlined ${styles.card}`} aria-labelledby="found">
              <h2 id="found" className="hw-heading hw-heading--4">What we found</h2>
              <p>
                The simulator used to add a fixed temperature rise, a value with no cited source, to every episode.
                That rise has been removed, and temperature noise went from 0.02 to 0.1 C. These numbers come from
                trials made again with that simulator (ward-v2), with the same seeds, the same evaluation and the
                same rules.
              </p>
              <p>
                Added to NEWS2, the student alerts in {added(M.anyAllCounts.news2OrStudent)} more of {C.evaluated} episode windows at {times("news2_or_student")} times
                NEWS2&apos;s alerts. Most of that is what the extra alerts give by chance: the excess over chance is {signed(rule("news2_or_student").excess)} against {signed(rule("ref_news2_chart_trigger").excess)} for
                NEWS2 alone, and excess has no interval. On this first held-out set (91 episodes) no gain beyond chance is shown. With the confidence check it is {added(M.anyAllCounts.news2OrStudentChecked)} more
                of {C.evaluated} at {times("news2_or_student_checked")} times the alerts, excess {signed(rule("news2_or_student_checked").excess)}. The reward-learned policy added to NEWS2 is {added(M.anyAllCounts.news2OrRewardFinal)} more
                of {C.evaluated} at {times("news2_or_reward_final")} times the alerts, excess {signed(rule("news2_or_reward_final").excess)}.
              </p>
              <p>
                A second held-out set, {H2.trials} new trials ({num(H2.episodes)} episodes, {num(H2.notCutShort)} not cut short), was read once
                afterwards, with an analysis written before the read. It is much larger, and it is the result to quote. The gain is the excess over
                chance of NEWS2 plus the model, minus that of NEWS2 alone; the unit is the trial and the interval is 98.33%.
              </p>
              <ul data-testid="held-out-2">
                {H2.rows.map((r) => (
                  <li key={r.id}>
                    NEWS2 or {r.name}: gain {signed4(r.gain)} ({signed4(r.lo)} to {signed4(r.hi)}), {r.windowsAdded} more episode windows at{" "}
                    {r.alertRatio.toFixed(3)} times NEWS2&apos;s alerts: {r.verdict}.
                  </li>
                ))}
              </ul>
              <p>
                The gain for the checked student is small and is bought with a few more alerts. The reward policy&apos;s upper end is at the edge of zero.
                Both are statements about this simulator only.
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
                ({rule("student").timeInAlert.toFixed(3)} against {rule("ref_news2_chart_trigger").timeInAlert.toFixed(3)}).
              </p>
              <p>
                The reward-learned policy was searched with {M.rewardCandidates} candidates per generation. A winner was kept
                only if it also beat the current policy on a second batch of new trials. {M.rewardAcceptedSteps} step was accepted,
                and the outer loop stopped after {M.rewardRounds} rounds. Alone it is a
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

            <p className="hw-caption">
              Source: evaluation on {P.date}, simulator commit {P.simCommit}, scenario {P.scenario}. {P.trials}. {P.tuning} {P.heldOutRead} Student model (run {P.studentRun}): {P.studentData}. Reward-learned policy (run {P.rewardRun}): {P.rewardData}. Confidence check (run {P.checkRun}): {P.checkData}. Earlier run: {P.previous}. NEWS2 itself is never
              changed by any of this.
            </p>
          </div>
        </details>

        {session?.role === "admin" && <ResearchAgentSection />}
      </div>
    </LabFrame>
  );
}
