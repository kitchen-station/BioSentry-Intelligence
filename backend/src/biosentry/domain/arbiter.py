"""The escalation arbiter: the only component allowed to change displayed priority.

v1 implements three rules; the others are typed, inert hooks (README, "Design rules"):

  1. Tier A threshold met -> escalate unconditionally.            LIVE
  2. Tier C may raise, never below Tier A.                        inert (no Tier C yet)
  3. Tier B significant finding -> list it, attach the pathway.   LIVE (a tie-break, see below)
  4. Notes-layer concern or a "worried" pin -> raise, attach it.  inert (v2)
  5. Missing required input -> NOT RANKED, naming it. Never impute. LIVE

An inert hook accepts its typed input and changes nothing; `Ranked.inert` records that it was
supplied, so a reader can see what was ignored. A tier that abstains contributes no value.

Rule 1 policy: NEWS2 chart 2 triggers. Any trigger (a single parameter at 3, a total of 5-6,
a total of 7 or more) escalates; a total of 0-4 with no single 3 does not. Order within the
ward is by trigger level, then NEWS2 total, then a Tier B flag, then patient id. The flag only
orders patients with no trigger and an equal total: it never moves a patient past a NEWS2 difference
and never sets `escalated`. A learned policy hook (`PolicySignal`) is inert like the others.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from biosentry.schemas import EscalationReason, EscalationRule

from .computed import NotComputed, Result
from .scoring.tier_a import (
    InputProblem,
    News2Abstained,
    News2Result,
    News2Score,
    Problem,
    Risk,
)
from .scoring.tier_b.baseline import Deviation
from .scoring.tier_b.interface import Finding

_LEVEL = {Risk.low: 0, Risk.low_medium: 1, Risk.medium: 2, Risk.high: 3}
_ESCALATING = frozenset({Risk.low_medium, Risk.medium, Risk.high})

_NURSE_ENTRY = {
    "systolic_bp": ("Systolic BP", "Record a blood pressure"),
    "consciousness": ("Level of consciousness", "Record ACVPU"),
    "supplemental_o2": ("Supplemental oxygen", "Record whether the patient is on oxygen"),
    "spo2_scale": ("SpO2 scale", "Record the SpO2 scale (a clinician's decision)"),
}
_DEVICE = {
    "respiratory_rate": "Respiratory rate",
    "spo2": "SpO2",
    "heart_rate": "Heart rate",
    "temperature": "Temperature",
}
_NAME = {
    "heart_rate": "heart rate",
    "respiratory_rate": "respiratory rate",
    "spo2": "SpO2",
    "temperature": "temperature",
}
_DEVICE_INSTRUCTION = "Check the patch is applied and the signal is verified, or enter the observations"
_NO_DEVICE_INSTRUCTION = "Apply the patch and verify the signal, or enter the observations"


# --- Typed hooks for rules 2-4 and the local guarantees: accepted, ignored in v1 ----------


@dataclass(frozen=True, slots=True)
class TierBSignal:
    findings: tuple[tuple[Finding, Deviation], ...] | NotComputed  # rule 3


@dataclass(frozen=True, slots=True)
class TierCSignal:
    probability: Result[float]  # rule 2: may raise, never lower below Tier A


@dataclass(frozen=True, slots=True)
class PolicySignal:
    alert: Result[bool]  # a learned alert policy: may not lower below Tier A


@dataclass(frozen=True, slots=True)
class NotesSignal:
    concern: bool  # rule 4 (v2 notes layer)
    pathway: str | None
    trigger_phrase: str | None


@dataclass(frozen=True, slots=True)
class WorriedPin:
    set_by: str  # rule 4: a clinician's structured "worried" flag
    reason: str


@dataclass(frozen=True, slots=True)
class Guarantees:
    conformal_certificate_current: bool | None  # backlog #4
    drift_flagged: bool | None


# --- Decisions ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArbiterReason:
    rule: EscalationRule
    text: str
    pathway: str | None = None


@dataclass(frozen=True, slots=True)
class Ranked:
    patient_id: str
    news2: News2Score
    escalated: bool
    reasons: tuple[ArbiterReason, ...]  # why this patient is up, from implemented rules only
    inert: tuple[str, ...] = field(default=())  # hooks supplied but not yet live
    flagged: bool = False  # a significant own-baseline finding

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        """Ascending order is display order: trigger level, total, then the flag (below a trigger
        only), then id."""
        level = _LEVEL[self.news2.risk]
        return (-level, -self.news2.total, 0 if self.flagged and level == 0 else 1, self.patient_id)

    def escalation_reasons(self) -> list[EscalationReason]:
        return [EscalationReason(rule=r.rule, text=r.text, pathway=r.pathway) for r in self.reasons]


@dataclass(frozen=True, slots=True)
class NotRanked:
    patient_id: str
    problems: tuple[InputProblem, ...]
    reason: str
    instruction: str
    inert: tuple[str, ...] = field(default=())


Decision = Ranked | NotRanked


@dataclass(frozen=True, slots=True)
class WardRanking:
    ranked: tuple[tuple[int, Ranked], ...]  # (rank, decision), ranks 1..n with no gaps
    not_ranked: tuple[NotRanked, ...]


def _inert(
    tier_c: TierCSignal | None,
    policy: PolicySignal | None,
    notes: NotesSignal | None,
    worried: WorriedPin | None,
    guarantees: Guarantees | None,
) -> tuple[str, ...]:
    supplied = (
        ("rule_2_tier_c", tier_c),
        ("learned_policy", policy),
        ("rule_4_notes", notes),
        ("rule_4_worried", worried),
        ("guarantees", guarantees),
    )
    return tuple(name for name, value in supplied if value is not None)


def _not_ranked_text(problems: tuple[InputProblem, ...]) -> tuple[str, str]:
    names = {p.input for p in problems}
    device_missing = names & set(_DEVICE)
    parts: list[str] = []
    steps: list[str] = []
    if device_missing == set(_DEVICE):
        parts.append("No device data")
        steps.append(_NO_DEVICE_INSTRUCTION)
    elif device_missing:
        for key, label in _DEVICE.items():
            problem = next((p for p in problems if p.input == key), None)
            if problem is not None:
                parts.append(f"{label} {problem.problem.value}")
        steps.append(_DEVICE_INSTRUCTION)
    for key, (label, instruction) in _NURSE_ENTRY.items():
        problem = next((p for p in problems if p.input == key), None)
        if problem is not None:
            parts.append(f"{label} {problem.problem.value}")
            steps.append(
                instruction if problem.problem is Problem.missing else f"Re-enter {label.lower()}"
            )
    return ", ".join(parts), "; ".join(dict.fromkeys(steps))


def _span(bins: int) -> str:
    hours, minutes = divmod(bins * 15, 60)
    parts = (f"{hours} h" if hours else "", f"{minutes} min" if minutes else "")
    return " ".join(p for p in parts if p)


def arbitrate(
    patient_id: str,
    tier_a: News2Result,
    *,
    tier_b: TierBSignal | None = None,
    tier_c: TierCSignal | None = None,
    policy: PolicySignal | None = None,
    notes: NotesSignal | None = None,
    worried: WorriedPin | None = None,
    guarantees: Guarantees | None = None,
) -> Decision:
    """One patient's decision. Only `tier_a` and `tier_b` can change it in v1."""
    inert = _inert(tier_c, policy, notes, worried, guarantees)
    if isinstance(tier_a, News2Abstained):
        reason, instruction = _not_ranked_text(tier_a.problems)
        return NotRanked(patient_id, tier_a.problems, reason, instruction, inert)

    reasons: list[ArbiterReason] = []
    escalated = tier_a.risk in _ESCALATING
    if escalated:
        if tier_a.risk in (Risk.medium, Risk.high):  # a total of 5 or more crossed a band
            reasons.append(ArbiterReason(EscalationRule.tier_a_threshold, tier_a.total_reason))
        if tier_a.trigger_reason is not None:
            reasons.append(ArbiterReason(EscalationRule.tier_a_threshold, tier_a.trigger_reason))
    findings = () if tier_b is None or isinstance(tier_b.findings, NotComputed) else tier_b.findings
    for finding, deviation in findings:
        if finding.significant:
            text = (
                f"Drifting from own baseline: {_NAME[deviation.vital]},"
                f" {_span(deviation.run_bins)} (synthetic, not validated)"
            )
            reasons.append(ArbiterReason(EscalationRule.tier_b_finding, text, finding.pathway))
    flagged = any(r.rule is EscalationRule.tier_b_finding for r in reasons)
    return Ranked(patient_id, tier_a, escalated, tuple(reasons), inert, flagged)


def rank_ward(decisions: Iterable[Decision]) -> WardRanking:
    """Order a ward's decisions. Rank is relative to the set given (never cache it)."""
    items = list(decisions)
    ranked = sorted((d for d in items if isinstance(d, Ranked)), key=lambda d: d.sort_key)
    not_ranked = sorted((d for d in items if isinstance(d, NotRanked)), key=lambda d: d.patient_id)
    return WardRanking(
        ranked=tuple((i + 1, d) for i, d in enumerate(ranked)),
        not_ranked=tuple(not_ranked),
    )
