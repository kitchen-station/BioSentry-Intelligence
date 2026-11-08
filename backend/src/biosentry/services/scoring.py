"""One patient's decision, computed from their stored vitals on every read."""

from biosentry.domain.arbiter import NotRanked, Ranked, arbitrate
from biosentry.domain.scoring.tier_a import inputs_from_vitals, score_news2
from biosentry.schemas import UnscoredPatient

Decision = Ranked | NotRanked


def decide(state: UnscoredPatient) -> Decision:
    """NEWS2, then the arbiter. Pure: nothing is cached, so a recorded value re-ranks at once."""
    return arbitrate(state.id, score_news2(inputs_from_vitals(state.vitals)))
