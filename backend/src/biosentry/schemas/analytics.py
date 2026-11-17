"""Anonymous, synthetic-only ward aggregates (GET /wards/{ward}/open-view/analytics).
Minutes are relative to the run's origin. Counts only: no person, bed, id, name, or a time that belongs
to one stay (no arrival or departure minute of a stay is served, so a bed card read now cannot be joined to
a count here).

Small-cell rule, k = 5: an outcome count (a shift's discharged, transferred_out, died) and a length-of-stay
count of 1 to 4 is served as null. 0 stays 0 and 5 or more is exact."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, NonNegInt, NonNegNum, Num

HOURS_MAX = 168
K = 5  # a count of 1 to K - 1 is hidden
SHIFT_MINUTES = 720


class News2Counts(Contract):
    zero_to_four: NonNegInt
    five_to_six: NonNegInt
    seven_plus: NonNegInt
    not_ranked: NonNegInt


class AnalyticsHour(Contract):
    """One hour ending at to_minute. Events use (from, to]; occupancy and NEWS2 are
    snapshots at to. NEWS2 uses the latest stored bin ending in the preceding 15 minutes;
    absent, unrebuildable or unranked-unit scores count as not_ranked."""

    from_minute: Num
    to_minute: Num
    beds_in_use: NonNegInt
    admitted: NonNegInt
    transferred_in: NonNegInt
    left: NonNegInt  # whatever the outcome: the split is per shift, and hidden when small
    news2: News2Counts


class AnalyticsShift(Contract):
    """Run-relative 12-hour shifts, clipped to the requested window. Alerts count scored
    patient bins ending in (from, to] with NEWS2 >=5 or a single parameter at 3, not runs of them.
    Patient-minutes sum stay overlap, including unranked patients. Unknown arrival times
    contribute from the window start; they are not counted as admissions. Discharged, transferred_out
    and died count the stays that ended in (from, to]; null when 1 to 4 (k = 5)."""

    from_minute: Num
    to_minute: Num
    alerts: NonNegInt
    patient_minutes: NonNegNum
    discharged: NonNegInt | None
    transferred_out: NonNegInt | None
    died: NonNegInt | None


class StayLength(Contract):
    """Finished stays in the window whose length is from_hours to from_hours + 1 whole hours; the last
    bucket also holds longer stays. A stay with an unknown arrival has no length and is in none. No
    arrival or departure time. Null when 1 to 4 stays (k = 5)."""

    from_hours: NonNegInt
    count: NonNegInt | None


class OpenWardAnalytics(Contract):
    """Last hours of ward time, oldest first, with zero-filled buckets. Transfers in are
    arrivals linked by person, time and destination to a transferred stay in this run;
    other known arrivals are admissions."""

    synthetic: Literal[True]
    origin: AwareDatetime
    hourly: Annotated[list[AnalyticsHour], Field(max_length=HOURS_MAX)]
    shifts: Annotated[list[AnalyticsShift], Field(max_length=HOURS_MAX // 12 + 1)]
    length_of_stay: Annotated[list[StayLength], Field(max_length=HOURS_MAX)]
