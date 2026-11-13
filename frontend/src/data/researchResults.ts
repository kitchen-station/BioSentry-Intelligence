// Transcribed by hand, not computed here. Run on ward-v2 (the simulator after its fixed temperature rise was removed
// and temperature noise raised to 0.1 C). Every block below names the file it was copied from.
// The NEWS2 and own-baseline rows: models/tier_c/student_vitals/held_out.json (rules), rounded to 3 decimals (1 for minutes).
export const RESEARCH_PROVENANCE = {
  simCommit: "f7647c5", // simulator commit of the held-out trials
  scenario: "95052d02", // first 8 characters of the scenario sha256
  date: "2026-11-13",
  trials: "ward-v2 simulator trials, held-out seeds 11 to 30, 1,873 patients, 91 episodes (79 not cut short)",
  tuning: "Detector settings k=4.0 p=8 were chosen on seeds 1 to 10.",
  heldOutRead: "Each held-out set was read once.",
  // The runs were squash-merged: the run id is the stable name, not a commit.
  studentRun: "m1-20261113-3d634f3",
  studentData: "trained with MindSpore 2.11.0 on ward-v2-ext seeds 61 to 160, chosen on seeds 261 to 300",
  rewardRun: "m2-20261113",
  rewardData: "learned on ward-v2-ext seeds from 401, validation seeds 341 to 400",
  checkRun: "m3-20261113-fbd39f2",
  checkData: "calibrated on ward-v2-ext seeds 301 to 340",
  previous: "ward-v1, simulator commit 3a15e01, which added a fixed temperature rise to every episode and used temperature noise 0.02 C",
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

// Transcribed by hand, ward-v2, held-out seeds 11 to 30, each set read once.
// Reference rows and student rows (teacher, student, with and without temperature): models/tier_c/student_vitals/held_out.json.
// Student with the confidence check: models/tier_c/confidence_check/heldout.json (rules).
// Reward-learned policy rows: models/tier_b/reward_policy/held_out.txt (printed table; its card.md repeats it).
// Per row: per_shift.value, time_in_alert.rate, any_bin_not_cut_short, chance_any, excess_any_not_cut_short, lead.median.
export const LEARNED_RULES: readonly LearnedRule[] = [
  { id: "ref_news2_chart_trigger", name: "NEWS2 chart trigger (reference)", perShift: 0.414, timeInAlert: 0.158,
    episodes: f(54, 79, 0.684), chance: f(2215, 4678, 0.473), excess: 0.21, leadMedianMin: 47.2 },
  { id: "ref_baseline_alone", name: "Own baseline alone (reference)", perShift: 0.042, timeInAlert: 0.012,
    episodes: f(22, 79, 0.278), chance: f(150, 4678, 0.032), excess: 0.246, leadMedianMin: -82.5 },
  { id: "teacher", name: "Teacher model (the larger network)", perShift: 0.391, timeInAlert: 0.069,
    episodes: f(56, 79, 0.709), chance: f(1438, 4678, 0.307), excess: 0.401, leadMedianMin: 49.6 },
  { id: "student", name: "Student model", perShift: 0.413, timeInAlert: 0.064,
    episodes: f(58, 79, 0.734), chance: f(1493, 4678, 0.319), excess: 0.415, leadMedianMin: 59.5 },
  { id: "news2_or_student", name: "NEWS2 or student", perShift: 0.525, timeInAlert: 0.185,
    episodes: f(65, 79, 0.823), chance: f(2712, 4678, 0.580), excess: 0.243, leadMedianMin: 66.0 },
  { id: "student_checked", name: "Student model with the confidence check", perShift: 0.128, timeInAlert: 0.026,
    episodes: f(50, 79, 0.633), chance: f(415, 4678, 0.089), excess: 0.544, leadMedianMin: 20.5 },
  { id: "news2_or_student_checked", name: "NEWS2 or student with the confidence check", perShift: 0.438, timeInAlert: 0.165,
    episodes: f(60, 79, 0.759), chance: f(2299, 4678, 0.491), excess: 0.268, leadMedianMin: 44.6 },
  { id: "reward_gen0", name: "Reward-learned policy, generation 0", perShift: 0.429, timeInAlert: 0.123,
    episodes: f(62, 79, 0.785), chance: f(2034, 4678, 0.435), excess: 0.350, leadMedianMin: 23.0 },
  { id: "reward_final", name: "Reward-learned policy, final", perShift: 0.153, timeInAlert: 0.035,
    episodes: f(50, 79, 0.633), chance: f(656, 4678, 0.140), excess: 0.493, leadMedianMin: -3.8 },
  { id: "news2_or_reward_gen0", name: "NEWS2 or reward-learned policy, generation 0", perShift: 0.535, timeInAlert: 0.248,
    episodes: f(68, 79, 0.861), chance: f(3116, 4678, 0.666), excess: 0.195, leadMedianMin: 67.9 },
  { id: "news2_or_reward_final", name: "NEWS2 or reward-learned policy, final", perShift: 0.463, timeInAlert: 0.178,
    episodes: f(61, 79, 0.772), chance: f(2472, 4678, 0.528), excess: 0.244, leadMedianMin: 73.2 },
  { id: "student_no_temp", name: "Student model without temperature", perShift: 0.409, timeInAlert: 0.065,
    episodes: f(59, 79, 0.747), chance: f(1540, 4678, 0.329), excess: 0.418, leadMedianMin: 48.8 },
  { id: "news2_or_student_no_temp", name: "NEWS2 or student without temperature", perShift: 0.512, timeInAlert: 0.183,
    episodes: f(65, 79, 0.823), chance: f(2699, 4678, 0.577), excess: 0.246, leadMedianMin: 66.0 },
  { id: "student_checked_no_temp", name: "Student model with the confidence check, without temperature", perShift: 0.127, timeInAlert: 0.025,
    episodes: f(50, 79, 0.633), chance: f(427, 4678, 0.091), excess: 0.542, leadMedianMin: 16.1 },
  { id: "news2_or_student_checked_no_temp", name: "NEWS2 or student with the confidence check, without temperature", perShift: 0.433, timeInAlert: 0.165,
    episodes: f(60, 79, 0.759), chance: f(2298, 4678, 0.491), excess: 0.268, leadMedianMin: 44.6 },
  { id: "reward_final_no_temp", name: "Reward-learned policy without temperature, final", perShift: 0.131, timeInAlert: 0.021,
    episodes: f(48, 79, 0.608), chance: f(427, 4678, 0.091), excess: 0.516, leadMedianMin: 20.2 },
  { id: "news2_or_reward_final_no_temp", name: "NEWS2 or reward-learned policy without temperature, final", perShift: 0.451, timeInAlert: 0.166,
    episodes: f(60, 79, 0.759), chance: f(2357, 4678, 0.504), excess: 0.256, leadMedianMin: 56.5 },
];

/**
 * The confidence check on the student, held-out seeds 11 to 30, variant with temperature.
 * models/tier_c/confidence_check/heldout.json, confidence.all; calibration: calibration.json (alpha "0.01", 3,238 stays).
 */
export const CONFIDENCE_CHECK = {
  targetOneIn: 100, // alpha 0.01
  achievedPct: 0.65, // achieved_error.mean 0.00655 as a share of non-onset checks, in percent
  achievedLoPct: 0.57, // achieved_error.lo
  achievedHiPct: 0.74, // achieved_error.hi
  verdict: "MET", // as written by the tool
  notSureBins: 27523, // not_sure.bins
  studentAlertingBins: 46103, // alerting_bins.student = not_sure.of_student_alerting_bins
  notSureShare: 0.597, // 27523 / 46103
  checkedAlertingBins: 18580, // alerting_bins.checked
  studentNewAlerts: 6247, // new_alerts.student
  checkedNewAlerts: 1930, // new_alerts.checked
  lostAny: 8, // lost.all.any: episodes the student caught in any bin and the checked rule did not
  lostEarly: 13, // lost.all.early: same, caught early (a new alert before fully in)
  evaluated: 91, // episodes_evaluated
} as const;

/**
 * Facts the page text quotes.
 * Student: models/tier_c/student_vitals/metadata.json, variants.all (parameters.student, parameters.teacher,
 * student.val_log_loss, references.hard_label_mlp.val_log_loss; validation seeds 261 to 300).
 * Reward policy: models/tier_b/reward_policy/card.md (Result, Inner loop).
 * Intervals (95%, patient-level bootstrap, any bin, all 91 evaluated episodes): held_out.json, rules[].any_bin
 * for the student and NEWS2 or student; card.md of the reward policy for NEWS2 or final.
 */
export const MODEL_FACTS = {
  studentParameters: 1185,
  teacherParameters: 79873,
  studentValLogLoss: 0.018969,
  hardLabelValLogLoss: 0.018925, // the same small network fitted on the true labels
  rewardCandidates: 30, // per generation
  rewardAcceptedSteps: 1,
  rewardRounds: 2, // of 4 allowed
  anyAllIntervals: {
    news2: { rate: 0.626, lo: 0.522, hi: 0.727 },
    student: { rate: 0.67, lo: 0.57, hi: 0.762 },
    news2OrStudent: { rate: 0.747, lo: 0.655, hi: 0.831 },
    news2OrRewardFinal: { rate: 0.703, lo: 0.608, hi: 0.798 },
  },
} as const;

/** One side of the ward-v1 against ward-v2 comparison. */
export interface RunSide {
  perShift: number; // alerts per patient per 12-hour shift
  anyAll: number; // alerting in any bin, all 91 evaluated episodes
  lo: number; // 95% interval, patient-level bootstrap
  hi: number;
  excess: number; // any-bin excess over chance, episodes not cut short; no interval
}

const side = (perShift: number, anyAll: number, lo: number, hi: number, excess: number): RunSide => ({ perShift, anyAll, lo, hi, excess });

// Transcribed by hand from the `research.evaluate` outputs on ward-v1 (with the temperature rise, noise 0.02 C) and
// ward-v2 (no rise, noise 0.1 C), held-out seeds 11 to 30. Only the rules that did not change: the student and the
// reward-learned policy were trained again, so their ward-v1 numbers belong to other models and are not shown.
export const RUN_COMPARISON: readonly { id: string; name: string; v1: RunSide; v2: RunSide }[] = [
  { id: "news2_chart_trigger", name: "NEWS2 chart trigger", v1: side(0.413, 0.648, 0.545, 0.745, 0.237), v2: side(0.414, 0.626, 0.522, 0.727, 0.21) },
  { id: "baseline_alone", name: "Own baseline alone", v1: side(0.189, 0.769, 0.677, 0.851, 0.67), v2: side(0.042, 0.264, 0.174, 0.36, 0.246) },
  { id: "news2_or_baseline", name: "NEWS2 or own baseline", v1: side(0.473, 0.846, 0.771, 0.913, 0.374), v2: side(0.426, 0.659, 0.558, 0.758, 0.235) },
];
