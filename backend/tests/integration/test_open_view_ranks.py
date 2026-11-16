"""The public unit view ranks a ranked unit from the same inputs as a signed-in view: a patient whose own
baseline has drifted (a finding built from the whole stored history) ranks ahead of an equal NEWS2 patient
whose baseline has not. A read that left the history out would lose the finding and the order with it."""

from datetime import timedelta
from typing import Any

from test_open_view import watch  # noqa: F401 (watch: the API with the setting on)
from test_store import Api, api, obs  # noqa: F401 (api: fixture)
from test_units import hospital, stay

from biosentry.domain.arbiter import Ranked, rank_ward
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.services.scoring import decide

CALM_BINS = [66.0, 70.0, 74.0] * 9  # 27 bins: median 70, spread 1.4826 * 4
RISEN = [*CALM_BINS[:24], 110.0, 112.0, 114.0]  # the same 24 baseline bins, then three high ones (a run of 3)


def hr_bins(pid: str, values: list[float]) -> list[dict[str, Any]]:
    last = 15 * (len(values) - 1)
    return [{"patient_id": pid, "vital": "heart_rate", "minute": 15 * i - last, "value": v} for i, v in enumerate(values)]


def calm(pid: str) -> list[dict[str, Any]]:
    """Every NEWS2 input in range: a total of 0, below a trigger, where the flag orders patients."""
    device = {"heart_rate": 70, "respiratory_rate": 14, "spo2": 97, "temperature": 36.8}
    return [
        *(obs(pid, code, -2, num=value) for code, value in device.items()),
        obs(pid, "blood_pressure", -10, "nurse", num=120, num2=80),
        obs(pid, "consciousness", -10, "nurse", txt="A"),
        obs(pid, "supplemental_o2", -10, "nurse", num=0),
        obs(pid, "spo2_scale", -10, "nurse", num=1),
    ]


def ward(drifting: str) -> list[dict[str, Any]]:
    """Two W4 patients with the same NEWS2 inputs (a total of 0); only `drifting` has a drifted own baseline."""
    both = [stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"), stay("w-2", "W4", "rn-reyes", None, "4B")]
    return [{**p, "observations": calm(p["id"]), "bins": hr_bins(p["id"], RISEN if p["id"] == drifting else CALM_BINS)} for p in both]


def beds(watch: Api) -> dict[str, int | None]:
    body = watch.client.get("/wards/W4/open-view").json()
    return {b["bed"]: b["rank"] for b in body["beds"]}


def whole(api: Api) -> dict[str, int]:
    """What ranking the full stored history gives: the reference."""
    store = PostgresStore(connect(api.url), api.wall)
    states = [s for s in (store.read(pid) for pid in ("w-1", "w-2")) if s]
    return {d.patient_id: rank for rank, d in rank_ward(decide(s) for s in states).ranked}


def test_the_open_view_ranks_like_the_whole_history_and_a_drifted_baseline_goes_first(watch: Api) -> None:
    for drifting, first_bed in (("w-2", "4B"), ("w-1", "4A")):
        hospital(watch, ward(drifting))
        reference = whole(watch)
        assert reference[drifting] == 1  # the finding counts: the test would be empty if it did not
        assert beds(watch) == {"4A": reference["w-1"], "4B": reference["w-2"]}
        assert beds(watch)[first_bed] == 1
        watch.wall.now += timedelta(seconds=31)  # the next run may replace this one


def test_the_short_read_would_have_lost_the_finding(watch: Api) -> None:
    hospital(watch, ward("w-2"))
    store = PostgresStore(connect(watch.url), watch.wall)
    short, full = store.read("w-2", False), store.read("w-2")
    assert short and full
    drifted, lost = decide(full), decide(short)
    assert isinstance(drifted, Ranked) and drifted.flagged  # the finding is built from the whole history
    assert isinstance(lost, Ranked) and not lost.flagged  # and a read without it has none
