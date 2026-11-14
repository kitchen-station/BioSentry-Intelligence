"""Change from the patient's own baseline (Tier B), as the dashboard reads it. Display only:
nothing here feeds the rank, the arbiter or storage.

Stored values, served exactly: `latest`, which is the latest 15-minute MEDIAN bin the simulator
stored, not a single reading. Everything else is derived: `baseline_median`, `delta`, `z`,
`band_low` and `band_high` (the median minus and plus k spreads) are rounded to one decimal;
`since`, `latest_at` and `baseline_from` are bin times (served time, like the patient's history); `run_bins`, `significant`, `direction` come from the detector.
The window is the first 24 valid bins from admission when it is known (`admission_known`), from
the first stored bin when it is not, and from the oldest bin still kept once the server's 7-day
prune or the 672-bin cap has dropped earlier ones: a long stay is baselined on mid-stay data.
`anchored_at` says which, per vital. `latest_age_s` is measured on the server's clock (the one
`last_update_s` uses) from the END of the latest bin, so the page never uses its own clock."""

from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, Flag, NonNegInt, Num
from .tiers import FindingDirection

BaselineVital = Literal["heart_rate", "respiratory_rate", "spo2", "temperature"]
BASELINE_LABEL: Final = "Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger."
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
    latest: Num  # the stored value, exactly: the latest 15-minute median bin
    latest_at: AwareDatetime  # the START of that bin: it may be old
    latest_age_s: NonNegInt  # server now minus the bin's end (latest_at + 15 min), never negative
    baseline_from: AwareDatetime  # the first bin of the window the baseline was formed from
    # "admission": that bin starts within 30 minutes of the admission time. Otherwise the window
    # starts at the earliest bin kept, which may be mid-stay.
    anchored_at: Literal["admission", "first_kept_bin"]
    delta: Num  # derived, 1 decimal
    z: Num  # derived, 1 decimal
    band_low: Num  # derived, 1 decimal: baseline_median - k x spread (k in `params`)
    band_high: Num  # derived, 1 decimal: baseline_median + k x spread
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
    reason: Literal["no_history", "latest_bin_missing", "patch_not_live"]


class BaselineResponse(Contract):
    patient_id: Annotated[str, Field(max_length=64)]
    synthetic: Flag
    admission_known: Flag  # false: the window starts at the first stored bin, not at admission
    label: Literal["Research finding. Synthetic, not validated. Breaks ties between equal NEWS2 totals below a trigger."]
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
