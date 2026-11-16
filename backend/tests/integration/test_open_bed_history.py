"""The bed card's history: the last 12 ward hours of the five vitals of a RANKED bed, for a caller
nobody identified. 15-minute values (a gap stays a null), each with its time and source, and the
charted blood pressures. No name, id or score is on a point; the answer is capped."""

from typing import Any

from test_open_view import watch  # noqa: F401 (watch: the API with the setting on)
from test_store import Api, api, batch, obs  # noqa: F401 (api: fixture)
from test_units import PATIENTS, hospital, stay

URL = "/wards/W4/open-view/beds/4A"
FOUR = ("heart_rate", "respiratory_rate", "spo2", "temperature")
POINT = {"time", "value", "source"}
PRESSURE = {"time", "systolic", "diastolic", "source"}


def card(api: Api, unit: str = "W4", bed: str = "4A") -> Any:
    response = api.client.get(f"/wards/{unit}/open-view/beds/{bed}")
    assert response.status_code == 200, response.text
    return response


def bins(pid: str, minutes: list[int], vital: str = "heart_rate") -> list[dict[str, Any]]:
    return [{"patient_id": pid, "vital": vital, "minute": m, "value": 100.0 + m} for m in minutes]


def with_bins(extra: list[dict[str, Any]], pid: str = "w-1") -> list[dict[str, Any]]:
    first = stay(pid, "W4", "rn-delacruz", "md-santos", "4A")
    return [{**first, "bins": [*first["bins"], *extra]}, *PATIENTS[1:]]


def test_the_history_holds_the_stored_bins_a_gap_stays_a_null_and_the_charted_pressure(watch: Api) -> None:
    hospital(watch)
    history = card(watch).json()["history"]
    assert history.keys() == {"hours", *FOUR, "blood_pressure"} and history["hours"] == 12
    stored = watch.sql("SELECT vital, bin_at, value FROM vital_bin WHERE patient_id = 'w-1' ORDER BY vital, bin_at")
    for vital in FOUR:
        points = history[vital]
        assert len(points) == 3 and all(p.keys() == POINT and p["source"] == "device" for p in points)
        assert [p["value"] for p in points] == [r["value"] for r in stored if r["vital"] == vital]
        assert None in [p["value"] for p in points]  # the -15 bin is a gap: kept, not zero, not filled
        assert [p["time"] for p in points] == sorted(p["time"] for p in points)
    [pressure] = history["blood_pressure"]
    assert pressure.keys() == PRESSURE and (pressure["systolic"], pressure["diastolic"], pressure["source"]) == (88, 52, "nurse")


def test_only_the_last_12_hours_of_ward_time_are_in_it(watch: Api) -> None:
    hospital(watch, with_bins(bins("w-1", [-800, -721, -719, -700])))
    times = {p["time"]: p["value"] for p in card(watch).json()["history"]["heart_rate"]}
    values = list(times.values())
    assert 100.0 - 800 not in values and 100.0 - 721 not in values  # older than 12 h: out
    assert 100.0 - 719 in values and 100.0 - 700 in values  # inside: in


def test_each_series_is_capped_at_49_points_the_newest_kept_and_the_answer_stays_small(watch: Api) -> None:
    hospital(watch, with_bins(bins("w-1", list(range(-300, -240)))))  # 60 more bins inside the window
    response = card(watch)
    points = response.json()["history"]["heart_rate"]
    assert len(points) == 49 and points[-1]["time"] == max(p["time"] for p in points)
    assert len(response.content) < 25_000


def test_a_point_holds_a_time_a_value_and_a_source_and_nothing_that_names_the_patient(watch: Api) -> None:
    marked = {**stay("PID-MARK", "W4", "rn-delacruz", "md-santos", "4A"), "name": "NAME-MARK", "context": "CONTEXT-MARK"}
    marked["observations"] = [{**o, "patient_id": "PID-MARK"} for o in marked["observations"]]
    marked["bins"] = [{**b, "patient_id": "PID-MARK"} for b in marked["bins"]]
    hospital(watch, [marked, *PATIENTS[1:]])
    response = card(watch)
    for mark in ("MARK", "Sim, Pat", "rn-delacruz", "md-santos", "w-1"):
        assert mark not in response.text, mark
    history = response.json()["history"]
    assert all(p.keys() == POINT for v in FOUR for p in history[v])
    assert all(p.keys() == PRESSURE for p in history["blood_pressure"])
    assert "score" not in str(history) and "rank" not in str(history)


