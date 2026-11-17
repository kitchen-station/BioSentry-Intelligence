"""Anonymous, synthetic-only ward aggregates (GET /wards/{ward}/open-view/analytics).
Minutes are relative to the run's origin. Counts only: no person, bed, id or name."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from .base import Contract, NonNegInt, NonNegNum, Num

HOURS_MAX = 168
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
    discharged: NonNegInt
    transferred_in: NonNegInt
    transferred_out: NonNegInt
    news2: News2Counts


class AnalyticsShift(Contract):
    """Run-relative 12-hour shifts, clipped to the requested window. Alerts count scored
    patient bins ending in (from, to] with NEWS2 >=5 or a single parameter at 3, not runs of them.
    Patient-minutes sum stay overlap, including unranked patients. Unknown arrival times
    contribute from the window start; they are not counted as admissions."""

    from_minute: Num
    to_minute: Num
    alerts: NonNegInt
    patient_minutes: NonNegNum


class FinishedStayCount(Contract):
    """Finished stays grouped by arrival/departure minute, never individual identifiers.
    Includes every exit outcome in the window; an unknown arrival remains null."""

    in_minute: Num | None
    out_minute: Num
    count: NonNegInt


class OpenWardAnalytics(Contract):
    """Last hours of ward time, oldest first, with zero-filled buckets. Transfers in are
    arrivals linked by person, time and destination to a transferred stay in this run;
    other known arrivals are admissions. Discharged counts only the discharged outcome."""

    synthetic: Literal[True]
    origin: AwareDatetime
    hourly: Annotated[list[AnalyticsHour], Field(max_length=HOURS_MAX)]
    shifts: Annotated[list[AnalyticsShift], Field(max_length=HOURS_MAX // 12 + 1)]
    finished_stays: list[FinishedStayCount]
