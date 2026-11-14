"""The whole ward for a physician or charge nurse, one row per patient, in the NEWS2 order. Display only:
every number is one the API already computes. A patient who is not the reader's has a bed label and
nothing else that names them: no id, no name. Synthetic data, not validated."""

from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, Flag, NonNegInt
from .baseline import BaselineComputed, BaselineFlagged, BaselineForming, BaselineUnavailable
from .rows import RowModels

RESEARCH_LABEL: Final = (
    "Each patient against their own baseline. Research view: synthetic, not validated. NEWS2 alone orders the list."
)
Id = Annotated[str, Field(max_length=64)]


class AlertCounted(Contract):
    status: Literal["counted"]
    bins: NonNegInt  # 15-minute sets at a NEWS2 alert level, over the stay
    minutes: NonNegInt  # bins x 15
    ongoing: Flag  # the latest set is at an alert level


class AlertUnavailable(Contract):
    status: Literal["unavailable"]
    reason: Literal["no_bins", "past_cap", "not_aligned"]  # the history's own reason


class ResearchRow(Contract):
    bed: str
    assigned: Flag
    id: Id | None  # null when the patient is not the reader's
    name: str | None  # null when the patient is not the reader's
    rank: Annotated[int, Field(strict=True, ge=1)]
    score: Annotated[int, Field(strict=True, ge=0, le=20)]  # NEWS2 total
    level: Literal["low", "low_medium", "medium", "high"]  # the NEWS2 band
    # A patient who is not the reader's has only the flag of a computed vital (status flag_only), never a number.
    baseline: Annotated[
        list[
            Annotated[
                BaselineComputed | BaselineFlagged | BaselineForming | BaselineUnavailable, Field(discriminator="status")
            ]
        ],
        Field(max_length=4),
    ]
    models: RowModels
    alert: Annotated[AlertCounted | AlertUnavailable, Field(discriminator="status")]


class ResearchAbstained(Contract):
    """NOT RANKED: the arbiter's reason and instruction, no score, no rank."""

    bed: str
    assigned: Flag
    id: Id | None
    name: str | None
    reason: str
    instruction: str


class WardResearch(Contract):
    ward: str
    now: AwareDatetime  # the time every value is measured at
    synthetic: Literal[True]
    label: Literal[
        "Each patient against their own baseline. Research view: synthetic, not validated. NEWS2 alone orders the list."
    ]
    ranked: list[ResearchRow]
    abstained: list[ResearchAbstained]
