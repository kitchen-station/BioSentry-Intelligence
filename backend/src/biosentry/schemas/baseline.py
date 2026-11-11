"""Change from the patient's own baseline (Tier B), as the dashboard reads it. Display only:
nothing here feeds the rank, the arbiter or storage.

Stored values, served exactly: `latest`. Everything else is derived: `baseline_median`, `delta` and
`z` are rounded to one decimal; `since`, `latest_at` and `baseline_from` are bin times (served
time, like the patient's history); `run_bins`, `significant`, `direction` come from the detector.
The window is the first 24 valid bins from admission when it is known (`admission_known`), from
the first stored bin when it is not, and from the oldest bin still kept once the server's 7-day
prune or the 672-bin cap has dropped earlier ones: a long stay is baselined on mid-stay data."""

from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, Flag, NonNegInt, Num
from .tiers import FindingDirection

BaselineVital = Literal["heart_rate", "respiratory_rate", "spo2", "temperature"]
BASELINE_LABEL: Final = "Research finding. Synthetic, not validated. Does not change the rank."
# Fixed text only: no stored free text can reach this response, and each string is capped.
Short = Annotated[str, Field(max_length=32)]


class BaselineUsed(Contract):
    """The detector's own defaults, echoed. Nothing in a request can set them."""

    window_bins: NonNegInt
    k: Num
    p: NonNegInt


class BaselineComputed(Contract):
    vital: BaselineVital
    status: Literal["computed"]
    baseline_median: Num  # derived, 1 decimal
    latest: Num  # the stored value, exactly
    latest_at: AwareDatetime  # when `latest` was taken: it may be old
    baseline_from: AwareDatetime  # the first bin of the window the baseline was formed from
    delta: Num  # derived, 1 decimal
    z: Num  # derived, 1 decimal
    run_bins: NonNegInt
    since: AwareDatetime | None  # start of the current run past k; null when there is none
    significant: Flag
    direction: FindingDirection | None
    pathway: Short | None
    unit: Short


class BaselineForming(Contract):
    vital: BaselineVital
    status: Literal["forming"]
    bins_valid: NonNegInt
    bins_needed: NonNegInt


class BaselineUnavailable(Contract):
    vital: BaselineVital
    status: Literal["unavailable"]
    reason: Literal["no_history", "latest_bin_missing"]


class BaselineResponse(Contract):
    patient_id: Annotated[str, Field(max_length=64)]
    synthetic: Flag
    admission_known: Flag  # false: the window starts at the first stored bin, not at admission
    label: Literal["Research finding. Synthetic, not validated. Does not change the rank."]
    params: BaselineUsed
    vitals: Annotated[
        list[
            Annotated[
                BaselineComputed | BaselineForming | BaselineUnavailable,
                Field(discriminator="status"),
            ]
        ],
        Field(max_length=4),
    ]
