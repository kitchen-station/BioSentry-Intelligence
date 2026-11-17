"""Aggregates of one unit's last hours for a caller nobody identified: counts per hour and per
12-hour shift, and finished stays by whole hours of stay (never a stay's own times). Nothing here carries a person, bed, id or name: the
stays are folded into numbers before they leave this module. NEWS2 is rebuilt from stored bins
(`as_of.news2_as_of`), never stored. Research code: synthetic data only, not validated."""

from bisect import bisect_right
from collections import Counter
from itertools import accumulate
from datetime import datetime, timedelta
from typing import Any

from biosentry.domain.scoring.tier_a import News2Abstained
from biosentry.repositories.postgres import DEVICE_VITALS, PostgresStore, whole
from biosentry.schemas.analytics import (
    K,
    SHIFT_MINUTES,
    AnalyticsHour,
    AnalyticsShift,
    News2Counts,
    OpenWardAnalytics,
    StayLength,
)

from .as_of import news2_as_of
from .scoring import BIN

Scored = tuple[int, bool]  # NEWS2 total, and whether it is an alert (5 or more, or a single 3)


def hide(n: int) -> int | None:
    """k-anonymity for a count: 0 and K or more are exact, 1 to K - 1 are not served."""
    return n if n == 0 or n >= K else None


def open_analytics(store: PostgresStore, unit: str, hours: int) -> OpenWardAnalytics:
    end = whole(store.now())
    since = end - timedelta(hours=hours)
    origin = store.origin

    def minute(t: datetime) -> float:
        return (t - origin).total_seconds() / 60

    stays, bins, rows = store.analytics_inputs(unit, since, end)
    scored: dict[str, tuple[list[datetime], list[Scored | None], list[int]]] = {}  # bins in order, their scores, alerts so far
    if store.units[unit]["kind"] == "ranked":  # a shown or unranked unit has no score to count
        for pid, by_bin in bins.items():
            times = sorted(by_bin)  # once per stay: the hours below bisect it
            found: list[Scored | None] = []
            for at in times:
                rebuilt = news2_as_of({v: by_bin[at].get(v) for v in DEVICE_VITALS}, rows[pid], at, earlier=True)
                result = None if rebuilt is None else rebuilt.result
                found.append(
                    None
                    if result is None or isinstance(result, News2Abstained)
                    else (result.total, result.total >= 5 or bool(result.single_threes))
                )
            scored[pid] = (times, found, [0, *accumulate(bool(v and v[1]) for v in found)])

    def at_hand(s: dict[str, Any], t: datetime) -> bool:
        return (s["admitted_at"] is None or s["admitted_at"] <= t) and (s["left_at"] is None or s["left_at"] > t)

    hourly = []
    for i in range(hours):
        t = end - timedelta(hours=hours - 1 - i)
        f = t - timedelta(hours=1)
        level = Counter[str]()
        here = [s for s in stays if at_hand(s, t)]
        for s in here:
            times, values, _ = scored.get(s["id"], ([], [], [0]))
            i = bisect_right(times, t)  # the newest bin at or before t, if it ends in the last 15 minutes
            found = values[i - 1] if i and times[i - 1] > t - BIN else None
            level["not_ranked" if found is None else "zero_to_four" if found[0] < 5 else "five_to_six" if found[0] < 7 else "seven_plus"] += 1
        arrived = [s for s in stays if s["admitted_at"] is not None and f < s["admitted_at"] <= t]
        hourly.append(
            AnalyticsHour(
                from_minute=minute(f),
                to_minute=minute(t),
                beds_in_use=len(here),
                admitted=sum(not s["transfer_in"] for s in arrived),
                transferred_in=sum(bool(s["transfer_in"]) for s in arrived),
                left=sum(s["left_at"] is not None and f < s["left_at"] <= t for s in stays),
                news2=News2Counts(**{k: level[k] for k in News2Counts.model_fields}),
            )
        )

    shifts = []
    k = int(minute(since) // SHIFT_MINUTES)
    while k * SHIFT_MINUTES < minute(end):
        f = max(origin + timedelta(minutes=k * SHIFT_MINUTES), since)
        t = min(origin + timedelta(minutes=(k + 1) * SHIFT_MINUTES), end)
        alerts = sum(c[bisect_right(times, t)] - c[bisect_right(times, f)] for times, _, c in scored.values())
        ended = Counter(s["outcome"] for s in stays if s["left_at"] is not None and f < s["left_at"] <= t)
        seconds = sum(
            max(0.0, (min(s["left_at"] or t, t) - max(s["admitted_at"] or f, f)).total_seconds()) for s in stays
        )
        shifts.append(AnalyticsShift(
                from_minute=minute(f), to_minute=minute(t), alerts=alerts, patient_minutes=seconds / 60,
                discharged=hide(ended["discharged"]), transferred_out=hide(ended["transferred"]), died=hide(ended["died"]),
            ))
        k += 1

    length = Counter(  # whole hours of stay; the last bucket holds longer stays; an unknown arrival has no length
        min(int((s["left_at"] - s["admitted_at"]).total_seconds() // 3600), hours - 1)
        for s in stays
        if s["admitted_at"] is not None and s["left_at"] is not None and since < s["left_at"] <= end
    )
    return OpenWardAnalytics(
        synthetic=True,
        origin=origin,
        hourly=hourly,
        shifts=shifts,
        length_of_stay=[StayLength(from_hours=h, count=hide(length[h])) for h in range(hours)],
    )
