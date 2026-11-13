"""Tier status, finding directions, Tier C input and escalation reasons.

I3 tier separation: Tier C input (TierCInput) is built only from raw and derived features.
It has no field that can carry a Tier A score, a Tier B finding or an arbiter reason.
"""

from enum import StrEnum

from pydantic import AwareDatetime

from .base import Contract, Flag, NonNegInt
from .clinical import LabPanel, Vitals, VitalTrends
from .common import Tier


class TierStatus(Contract):
    tier: Tier
    live: Flag
    label: str


class FindingDirection(StrEnum):
    rising = "rising"
    falling = "falling"
    stable = "stable"
    new = "new"


class TierCInput(Contract):
    """Everything Tier C may read. Features in, never verdicts (README: fuse inputs)."""

    patient_id: str
    as_of: AwareDatetime
    age: NonNegInt | None
    sex: str | None
    admitting_context: str
    vitals: Vitals
    vital_trends: VitalTrends
    labs: LabPanel
    synthetic: Flag


class EscalationRule(StrEnum):
    tier_a_threshold = "tier_a_threshold"
    tier_c_raise = "tier_c_raise"
    tier_b_finding = "tier_b_finding"
    missing_input = "missing_input"
    prediction_model_flag = "prediction_model_flag"
    alert_policy_flag = "alert_policy_flag"


class EscalationReason(Contract):
    rule: EscalationRule
    text: str
    pathway: str | None = None  # for tier_b_finding: which mechanism
