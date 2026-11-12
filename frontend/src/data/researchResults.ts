// Transcribed by hand from the printed table of `python -m research.evaluate`. Not computed here.
// Run on ward-v2 (the simulator after its fixed temperature rise was removed).
export const RESEARCH_PROVENANCE = {
  commit: "a9a7c36",
  simCommit: "f7647c5",
  scenario: "95052d02", // first 8 characters of the scenario sha256
  date: "2026-11-12",
  trials: "ward-v2 simulator trials, held-out seeds 11 to 30, 1,873 patients, 91 episodes (79 not cut short)",
  tuning: "Detector settings k=4.0 p=8 were chosen on seeds 1 to 10.",
  studentCommit: "a9a7c36",
  rewardCommit: "a9a7c36",
  previous: "ward-v1, simulator commit 3a15e01, which added a fixed temperature rise to every episode",
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
  { id: "news2_chart_trigger", name: "NEWS2 chart trigger", perShift: 0.414,
    episodes: f(54, 79, 0.684), chance: f(2215, 4678, 0.473), excess: 0.21, leadN: 33, leadMedianMin: 47.2 },
  { id: "news2_total_ge_5", name: "NEWS2 total 5 or more", perShift: 0.34,
    episodes: f(49, 79, 0.62), chance: f(1761, 4678, 0.376), excess: 0.244, leadN: 36, leadMedianMin: 45.9 },
  { id: "baseline_alone", name: "Own baseline alone", perShift: 0.042,
    episodes: f(22, 79, 0.278), chance: f(150, 4678, 0.032), excess: 0.246, leadN: 24, leadMedianMin: -82.5 },
  { id: "news2_or_baseline", name: "NEWS2 or own baseline", perShift: 0.426,
    episodes: f(57, 79, 0.722), chance: f(2274, 4678, 0.486), excess: 0.235, leadN: 36, leadMedianMin: 39.6 },
  { id: "baseline_alone_no_temp", name: "Own baseline alone, without temperature", perShift: 0.032,
    episodes: f(21, 79, 0.266), chance: f(115, 4678, 0.025), excess: 0.241, leadN: 23, leadMedianMin: -85.3 },
  { id: "news2_or_baseline_no_temp", name: "NEWS2 or own baseline, without temperature", perShift: 0.425,
    episodes: f(56, 79, 0.709), chance: f(2263, 4678, 0.484), excess: 0.225, leadN: 35, leadMedianMin: 44.6 },
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

// Transcribed by hand from the model cards (student_vitals, reward_policy), ward-v2, held-out seeds 11 to 30.
// The two reference rows repeat the table above; their time in alert is from the reward policy card.
export const LEARNED_RULES: readonly LearnedRule[] = [
  { id: "ref_news2_chart_trigger", name: "NEWS2 chart trigger (reference)", perShift: 0.414, timeInAlert: 0.158,
    episodes: f(54, 79, 0.684), chance: f(2215, 4678, 0.473), excess: 0.21, leadMedianMin: 47.2 },
  { id: "ref_baseline_alone", name: "Own baseline alone (reference)", perShift: 0.042, timeInAlert: 0.012,
    episodes: f(22, 79, 0.278), chance: f(150, 4678, 0.032), excess: 0.246, leadMedianMin: -82.5 },
  { id: "student", name: "Student model", perShift: 0.088, timeInAlert: 0.019,
    episodes: { rate: 0.367 }, chance: { rate: 0.058 }, excess: 0.309, leadMedianMin: 5.2 },
  { id: "news2_or_student", name: "NEWS2 or student", perShift: 0.429, timeInAlert: 0.165,
    episodes: { rate: 0.709 }, chance: { rate: 0.486 }, excess: 0.223, leadMedianMin: 47.2 },
  { id: "reward_gen0", name: "Reward-learned policy, generation 0", perShift: 0.429, timeInAlert: 0.123,
    episodes: { rate: 0.785 }, chance: { rate: 0.435 }, excess: 0.35, leadMedianMin: 23.0 },
  { id: "reward_final", name: "Reward-learned policy, final", perShift: 0.15, timeInAlert: 0.029,
    episodes: { rate: 0.646 }, chance: { rate: 0.12 }, excess: 0.525, leadMedianMin: -11.2 },
  { id: "news2_or_reward_gen0", name: "NEWS2 or reward-learned policy, generation 0", perShift: 0.535, timeInAlert: 0.248,
    episodes: { rate: 0.861 }, chance: { rate: 0.666 }, excess: 0.195, leadMedianMin: 67.9 },
  { id: "news2_or_reward_final", name: "NEWS2 or reward-learned policy, final", perShift: 0.465, timeInAlert: 0.173,
    episodes: { rate: 0.772 }, chance: { rate: 0.516 }, excess: 0.256, leadMedianMin: 61.5 },
  { id: "reward_final_no_temp", name: "Reward-learned policy without temperature, final", perShift: 0.061, timeInAlert: 0.017,
    episodes: { rate: 0.519 }, chance: { rate: 0.05 }, excess: 0.469, leadMedianMin: -38.7 },
  { id: "news2_or_reward_final_no_temp", name: "NEWS2 or reward-learned policy without temperature, final", perShift: 0.434, timeInAlert: 0.166,
    episodes: { rate: 0.722 }, chance: { rate: 0.492 }, excess: 0.229, leadMedianMin: 60.9 },
];

/** One side of the ward-v1 against ward-v2 comparison. */
export interface RunSide {
  perShift: number; // alerts per patient per 12-hour shift
  anyAll: number; // alerting in any bin, all 91 evaluated episodes
  lo: number; // 95% interval, patient-level bootstrap
  hi: number;
  excess: number; // any-bin excess over chance, episodes not cut short; no interval
}

const side = (perShift: number, anyAll: number, lo: number, hi: number, excess: number): RunSide => ({ perShift, anyAll, lo, hi, excess });

// Transcribed by hand from the `research.evaluate_student` and `reward_loop --evaluate` outputs on
// ward-v1 (with the temperature rise) and ward-v2 (without it), held-out seeds 11 to 30.
export const RUN_COMPARISON: readonly { id: string; name: string; v1: RunSide; v2: RunSide }[] = [
  { id: "news2_chart_trigger", name: "NEWS2 chart trigger", v1: side(0.413, 0.648, 0.545, 0.745, 0.237), v2: side(0.414, 0.626, 0.522, 0.727, 0.21) },
  { id: "baseline_alone", name: "Own baseline alone", v1: side(0.189, 0.769, 0.677, 0.851, 0.67), v2: side(0.042, 0.264, 0.174, 0.36, 0.246) },
  { id: "news2_or_baseline", name: "NEWS2 or own baseline", v1: side(0.473, 0.846, 0.771, 0.913, 0.374), v2: side(0.426, 0.659, 0.558, 0.758, 0.235) },
  { id: "student", name: "Student model", v1: side(0.226, 0.637, 0.527, 0.731, 0.539), v2: side(0.088, 0.352, 0.25, 0.452, 0.309) },
  { id: "news2_or_student", name: "NEWS2 or student", v1: side(0.457, 0.736, 0.643, 0.821, 0.301), v2: side(0.429, 0.648, 0.545, 0.745, 0.223) },
  { id: "reward_final", name: "Reward-learned policy, final", v1: side(0.179, 0.769, 0.676, 0.852, 0.7), v2: side(0.15, 0.582, 0.464, 0.678, 0.525) },
  { id: "news2_or_reward_final", name: "NEWS2 or reward-learned policy, final", v1: side(0.462, 0.857, 0.779, 0.922, 0.41), v2: side(0.465, 0.703, 0.608, 0.798, 0.256) },
];