def test_a_bed_that_is_not_ranked_and_the_er_and_icu_cards_have_no_history(watch: Api) -> None:
    gone = stay("w-2", "W4", "rn-delacruz", "md-santos", "4B")
    gone["observations"] = [*(o for o in gone["observations"] if o["code"] != "heart_rate"), obs("w-2", "heart_rate", -1, absent_reason="x")]
    hospital(watch, [*PATIENTS, gone])
    for unit, bed in (("W4", "4B"), ("ER", "E1"), ("ICU", "I1")):
        body = card(watch, unit, bed).json()
        assert body["state"] == "not_ranked" and body["history"] is None and body["vitals"] is None, (unit, bed)


def test_a_ranked_bed_with_no_stored_bin_has_empty_series_not_an_error(watch: Api) -> None:
    quiet = {**stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"), "bins": []}
    hospital(watch, [quiet, *PATIENTS[1:]])
    history = card(watch).json()["history"]
    assert [history[v] for v in FOUR] == [[]] * 4 and len(history["blood_pressure"]) == 1


def test_a_reading_exactly_12_hours_old_is_out_and_one_minute_younger_is_in(watch: Api) -> None:
    pressures = [obs("w-1", "blood_pressure", m, "nurse", num=s, num2=60) for m, s in ((-720, 141), (-719, 142))]
    first = stay("w-1", "W4", "rn-delacruz", "md-santos", "4A")
    patients = [{**first, "bins": [*first["bins"], *bins("w-1", [-720, -719])], "observations": [*first["observations"], *pressures]}]
    hospital(watch, [*patients, *PATIENTS[1:]])
    history = card(watch).json()["history"]
    heart = [p["value"] for p in history["heart_rate"]]
    assert 100.0 - 720 not in heart and 100.0 - 719 in heart  # a bin stamped at the edge holds older time
    systolic = [p["systolic"] for p in history["blood_pressure"]]
    assert 142 in systolic and 141 not in systolic


def moved_to_med3a(api: Api, earlier: list[int], pressures: list[int]) -> Any:
    """A person of W4 admitted to MED3A at minute 30, whose runner also sent bins and pressures stamped
    `earlier` minutes (the contract lets it send a moved person's readings with their original minutes)."""
    run_id = hospital(api, [*PATIENTS, stay("w-2", "W4", "rn-delacruz", "md-santos", "4B", person="h-2")])
    new = stay("m-5", "MED3A", "cn-med3a", None, "M2", person="h-2")
    new["observations"] = [
        *(o for o in new["observations"] if o["code"] != "blood_pressure"),
        *(obs("m-5", "blood_pressure", m, "nurse", num=100 + m, num2=60) for m in pressures),
    ]
    new["bins"] = bins("m-5", earlier)
    move = {
        "admissions": [{**new, "minute": 30}],
        "departures": [{"patient_id": "w-2", "minute": 30, "outcome": "transferred", "to_unit": "MED3A"}],
    }
    body = batch(minute=31, patients=[], observations=[], bins=[], events=[], **move)
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    return card(api, "MED3A", "M2").json()


def test_a_moved_persons_new_stay_shows_only_points_from_its_own_admission(watch: Api) -> None:
    body = moved_to_med3a(watch, [-100, 0, 20, 29, 30, 31], [-60, 20, 29, 30, 31])
    assert body["state"] == "ranked"
    history = body["history"]
    assert [p["value"] for p in history["heart_rate"]] == [131.0]  # bins stamped at or before 30 end by the admission
    assert [p["systolic"] for p in history["blood_pressure"]] == [130, 131]  # a reading AT the admission is in
    assert all(len(history[v]) <= 1 for v in FOUR)


def test_the_moved_persons_old_stay_keeps_its_own_history(watch: Api) -> None:
    run_id = hospital(watch, [*PATIENTS, {**stay("w-2", "W4", "rn-delacruz", "md-santos", "4B", person="h-2"), "bins": bins("w-2", [-100, -20])}])
    assert run_id
    heart = [p["value"] for p in card(watch, "W4", "4B").json()["history"]["heart_rate"]]
    assert heart == [100.0 - 100, 100.0 - 20]  # a first stay has no admission minute: nothing to clip
