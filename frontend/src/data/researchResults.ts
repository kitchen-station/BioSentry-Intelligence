// Transcribed by hand from the printed table of `python -m research.evaluate`. Not computed here.
export const RESEARCH_PROVENANCE = {
  commit: "46f9017",
  simCommit: "3a15e01",
  scenario: "95052d02", // first 8 characters of the scenario sha256
  date: "2026-11-12",
  trials: "ward-v1 simulator trials, held-out seeds 11 to 30, 1,873 patients, 91 episodes (79 not cut short)",
  tuning: "Detector settings k=3.0 p=4 were chosen on seeds 1 to 10.",
  studentCommit: "5641d92",
  rewardCommit: "cce584b",
} as const;

export interface Frac {
  n: number;
  d: number;
  rate: number; // as printed, 3 decimals
}

export interface RuleResult {
  id: string;
  name: string;
  perShift: number; // alerts per patient per 12-hour shift
  episodes: Frac; // alerting in any bin, episodes not cut short
  chance: Frac; // same on control windows (patients with no episode)
  excess: number; // as printed: episodes rate minus chance rate
  leadN: number; // episodes with a new alert (of 91 evaluated)
  leadMedianMin: number; // episodes with a new alert; negative = after fully in
}

const f = (n: number, d: number, rate: number): Frac => ({ n, d, rate });

export const RESEARCH_RESULTS: readonly RuleResult[] = [
  { id: "news2_chart_trigger", name: "NEWS2 chart trigger", perShift: 0.413,
    episodes: f(56, 79, 0.709), chance: f(2208, 4678, 0.472), excess: 0.237, leadN: 34, leadMedianMin: 45.9 },
  { id: "news2_total_ge_5", name: "NEWS2 total 5 or more", perShift: 0.338,
    episodes: f(53, 79, 0.671), chance: f(1751, 4678, 0.374), excess: 0.297, leadN: 39, leadMedianMin: 34.6 },
  { id: "baseline_alone", name: "Own baseline alone", perShift: 0.189,
    episodes: f(67, 79, 0.848), chance: f(831, 4678, 0.178), excess: 0.67, leadN: 64, leadMedianMin: -18.8 },
  { id: "news2_or_baseline", name: "NEWS2 or own baseline", perShift: 0.473,
    episodes: f(73, 79, 0.924), chance: f(2573, 4678, 0.55), excess: 0.374, leadN: 47, leadMedianMin: 20.5 },
  { id: "baseline_alone_no_temp", name: "Own baseline alone, without temperature", perShift: 0.139,
    episodes: f(44, 79, 0.557), chance: f(642, 4678, 0.137), excess: 0.42, leadN: 44, leadMedianMin: -14.1 },
  { id: "news2_or_baseline_no_temp", name: "NEWS2 or own baseline, without temperature", perShift: 0.462,
    episodes: f(59, 79, 0.747), chance: f(2495, 4678, 0.533), excess: 0.213, leadN: 37, leadMedianMin: 59.7 },
];

/** A row of the learned-rules table. Counts are shown only where the source gives them. */
export interface LearnedRule {
  id: string;
  name: string;
  perShift: number;
  timeInAlert: number;
  episodes: { rate: number; n?: number; d?: number }; // alerting in any bin, episodes not cut short
  chance: { rate: number; n?: number; d?: number };
  excess: number; // as printed in the cards
  leadMedianMin: number;
}

// Transcribed by hand from the model cards (student_vitals, reward_policy), held-out seeds 11 to 30.
// The two reference rows repeat the table above; their time in alert is from the reward policy card.
export const LEARNED_RULES: readonly LearnedRule[] = [
  { id: "ref_news2_chart_trigger", name: "NEWS2 chart trigger (reference)", perShift: 0.413, timeInAlert: 0.159,
    episodes: f(56, 79, 0.709), chance: f(2208, 4678, 0.472), excess: 0.237, leadMedianMin: 45.9 },
  { id: "ref_baseline_alone", name: "Own baseline alone (reference)", perShift: 0.189, timeInAlert: 0.047,
    episodes: f(67, 79, 0.848), chance: f(831, 4678, 0.178), excess: 0.67, leadMedianMin: -18.8 },
  { id: "student", name: "Student model", perShift: 0.226, timeInAlert: 0.036,
    episodes: { rate: 0.696 }, chance: { rate: 0.157 }, excess: 0.539, leadMedianMin: 20.5 },
  { id: "news2_or_student", name: "NEWS2 or student", perShift: 0.457, timeInAlert: 0.173,
    episodes: { rate: 0.81 }, chance: { rate: 0.509 }, excess: 0.301, leadMedianMin: 44.6 },
  { id: "reward_gen0", name: "Reward-learned policy, generation 0", perShift: 0.428, timeInAlert: 0.127,
    episodes: { rate: 0.886 }, chance: { rate: 0.436 }, excess: 0.45, leadMedianMin: 23.0 },
  { id: "reward_final", name: "Reward-learned policy, final", perShift: 0.179, timeInAlert: 0.033,
    episodes: { rate: 0.835 }, chance: { rate: 0.135 }, excess: 0.7, leadMedianMin: 19.3 },
  { id: "news2_or_reward_gen0", name: "NEWS2 or reward-learned policy, generation 0", perShift: 0.533, timeInAlert: 0.251,
    episodes: { rate: 0.937 }, chance: { rate: 0.665 }, excess: 0.271, leadMedianMin: 56.5 },
  { id: "news2_or_reward_final", name: "NEWS2 or reward-learned policy, final", perShift: 0.462, timeInAlert: 0.177,
    episodes: { rate: 0.937 }, chance: { rate: 0.526 }, excess: 0.41, leadMedianMin: 33.8 },
  { id: "reward_final_no_temp", name: "Reward-learned policy without temperature, final", perShift: 0.061, timeInAlert: 0.017,
    episodes: { rate: 0.519 }, chance: { rate: 0.05 }, excess: 0.469, leadMedianMin: -38.7 },
  { id: "news2_or_reward_final_no_temp", name: "NEWS2 or reward-learned policy without temperature, final", perShift: 0.433, timeInAlert: 0.166,
    episodes: { rate: 0.734 }, chance: { rate: 0.491 }, excess: 0.243, leadMedianMin: 58.1 },
];
