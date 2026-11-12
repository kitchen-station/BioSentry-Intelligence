"""Re-score cohort rows with the backend's NEWS2 (imported, never copied)."""

from functools import lru_cache

from biosentry.domain.scoring.tier_a import (
    News2Abstained,
    News2Inputs,
    News2Result,
    News2Score,
    score_news2,
)
from biosentry.schemas import Acvpu, Spo2Scale

from .trials import Row, Trial, TrialError


@lru_cache(maxsize=1 << 16)
def _score(inputs: News2Inputs) -> News2Result:
    return score_news2(inputs)


def score_row(row: Row) -> News2Result:
    """The inputs the sim's scorer used, as the backend builds them from stored vitals."""
    return _score(
        News2Inputs(
            respiratory_rate=row.respiratory_rate,
            spo2=row.spo2,
            spo2_scale=Spo2Scale(row.spo2_scale),
            supplemental_o2=row.on_oxygen,
            systolic_bp=row.systolic,
            heart_rate=row.heart_rate,
            consciousness=None if row.consciousness is None else Acvpu(row.consciousness),
            temperature=row.temperature,
        )
    )


def check_parity(trial: Trial) -> int:
    """Every row must score as the sim did (ranked and total). Returns the rows checked."""
    for r in trial.rows:
        got = score_row(r)
        ranked, total = isinstance(got, News2Score), got.total if isinstance(got, News2Score) else None
        if (ranked, total) != (r.ranked, r.news2):
            sim = f"ranked {int(r.ranked)} news2 {r.news2}"
            back = "NOT RANKED" if isinstance(got, News2Abstained) else f"news2 {total}"
            raise TrialError(
                f"{trial.name}: parity mismatch at patient {r.patient} minute {r.minute}: "
                f"sim {sim}, backend {back}"
            )
    return len(trial.rows)
