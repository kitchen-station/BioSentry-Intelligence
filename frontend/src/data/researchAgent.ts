// Hand-exported from the transcript in PR 19's description (merged; no ledger is committed). Not computed here.
// When `python -m research.agent export` exists, research-agent.json is replaced by its output; this file does not change.
import data from "./research-agent.json";
export const ASK_STATUSES = ["answered", "refused"] as const;
export const PROPOSAL_STATES = ["proposed", "approved", "applied"] as const; // there is no "rejected"
export interface AgentQuestion {
  id: string; // derived from record counts, not shown
  askedAt: string | null; // null: the transcript prints no time
  question: string;
  template: "news2_threshold" | "baseline_vs_news2" | "alerts_per_nurse" | "none";
  status: (typeof ASK_STATUSES)[number];
  answer: readonly string[]; // line 2 of an answer is the chance control
  numbers: readonly string[]; // the values the grounding check passed, in order
}
export interface AgentProposal {
  id: string;
  action: "set_baseline_params" | "adopt_reward_policy";
  step: string;
  conclusion: string;
  state: (typeof PROPOSAL_STATES)[number];
  approvedBy: string | null;
  approvedAt: string | null;
}
export interface AgentExport {
  provenance: { source: string; records: number; exportedBy: string; date: string };
  questions: readonly AgentQuestion[];
  proposals: readonly AgentProposal[];
}
export const RESEARCH_AGENT = data as AgentExport;
