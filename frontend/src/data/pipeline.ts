export const STAGE_STATES = ["live in the ranking", "shown, does not change the ranking", "research only, offline", "planned"] as const;
export type StageState = (typeof STAGE_STATES)[number];
export interface Stage {
  id: string;
  name: string;
  state: StageState;
  text: readonly string[];
  evidence: string;
  tier?: "A";
  decides?: true;
}

/** The eight stages in order, with their state when this file was last changed. Change one line when a state changes. */
export const PIPELINE: readonly Stage[] = [
  {
    id: "news2", name: "NEWS2", state: "live in the ranking", tier: "A",
    text: ["The published RCP 2017 early warning score: seven bedside observations scored from fixed tables, on the SpO2 scale a clinician has recorded, with no learned weights.", "It orders the ward list: trigger level first, then the total; an own-baseline change only breaks a tie."],
    evidence: "backend/src/biosentry/domain/scoring/tier_a/news2.py, domain/arbiter.py (Ranked.sort_key), models/tier_a/news2/card.md",
  },
  {
    id: "quality", name: "Signal quality check", state: "live in the ranking",
    text: ["Each device reading carries a quality mark set by the ward simulator's fixed demo rule, not by BioSentry: a value not refreshed in time turns degraded, then missing.", "BioSentry scores a degraded reading as recorded; a missing or impossible one is not scored, so the patient is listed NOT RANKED with the step to take."],
    evidence: "sim src/sim/sim.ts (quality gate), domain/scoring/tier_a/news2.py (_usable), domain/arbiter.py (arbitrate, NotRanked)",
  },
  {
    id: "baseline", name: "Change from the patient's own baseline", state: "live in the ranking",
    text: ["Compares heart rate, breathing rate and SpO2 with the patient's own first 24 valid 15-minute bins, six hours of data. Temperature is not compared: the patient read carries no temperature history, so it has no baseline.", "A change in the worse direction (heart rate or breathing rate up, SpO2 down) lasting 45 minutes or more, while the patch is still sampling, is listed as a reason on the ward row and breaks ties between patients with the same NEWS2 total and no trigger. It never moves a patient past a NEWS2 difference. It runs on the ward's own setting (k 2.0, p 3), not the k 4.0, p 8 measured on the Research page. Live API only."],
    evidence: "services/scoring.py (drift), domain/scoring/tier_b/baseline.py (baseline_finding), domain/arbiter.py (Ranked.sort_key), frontend/src/components/BaselinePanel.tsx",
  },
  {
    id: "prediction", name: "Prediction model", state: "research only, offline",
    text: ["A small MindSpore network (one hidden layer, 1,185 parameters) gives a score for each 15-minute bin of simulator data. It is fitted to the outputs of a larger network, which bought nothing measurable on validation.", "Trained and judged on the same simulator, not validated. It is not connected to the API, the rank or the arbiter."],
    evidence: "models/tier_c/student_vitals/card.md, research/README.md, frontend/src/data/researchResults.ts",
  },
  {
    id: "reward", name: "Alert policy learned by reward", state: "research only, offline",
    text: ["An alert rule of six numbers. Each generation tries 30 candidates and keeps a winner only if it also beats the current rule on a second batch of new simulator trials.", "Learned on synthetic trials, not validated, and not connected to the API, the rank or the arbiter."],
    evidence: "models/tier_b/reward_policy/card.md, research/README.md, frontend/src/data/researchResults.ts",
  },
  {
    id: "confidence", name: "Confidence check", state: "research only, offline",
    text: ["A check on the student: a score too low to support an alert is marked not sure, and a not sure bin never alerts. On simulator trials its target was met: at most 1 in 100 checks on a patient who is not starting to deteriorate shows a supported alert.", "Not validated, and not connected to the API, the rank or the arbiter. The arbiter's slot for it changes nothing."],
    evidence: "models/tier_c/confidence_check/card.md, research/confidence.py, domain/arbiter.py (Guarantees, _inert)",
  },
  {
    id: "agent", name: "Research agent", state: "research only, offline",
    text: ["Run by hand on simulated trials: it picks the next experiment from a fixed list and answers three fixed kinds of question in fixed sentences, each with a chance control. An optional model planner, off unless set up and off in the recorded run, may only reorder that list.", "Every step goes to a ledger, and a proposal changes no file until a sign-off is recorded. It is not connected to the API, the rank or the arbiter, and the dashboard cannot ask it a question."],
    evidence: "research/agent/ (on main since #19), frontend/src/data/research-agent.json",
  },
  {
    id: "arbiter", name: "The arbiter", state: "live in the ranking", decides: true,
    text: ["The only part that decides what reaches the nurse.", "Three rules are live: a NEWS2 trigger escalates the patient; an own-baseline change is listed as a reason and breaks ties between equal NEWS2 totals below a trigger; a missing or impossible required input lists the patient NOT RANKED. Its other slots (prediction model, learned alert policy, notes layer, worried pin, confidence check) accept input and change nothing; today nothing is passed to them."],
    evidence: "domain/arbiter.py (arbitrate, rank_ward), services/scoring.py (decide)",
  },
];
