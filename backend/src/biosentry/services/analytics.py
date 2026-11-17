"""Aggregates of one unit's last hours for a caller nobody identified: counts per hour and per
12-hour shift, and finished stays by minute. Nothing here carries a person, bed, id or name: the
stays are folded into numbers before they leave this module. NEWS2 is rebuilt from stored bins
(`as_of.news2_as_of`), never stored. Research code: synthetic data only, not validated."""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from biosentry.domain.scoring.tier_a import News2Abstained
from biosentry.repositories.postgres import DEVICE_VITALS, PostgresStore, whole
from biosentry.schemas.analytics import (
    SHIFT_MINUTES,
    AnalyticsHour,
    AnalyticsShift,
    FinishedStayCount,
    News2Counts,
    OpenWardAnalytics,
)

from .as_of import news2_as_of
from .scoring import BIN

Scored = tuple[int, bool]  # NEWS2 total, and whether it is an alert (5 or more, or a single 3)


def open_analytics(store: PostgresStore, unit: str, hours: int) -> OpenWardAnalytics:
    end = whole(store.now())
    since = end - timedelta(hours=hours)
    origin = store.origin

    def minute(t: datetime) -> float:
        return (t - origin).total_seconds() / 60

    stays, bins, rows = store.analytics_inputs(unit, since, end)
    scored: dict[str, dict[datetime, Scored | None]] = {}
    if store.units[unit]["kind"] == "ranked":  # a shown or unranked unit has no score to count
        for pid, by_bin in bins.items():
            scored[pid] = {}
            for at, values in by_bin.items():
                rebuilt = news2_as_of({v: values.get(v) for v in DEVICE_VITALS}, rows[pid], at, earlier=True)
                result = None if rebuilt is None else rebuilt.result
                scored[pid][at] = (
                    None
                    if result is None or isinstance(result, News2Abstained)
                    else (result.total, result.total >= 5 or bool(result.single_threes))
                )

    def at_hand(s: dict[str, Any], t: datetime) -> bool:
        return (s["admitted_at"] is None or s["admitted_at"] <= t) and (s["left_at"] is None or s["left_at"] > t)

    hourly = []
    for i in range(hours):
        t = end - timedelta(hours=hours - 1 - i)
        f = t - timedelta(hours=1)
        level = Counter[str]()
        here = [s for s in stays if at_hand(s, t)]
        for s in here:
            near = [b for b in scored.get(s["id"], {}) if t - BIN < b <= t]
            found = scored[s["id"]][max(near)] if near else None
            level["not_ranked" if found is None else "zero_to_four" if found[0] < 5 else "five_to_six" if found[0] < 7 else "seven_plus"] += 1
        arrived = [s for s in stays if s["admitted_at"] is not None and f < s["admitted_at"] <= t]
        left = [s for s in stays if s["left_at"] is not None and f < s["left_at"] <= t]
        hourly.append(
            AnalyticsHour(
                from_minute=minute(f),
                to_minute=minute(t),
                beds_in_use=len(here),
                admitted=sum(not s["transfer_in"] for s in arrived),
                discharged=sum(s["outcome"] == "discharged" for s in left),
                transferred_in=sum(bool(s["transfer_in"]) for s in arrived),
                transferred_out=sum(s["outcome"] == "transferred" for s in left),
                news2=News2Counts(**{k: level[k] for k in News2Counts.model_fields}),
            )
        )

    shifts = []
    k = int(minute(since) // SHIFT_MINUTES)
    while k * SHIFT_MINUTES < minute(end):
        f = max(origin + timedelta(minutes=k * SHIFT_MINUTES), since)
        t = min(origin + timedelta(minutes=(k + 1) * SHIFT_MINUTES), end)
        alerts = sum(
            bool(v and v[1]) for by_bin in scored.values() for b, v in by_bin.items() if f < b <= t
        )
        seconds = sum(
            max(0.0, (min(s["left_at"] or t, t) - max(s["admitted_at"] or f, f)).total_seconds()) for s in stays
        )
        shifts.append(AnalyticsShift(from_minute=minute(f), to_minute=minute(t), alerts=alerts, patient_minutes=seconds / 60))
        k += 1

    finished = Counter(
        (None if s["admitted_at"] is None else minute(s["admitted_at"]), minute(s["left_at"]))
        for s in stays
        if s["left_at"] is not None and s["left_at"] <= end
    )
    return OpenWardAnalytics(
        synthetic=True,
        origin=origin,
        hourly=hourly,
        shifts=shifts,
        finished_stays=[
            FinishedStayCount(in_minute=i, out_minute=o, count=n)
            for (i, o), n in sorted(finished.items(), key=lambda kv: (kv[0][1], kv[0][0] is not None, kv[0][0] or 0))
        ],
    )
