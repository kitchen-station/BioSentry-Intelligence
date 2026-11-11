"""Alert policies and the one-open-alert rule (research-models.md section 4)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from biosentry.domain.arbiter import arbitrate
from biosentry.domain.scoring.tier_a import News2Score
from biosentry.domain.scoring.tier_a.tables import MEDIUM_FROM

from .rescore import score_row
from .trials import BIN_MINUTES, Row

REARM_BINS = 16  # 4 hours of not alerting; a judgement, the same for every policy


@dataclass(frozen=True, slots=True)
class Policy:
    """Later slices add a row by adding a Policy; nothing else changes."""

    name: str
    alerting: Callable[[Row], bool]
    abstains: Callable[[Row], bool]


def _chart_trigger(row: Row) -> bool:
    # The product's own arbiter decides, so this is exactly what the API escalates.
    score = score_row(row)
    return isinstance(score, News2Score) and arbitrate(row.patient, score).escalated


def _total_at_least_5(row: Row) -> bool:
    score = score_row(row)
    return isinstance(score, News2Score) and score.total >= MEDIUM_FROM


def _not_ranked(row: Row) -> bool:
    return not row.ranked


POLICIES = (
    Policy("news2_chart_trigger", _chart_trigger, _not_ranked),
    Policy("news2_total_ge_5", _total_at_least_5, _not_ranked),
)


def scan(policy: Policy, rows: Mapping[int, Row]) -> tuple[list[int], list[int]]:
    """One patient: the minutes at which `policy` opens an alert, and the minutes it is alerting.

    A bin with no row counts as not alerting, as does a NOT RANKED row (the policy says False).
    """
    fired: list[int] = []
    on: list[int] = []
    quiet = REARM_BINS  # consecutive not-alerting bins; starts armed
    previous: int | None = None
    for minute in sorted(rows):
        if previous is not None:
            quiet += (minute - previous) // BIN_MINUTES - 1
        previous = minute
        if policy.alerting(rows[minute]):
            on.append(minute)
            if quiet >= REARM_BINS:
                fired.append(minute)
            quiet = 0
        else:
            quiet += 1
    return fired, on


def fire_alerts(policy: Policy, rows: Mapping[int, Row]) -> list[int]:
    return scan(policy, rows)[0]
