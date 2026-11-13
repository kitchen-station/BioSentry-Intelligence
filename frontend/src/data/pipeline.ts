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

/** The eight stages in order, with their state on commit 53f7106. Change one line when a state changes. */
export const PIPELINE: readonly Stage[] = [
  {
    id: "news2", name: "NEWS2", state: "live in the ranking", tier: "A",
    text: ["The published RCP 2017 early warning score, from eight bedside readings, with no learned weights.", "It orders the ward list: trigger level first, then the total."],
    evidence: "backend/src/biosentry/domain/scoring/tier_a/news2.py, domain/arbiter.py:105-107, models/tier_a/news2/card.md",
  },
  {
    id: "quality", name: "Signal quality check", state: "live in the ranking",
    text: ["Each device reading carries a quality mark, set by the ward simulator with a fixed rule: a reading held too long turns degraded, then missing.", "A missing or impossible reading is not scored, so the patient is listed NOT RANKED with what to record."],
    evidence: "sim src/sim/sim.ts:3,316-320, domain/scoring/tier_a/news2.py:150-162,269-298, domain/arbiter.py:184-186",
  },
  {
    id: "baseline", name: "Change from the patient's own baseline", state: "shown, does not change the ranking",
    text: ["Compares heart rate, breathing rate, SpO2 and temperature with the patient's own first 24 valid 15-minute bins, six hours of data.", "Shown on the patient page with the live API only. It does not change the rank."],
    evidence: "services/baseline.py:1-11, domain/scoring/tier_b/baseline.py:47, schemas/baseline.py:22, frontend/src/components/BaselinePanel.tsx:146",
  },
  {
    id: "prediction", name: "Prediction model", state: "research only, offline",
    text: ["A small learned model gives a score for each 15-minute bin of simulator data.", "Trained and judged on the same simulator. It is not connected to the API, the rank or the arbiter."],
    evidence: "models/tier_c/student_vitals/card.md",
  },
  {
    id: "reward", name: "Alert policy learned by reward", state: "research only, offline",
    text: ["An alert rule of six numbers, changed step by step and kept only when it scored a higher reward on simulator trials.", "Learned on synthetic trials, not validated, and not connected to the API, the rank or the arbiter."],
    evidence: "models/tier_b/reward_policy/card.md, research/README.md:215-227, frontend/src/data/researchResults.ts",
  },
  {
    id: "confidence", name: "Confidence check", state: "planned",
    text: ["A check that a model's output is still inside the range it was tested on before it may count.", "Not built. The arbiter has an empty slot for it that changes nothing."],
    evidence: "domain/arbiter.py:80-83,131-145, README Pending",
  },
  {
    id: "agent", name: "Research agent", state: "planned",
    text: ["A plain-language explanation of each ranked alert for the nurse or physician.", "Not built. Nothing of it is in this repository."],
    evidence: "README Pending",
  },
  {
    id: "arbiter", name: "The arbiter", state: "live in the ranking", decides: true,
    text: ["The only part that decides what reaches the nurse.", "Two rules are live: a NEWS2 trigger escalates the patient, and a missing required reading lists the patient NOT RANKED. Its slots for the model, the findings and the notes accept input and change nothing."],
    evidence: "domain/arbiter.py:1-17,172-195, services/scoring.py:10-12",
  },
];
