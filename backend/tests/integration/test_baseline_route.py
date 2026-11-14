"""GET /patients/{id}/baseline: a patient's change from their own baseline, display only.
Scoped and audited like the patient route, typed, capped, and it writes nothing but its audit row."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_store import FOUR, KEY, NOW, ORIGIN, Api, admission, api, batch, obs, sim_patient, start  # noqa: F401 (api: fixture)

from biosentry.api.app import create_app
from biosentry.core.config import Settings
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.schemas import BaselineResponse, PatientDetail, VitalHistory
from biosentry.schemas.baseline import BASELINE_LABEL
from biosentry.services.baseline import BIN, MAX_BINS, grid
from biosentry.services.scoring import counted, drift

SKIP: Any = object()  # a slot with no stored row at all, as opposed to a row whose value is null
BASE = [66.0, 70.0, 74.0] * 8  # 24 bins: median 70, MAD 4, so spread 1.4826 * 4
SPREAD = 1.4826 * 4
SPO2 = [96.0, 97.0, 98.0] * 8
TIMES = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def rows(pid: str, vital: str, values: list[Any], first_minute: int = 0) -> list[dict[str, Any]]:
    return [
        {"patient_id": pid, "vital": vital, "minute": first_minute + 15 * i, "value": v}
        for i, v in enumerate(values)
        if v is not SKIP
    ]


def patient(pid: str = "s-1", bins: list[dict[str, Any]] | None = None, **kw: Any) -> dict:
    return {**sim_patient(pid, **kw), "bins": bins or []}


def get(api: Api, pid: str = "s-1", who: str = "rn-delacruz", **kw: Any) -> Any:
    return api.client.get(f"/patients/{pid}/baseline", headers=api.as_(who), **kw)


def vitals(api: Api, pid: str = "s-1") -> dict[str, dict[str, Any]]:
    response = get(api, pid)
    assert response.status_code == 200, response.text
    return {v["vital"]: v for v in response.json()["vitals"]}


def history_times(api: Api, pid: str = "s-1") -> list[str]:
    detail = api.client.get(f"/patients/{pid}", headers=api.as_("rn-delacruz")).json()
    return [p["time"] for p in detail["vital_history"]["heart_rate"]]


def served(api: Api, minute: int) -> str:
    """The served time of a bin stored at this sim minute: its stored time plus the run's shift."""
    (row,) = api.sql("SELECT last_batch_at - as_of AS shift FROM run ORDER BY id DESC LIMIT 1")
    moment = datetime.fromisoformat(ORIGIN) + timedelta(minutes=minute) + row["shift"]
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def sampling(pid: str, minute: int) -> list[dict[str, Any]]:
    """A batch's patient entry: the patch last sampled at this minute."""
    return [{"id": pid, "trend": "stable", "signal_quality": 90, "last_sample_minute": minute}]


def at(minute: int, value: float | None, vital: str = "heart_rate") -> dict[str, Any]:
    return {"patient_id": "s-1", "vital": vital, "minute": minute, "value": value}


def audit(api: Api) -> list[tuple[Any, ...]]:
    found = api.sql(
        "SELECT clinician_id, action, resource, outcome FROM audit_log"
        " WHERE action <> 'sign_in' ORDER BY seq"
    )
    return [tuple(r.values()) for r in found]


def with_bins(api: Api, bins: list[dict[str, Any]], **kw: Any) -> None:
    start(api, patients=[patient(bins=bins, **kw)])


# --- What it says ---------------------------------------------------------------------------


def test_fewer_than_24_bins_is_forming_and_says_how_many(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE[:10]) + rows("s-1", "spo2", SPO2[:5]))
    v = vitals(api)
    assert v["heart_rate"] == {
        "vital": "heart_rate", "status": "forming", "bins_valid": 10, "bins_needed": 24,
    }  # fmt: skip
    assert v["spo2"]["bins_valid"] == 5


def test_a_computed_baseline_that_has_not_moved_is_not_significant(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE + [72.0] * 3))
    hr = vitals(api)["heart_rate"]
    assert hr == {
        "vital": "heart_rate", "status": "computed", "baseline_median": 70.0, "latest": 72.0,
        "latest_at": served(api, 26 * 15), "latest_age_s": 0, "baseline_from": served(api, 0),
        "anchored_at": "first_kept_bin", "delta": 2.0, "z": 0.3, "run_bins": 0, "since": None, "significant": False, "direction": "rising",
        "pathway": "circulatory", "unit": "bpm",
    }  # fmt: skip


def test_a_persistent_rise_is_significant_and_says_when_it_began(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE + [70.0, 70.0, 90.0, 90.0, 90.0, 90.0]))
    response = get(api)
    hr = {v["vital"]: v for v in response.json()["vitals"]}["heart_rate"]
    assert (hr["latest"], hr["delta"], hr["run_bins"], hr["significant"]) == (90.0, 20.0, 4, True)
    assert hr["z"] == 3.4
    assert hr["since"] == history_times(api)[26] == served(api, 26 * 15)  # first of the four past k
    assert (hr["latest_at"], hr["baseline_from"]) == (served(api, 29 * 15), served(api, 0))
    assert BaselineResponse.model_validate(response.json()).label == BASELINE_LABEL
    assert response.json()["params"] == {"window_bins": 24, "k": 2.0, "p": 3}
    assert response.json()["synthetic"] is True


@pytest.mark.parametrize("gap", [SKIP, None], ids=["no row", "null value"])
def test_a_gap_is_a_gap_and_ends_a_run(api: Api, gap: Any) -> None:
    held = BASE + [90.0, 90.0, gap, 90.0, 90.0]
    with_bins(api, rows("s-1", "heart_rate", held))
    hr = vitals(api)["heart_rate"]
    assert (hr["run_bins"], hr["significant"]) == (2, False)
    # The time grid, not the row count, places the run: slot 27, with the missing slot between.
    assert hr["since"] == served(api, 27 * 15)


@pytest.mark.parametrize("gap", [SKIP, None], ids=["no row", "null value"])
def test_a_gap_in_the_baseline_window_is_never_filled(api: Api, gap: Any) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE[:12] + [gap] + BASE[12:23]))
    assert vitals(api)["heart_rate"]["bins_valid"] == 23  # 24 slots, 23 readings: still forming


@pytest.mark.parametrize("bad", [-5.0, 1e9])
def test_a_physically_impossible_value_is_a_gap(api: Api, bad: float) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE[:23] + [bad]))
    assert vitals(api)["heart_rate"]["bins_valid"] == 23


@pytest.mark.parametrize(
    ("vital", "base", "last"),
    [
        ("heart_rate", BASE, None),
        ("heart_rate", BASE, 1e9),
        ("spo2", SPO2, 150.0),
        ("spo2", SPO2, 0.0),  # zero SpO2 is what a zero-fill looks like
    ],
)
def test_a_missing_or_impossible_latest_bin_is_unavailable(
    api: Api, vital: str, base: list[float], last: float | None
) -> None:
    with_bins(api, rows("s-1", vital, [*base, last]))
    assert vitals(api)[vital] == {
        "vital": vital, "status": "unavailable", "reason": "latest_bin_missing",
    }  # fmt: skip


def test_temperature_has_no_history_to_read_so_it_is_unavailable(api: Api) -> None:
    with_bins(api, rows("s-1", "temperature", [37.0] * 30))  # stored, but not served in full
    assert vitals(api)["temperature"] == {
        "vital": "temperature", "status": "unavailable", "reason": "no_history",
    }  # fmt: skip


def test_a_patient_with_no_bins_is_200_with_every_vital_forming_or_unavailable(api: Api) -> None:
    with_bins(api, [])
    response = get(api)
    assert response.status_code == 200
    assert [(v["vital"], v["status"]) for v in response.json()["vitals"]] == [
        ("heart_rate", "forming"),
        ("respiratory_rate", "forming"),
        ("spo2", "forming"),
        ("temperature", "unavailable"),
    ]
    assert {v.get("bins_valid") for v in response.json()["vitals"][:3]} == {0}


def test_a_long_stay_keeps_the_newest_672_bins_and_baselines_from_the_oldest_of_those(
    api: Api,
) -> None:
    kept = BASE + [70.0] * (MAX_BINS - 24)
    with_bins(api, rows("s-1", "heart_rate", [100.0] * 28 + kept))
    hr = vitals(api)["heart_rate"]
    assert (hr["baseline_median"], hr["delta"]) == (70.0, 0.0)  # uncapped, it would be 100


def test_the_grid_is_capped_and_starts_at_the_oldest_bin_kept() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    points = [VitalHistory(time=t0 + i * BIN, value=float(i)) for i in range(MAX_BINS + 28)]
    bins, start_at = grid(points, None, "heart_rate")
    assert (len(bins), bins[0], bins[-1], start_at) == (MAX_BINS, 28.0, 699.0, t0 + 28 * BIN)


def test_bins_before_admission_are_not_the_baseline(api: Api) -> None:
    run_id = start(api, patients=[patient()])
    minute = 600
    arrival = {
        **admission("s-2"),
        "minute": minute,
        "bins": rows("s-2", "heart_rate", [100.0] * 24)  # before admission: 100 bpm
        + rows("s-2", "heart_rate", BASE, first_minute=minute),
    }
    body = batch(minute, admissions=[arrival], patients=sampling("s-2", minute))
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    hr = vitals(api, "s-2")["heart_rate"]
    assert (hr["baseline_median"], hr["latest"]) == (70.0, 74.0)
    assert hr["baseline_from"] == served(api, minute)  # not the older bins before admission
    assert get(api, "s-2").json()["admission_known"] is True
    assert hr["anchored_at"] == "admission"


def test_without_an_admission_time_the_window_starts_at_the_first_stored_bin(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE, first_minute=45))
    response = get(api).json()
    assert response["admission_known"] is False
    assert response["vitals"][0]["baseline_from"] == served(api, 45)


def test_a_value_beats_a_null_in_one_slot_and_the_later_of_two_values_stands(api: Api) -> None:
    lead = rows("s-1", "heart_rate", BASE[:23])  # minutes 0..330; slot 23 is minutes 345..359
    for extra, latest in [
        ([at(345, 74.0), at(350, None)], 74.0),  # a later null does not erase the value
        ([at(345, None), at(350, 74.0)], 74.0),  # an earlier null does not hide it
        ([at(345, 74.0), at(350, 80.0)], 80.0),
    ]:
        start(api, patients=[patient(bins=lead + extra)])
        hr = vitals(api)["heart_rate"]
        assert (hr["status"], hr["latest"]) == ("computed", latest)  # 24 valid slots, not 23


def test_a_heart_rate_of_zero_is_a_reading_not_a_gap(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", [*BASE, 0.0]))
    hr = vitals(api)["heart_rate"]
    assert (hr["status"], hr["latest"], hr["delta"], hr["direction"]) == (
        "computed", 0.0, -70.0, "falling",
    )  # fmt: skip
    assert (hr["run_bins"], hr["significant"]) == (0, False)  # a fall is not the bad direction


def test_latest_is_served_as_stored_and_derived_numbers_are_rounded(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", [*BASE, 72.3]))
    response = get(api)
    hr = response.json()["vitals"][0]
    assert (hr["latest"], hr["delta"], hr["baseline_median"], hr["z"]) == (72.3, 2.3, 70.0, 0.4)
    assert "2.299999" not in response.text


def test_a_stale_latest_says_when_it_was_taken(api: Api) -> None:
    run_id = start(api, patients=[patient(bins=rows("s-1", "heart_rate", BASE + [90.0] * 4))])
    two_days = 2 * 24 * 60  # the run moves on and no heart-rate bin follows
    # the patch keeps sampling (its value is unchanged, so no new bin row), or the panel would
    # rightly say it is not live
    quiet = batch(minute=two_days, bins=[], observations=[], events=[])
    assert api.post(f"/sim/runs/{run_id}/batch", quiet).status_code == 204
    hr = vitals(api)["heart_rate"]
    assert (hr["latest"], hr["significant"]) == (90.0, True)  # still served, and now dated
    assert hr["latest_at"] == served(api, 27 * 15)
    lag = api.wall.now - datetime.fromisoformat(hr["latest_at"])
    assert lag == timedelta(minutes=two_days - 27 * 15)


def test_the_age_is_the_servers_and_counts_from_the_end_of_the_latest_bin(api: Api) -> None:
    last = 24 * 15  # the minute of the latest bin
    run_id = start(api, patients=[patient(bins=rows("s-1", "heart_rate", [*BASE, 90.0]))])
    batch_at = batch(minute=last, bins=[], observations=[], events=[])
    assert api.post(f"/sim/runs/{run_id}/batch", batch_at).status_code == 204
    assert vitals(api)["heart_rate"]["latest_age_s"] == 0  # the bin has only just begun
    api.wall.now += timedelta(minutes=20)  # a known shift: 5 minutes past the bin's end
    assert vitals(api)["heart_rate"]["latest_age_s"] == 5 * 60
    api.wall.now += timedelta(hours=6)  # inside the token's 12 hours
    # the patch kept sampling: the same shift, a newer sample (and still no new bin)
    later = batch(minute=last + 380, bins=[], observations=[], events=[])
    assert api.post(f"/sim/runs/{run_id}/batch", later).status_code == 204
    stale = vitals(api)["heart_rate"]
    assert stale["latest_age_s"] == 5 * 60 + 6 * 3600
    assert stale["latest_at"] == served(api, last)  # still the bin's start, not its end


def test_the_age_is_never_negative_when_the_servers_clock_is_behind(api: Api) -> None:
    run_id = start(api, patients=[patient(bins=rows("s-1", "heart_rate", [*BASE, 90.0]))])
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=24 * 15)).status_code == 204
    api.wall.now -= timedelta(hours=3)
    assert vitals(api)["heart_rate"]["latest_age_s"] == 0


def test_a_window_that_starts_at_admission_says_so_within_thirty_minutes(api: Api) -> None:
    run_id = start(api, patients=[patient()])
    minute = 600
    for pid, lead in [("s-2", 2), ("s-3", 3)]:  # two leading gap bins = 30 min; three = 45
        values = [None] * lead + BASE
        arrival = {**admission(pid), "minute": minute, "bins": rows(pid, "heart_rate", values, minute)}
        body = batch(minute, admissions=[arrival], patients=sampling(pid, minute))
        assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert vitals(api, "s-2")["heart_rate"]["anchored_at"] == "admission"
    assert vitals(api, "s-3")["heart_rate"]["anchored_at"] == "first_kept_bin"


def test_a_window_cut_by_the_cap_after_admission_is_anchored_mid_stay(api: Api) -> None:
    run_id = start(api, patients=[patient()])
    minute = 600
    values = [100.0] * 28 + BASE + [70.0] * (MAX_BINS - 24)  # 28 oldest bins fall off the cap
    arrival = {**admission("s-2"), "minute": minute, "bins": rows("s-2", "heart_rate", values, minute)}
    body = batch(minute, admissions=[arrival], patients=sampling("s-2", minute))
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    response = get(api, "s-2").json()
    hr = response["vitals"][0]
    assert response["admission_known"] is True  # the admission is known, yet the window is not at it
    assert hr["anchored_at"] == "first_kept_bin"
    assert hr["baseline_from"] == served(api, minute + 28 * 15)


def test_without_an_admission_time_the_anchor_is_the_first_kept_bin(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE))
    assert vitals(api)["heart_rate"]["anchored_at"] == "first_kept_bin"


def test_retention_above_seven_days_meets_the_cap_which_drops_stored_data(
    database_url: str,
) -> None:
    long = Api(database_url)
    long.settings = Settings(database_url=database_url, ingest_key=KEY, retention_days=14)
    long.client = TestClient(create_app(long.settings, clock=long.wall))
    stays = 10 * 24 * 4  # ten days of bins, all still stored under a 14-day retention
    values = [100.0] * 288 + BASE + [70.0] * (stays - 288 - 24)
    run_id = start(long, patients=[patient(bins=rows("s-1", "heart_rate", values))])
    assert long.post(f"/sim/runs/{run_id}/batch", batch(stays * 15, bins=[])).status_code == 204
    stored = "SELECT count(*) AS n FROM vital_bin WHERE run_id = %s AND vital = 'heart_rate'"
    assert long.sql(stored, run_id) == [{"n": stays}]
    hr = vitals(long)["heart_rate"]
    # The anchor is the oldest bin inside the cap, and `baseline_from` shows it.
    assert hr["baseline_from"] == served(long, (stays - MAX_BINS) * 15)
    assert hr["baseline_median"] == 70.0


def test_a_not_ranked_patient_gets_the_patient_routes_404_and_one_audit_row(api: Api) -> None:
    nurse_only = [o for o in sim_patient()["observations"] if o["source"] == "nurse"]
    start(api, patients=[{**patient(bins=rows("s-1", "heart_rate", BASE)), "observations": nurse_only}])
    assert [a["id"] for a in api.ward("rn-delacruz")["abstained"]] == ["s-1"]
    theirs = api.client.get("/patients/s-1", headers=api.as_("rn-delacruz"))
    before = len(audit(api))
    mine = get(api)
    assert (mine.status_code, mine.json()) == (theirs.status_code, theirs.json()) == (
        404, {"detail": "Not found"},
    )  # fmt: skip
    assert audit(api)[before - 1 :] == [
        ("rn-delacruz", "view_patient", "s-1", "not_found"),
        ("rn-delacruz", "view_baseline", "s-1", "not_found"),
    ]


def test_a_patient_who_has_left_gets_the_patient_routes_404_and_one_audit_row(api: Api) -> None:
    run_id = start(api, patients=[patient(bins=rows("s-1", "heart_rate", BASE))])
    leaving = [{"patient_id": "s-1", "minute": 5, "outcome": "discharged"}]
    assert api.post(f"/sim/runs/{run_id}/batch", batch(5, departures=leaving)).status_code == 204
    theirs = api.client.get("/patients/s-1", headers=api.as_("rn-delacruz"))
    before = len(audit(api))
    mine = get(api)
    assert (mine.status_code, mine.json()) == (theirs.status_code, theirs.json())
    assert mine.status_code == 404
    assert audit(api)[before:] == [("rn-delacruz", "view_baseline", "s-1", "not_found")]


def test_nothing_in_the_request_can_set_the_parameters(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE + [90.0] * 5))
    plain = get(api).json()
    tuned = get(api, params={"window_bins": 1, "k": 0.01, "p": 1, "vital": "x"}).json()
    assert tuned == plain


# --- Who may see it ---------------------------------------------------------------------------


def test_a_clinician_who_is_not_assigned_gets_the_patient_routes_404_and_audit(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE))
    h = api.as_("rn-reyes")
    theirs = api.client.get("/patients/s-1", headers=h)
    before = len(audit(api))
    refused = get(api, who="rn-reyes")
    missing = get(api, "s-999", who="rn-reyes")
    assert (refused.status_code, refused.json()) == (theirs.status_code, theirs.json()) == (
        404, {"detail": "Not found"},
    )  # fmt: skip
    assert (missing.status_code, missing.json()) == (refused.status_code, refused.json())
    assert audit(api)[before:] == [
        ("rn-reyes", "view_baseline", "s-1", "not_found"),
        ("rn-reyes", "view_baseline", "s-999", "not_found"),
    ]


def test_a_missing_or_bad_token_is_refused_and_serves_nothing(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE))
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        response = api.client.get("/patients/s-1/baseline", headers=headers)
        assert response.status_code == 401
        assert "s-1" not in response.text
    assert set(audit(api)) == {(None, "authenticate", "-", "denied")}


def test_a_successful_call_writes_one_audit_row_and_nothing_else(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE))
    api.as_("rn-delacruz")
    counts = (
        "SELECT (SELECT count(*) FROM observation) AS o, (SELECT count(*) FROM vital_bin) AS b,"
        " (SELECT count(*) FROM patient) AS p, (SELECT count(*) FROM sim_event) AS e,"
        " (SELECT count(*) FROM audit_log) AS a"
    )
    (before,) = api.sql(counts)
    assert get(api).status_code == 200
    (after,) = api.sql(counts)
    assert after == {**before, "a": before["a"] + 1}
    assert audit(api) == [("rn-delacruz", "view_baseline", "s-1", "ok")]  # no view_patient row


@pytest.mark.parametrize(
    "pid",
    ["x" * 5000, "a b", "é", "%00", "p-1;DROP TABLE patient;--", "%2e%2e", "p%2Fq", "s-1 "],
)
def test_a_strange_id_is_handled_like_the_patient_route_handles_it(api: Api, pid: str) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE))
    api.as_("rn-delacruz")
    start_at = len(audit(api))
    theirs = api.client.get(f"/patients/{pid}", headers=api.as_("rn-delacruz"))
    patient_rows = audit(api)[start_at:]
    mine = get(api, pid)
    baseline_rows = audit(api)[start_at + len(patient_rows) :]
    assert (mine.status_code, mine.json()) == (theirs.status_code, theirs.json())
    named = [("view_baseline" if a == "view_patient" else a, *rest) for _, a, *rest in patient_rows]
    assert [r[1:] for r in baseline_rows] == [tuple(r) for r in named]
    assert max((len(r[2]) for r in baseline_rows), default=0) <= 200


def test_the_response_carries_no_name_and_no_stored_text(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE + [90.0] * 4) + rows("s-1", "spo2", SPO2))
    response = get(api)
    for stored in ("Sim, Pat", "a story", "ctx", "9A", "rn-delacruz", "md-santos"):
        assert stored not in response.text
    strings: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                strings.append(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(response.json())
    fixed = {
        "s-1", BASELINE_LABEL, "computed", "forming", "unavailable", "heart_rate",
        "respiratory_rate", "spo2", "temperature", "no_history", "latest_bin_missing",
        "bpm", "/min", "%", "°C", "rising", "falling", "stable", "circulatory", "respiratory",
            "admission", "first_kept_bin",
    }  # fmt: skip
    assert {s for s in strings if s not in fixed and not TIMES.match(s)} <= {
        "patient_id", "synthetic", "admission_known", "latest_at", "baseline_from", "label", "params", "vitals", "window_bins", "k", "p", "vital", "latest_age_s", "anchored_at",
        "status", "baseline_median", "latest", "delta", "z", "run_bins", "since", "significant",
        "direction", "pathway", "unit", "bins_valid", "bins_needed", "reason",
    }  # fmt: skip


def test_the_baseline_does_not_change_what_the_patient_route_serves(api: Api) -> None:
    with_bins(api, rows("s-1", "heart_rate", BASE + [90.0] * 5))
    h = api.as_("rn-delacruz")
    before = api.client.get("/patients/s-1", headers=h).json()
    assert get(api).status_code == 200
    assert api.client.get("/patients/s-1", headers=h).json() == before
    PatientDetail.model_validate(before)


# --- The finding in the ward's order ----------------------------------------------------------

FLAGGED = BASE + [90.0] * 5  # a rise of 20 bpm for 75 minutes: significant
NAME = {"heart_rate": "heart rate", "respiratory_rate": "respiratory rate", "spo2": "SpO2"}


def calm(pid: str, bins: list[dict[str, Any]] | None = None, **device: float) -> dict[str, Any]:
    """A patient at NEWS2 0 unless a device value says otherwise."""
    values = {"heart_rate": 70, "respiratory_rate": 16, "spo2": 98, "temperature": 37.0, **device}
    made = patient(pid, bins)
    made["observations"] = [
        o for o in made["observations"] if o["source"] == "nurse"
    ] + [obs(pid, code, -2, num=num) for code, num in values.items()]
    made["observations"][0:4] = [
        obs(pid, "blood_pressure", -10, "nurse", num=120, num2=80),
        obs(pid, "consciousness", -10, "nurse", txt="A"),
        obs(pid, "supplemental_o2", -10, "nurse", num=0),
        obs(pid, "spo2_scale", -10, "nurse", num=1),
    ]
    return made


def order(api: Api) -> list[str]:
    return [r["id"] for r in api.ward("rn-delacruz")["ranked"]]


def reasons(api: Api, pid: str) -> list[dict[str, Any]]:
    row = next(r for r in api.ward("rn-delacruz")["ranked"] if r["id"] == pid)
    return [r for r in row["escalation_reasons"] if r["rule"] == "tier_b_finding"]


def test_a_flagged_patient_ranks_first_among_equal_totals_and_says_why(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", BASE)),
                         calm("s-2", rows("s-2", "heart_rate", FLAGGED))])  # fmt: skip
    assert order(api) == ["s-2", "s-1"]
    assert reasons(api, "s-2") == [{
        "rule": "tier_b_finding", "pathway": "circulatory",
        "text": "Drifting from own baseline: heart rate, 1 h 15 min (synthetic, not validated)",
    }]  # fmt: skip
    assert reasons(api, "s-1") == []


def test_a_flagged_patient_with_a_lower_total_stays_below(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", BASE), heart_rate=100),
                         calm("s-2", rows("s-2", "heart_rate", FLAGGED))])  # fmt: skip
    assert order(api) == ["s-1", "s-2"]
    assert reasons(api, "s-2")


def test_a_triggered_patient_stays_above_a_flagged_one(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", BASE), respiratory_rate=25),
                         calm("s-2", rows("s-2", "heart_rate", FLAGGED))])  # fmt: skip
    assert order(api) == ["s-1", "s-2"]


def test_a_flag_leaves_news2_and_escalation_alone(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", BASE)),
                         calm("s-2", rows("s-2", "heart_rate", FLAGGED))])  # fmt: skip
    rows_ = {r["id"]: r for r in api.ward("rn-delacruz")["ranked"]}
    assert (rows_["s-2"]["score"], rows_["s-2"]["supporting"]) == (0, rows_["s-1"]["supporting"])
    assert not [r for r in rows_["s-2"]["escalation_reasons"] if r["rule"] == "tier_a_threshold"]


def test_a_patch_that_is_off_gets_no_flag_from_its_old_bins(api: Api) -> None:
    made = calm("s-1", rows("s-1", "heart_rate", FLAGGED))
    made["observations"] = [o for o in made["observations"] if o["code"] != "heart_rate"] + [
        obs("s-1", "heart_rate", -1, "nurse", num=70)
    ]
    start(api, patients=[made])
    assert order(api) == ["s-1"]
    assert reasons(api, "s-1") == []
    panel = vitals(api)
    assert panel["heart_rate"] == {
        "vital": "heart_rate", "status": "unavailable", "reason": "patch_not_live",
    }  # fmt: skip
    assert (panel["respiratory_rate"]["status"], panel["spo2"]["status"]) == ("forming", "forming")
    assert panel["temperature"]["reason"] == "no_history"


def test_a_nurse_typed_heart_rate_after_a_significant_run_leaves_no_flag_and_no_panel_finding(
    api: Api,
) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", FLAGGED))])
    assert reasons(api, "s-1") and vitals(api)["heart_rate"]["significant"] is True
    post = api.client.post(
        "/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json={"heart_rate": 70}
    )
    assert post.status_code == 200
    assert reasons(api, "s-1") == []
    assert vitals(api)["heart_rate"]["reason"] == "patch_not_live"


def silent_for(api: Api, minutes: int) -> None:
    """The ward clock is at minute 480 and the patch last sampled `minutes` ago."""
    quiet = batch(
        minute=480, bins=[], observations=[], events=[],
        patients=[{"id": "s-1", "trend": "stable", "signal_quality": 90,
                   "last_sample_minute": None if minutes < 0 else 480 - minutes}],
    )  # fmt: skip
    (run,) = api.sql("SELECT max(id) AS i FROM run")
    assert api.post(f"/sim/runs/{run['i']}/batch", quiet).status_code == 204


def test_a_patch_silent_for_31_minutes_gets_no_flag_and_one_silent_for_30_does(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", FLAGGED))])
    silent_for(api, 30)
    assert reasons(api, "s-1")
    hr = vitals(api)["heart_rate"]
    assert (hr["status"], hr["significant"]) == ("computed", True)
    silent_for(api, 31)
    assert reasons(api, "s-1") == []
    assert vitals(api)["heart_rate"]["reason"] == "patch_not_live"


def test_a_patch_that_never_sampled_gets_no_flag_even_with_device_values(api: Api) -> None:
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", FLAGGED))])
    silent_for(api, -1)
    assert api.ward("rn-delacruz")["ranked"][0]["last_update_s"] is None
    assert reasons(api, "s-1") == []
    assert vitals(api)["heart_rate"]["reason"] == "patch_not_live"


def test_a_device_absent_heart_rate_after_a_significant_run_counts_for_nothing(api: Api) -> None:
    run_id = start(api, patients=[calm("s-1", rows("s-1", "heart_rate", FLAGGED))])
    gone = obs("s-1", "heart_rate", 500, absent_reason="No usable reading yet")
    body = batch(minute=500, bins=[], observations=[gone], events=[], patients=[])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert [a["id"] for a in api.ward("rn-delacruz")["abstained"]] == ["s-1"]  # no heart rate to score
    with connect(api.url) as conn:
        state = PostgresStore(conn, api.wall).read("s-1")
    assert state is not None
    assert not counted(state, "heart_rate")
    assert drift(state) == ()
    assert get(api).status_code == 404  # the panel itself is closed to a NOT RANKED patient


def test_a_not_ranked_patient_never_has_a_finding_computed(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    nurse_only = [o for o in sim_patient()["observations"] if o["source"] == "nurse"]
    start(api, patients=[{**patient(bins=rows("s-1", "heart_rate", FLAGGED)), "observations": nurse_only}])

    def boom(state: Any) -> Any:
        raise AssertionError("drift ran for a NOT RANKED patient")

    monkeypatch.setattr("biosentry.services.scoring.drift", boom)
    assert [a["id"] for a in api.ward("rn-delacruz")["abstained"]] == ["s-1"]
    assert get(api).status_code == 404


def test_a_nurse_only_patient_gets_no_flag_and_a_forming_baseline(api: Api) -> None:
    nurse_only = [o for o in sim_patient()["observations"] if o["source"] == "nurse"]
    start(api, patients=[{**patient(), "observations": nurse_only}])
    post = api.client.post("/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json=FOUR)
    assert post.status_code == 200
    assert order(api) == ["s-1"]
    assert reasons(api, "s-1") == []
    assert {v["status"] for v in vitals(api).values()} == {"forming", "unavailable"}


def test_the_baseline_route_and_the_row_name_the_same_vitals(api: Api) -> None:
    rr = [14.0, 15.0, 16.0] * 8
    start(api, patients=[calm("s-1", rows("s-1", "heart_rate", FLAGGED) + rows("s-1", "respiratory_rate", rr + [20.0] * 4))])
    significant = {k for k, v in vitals(api).items() if v.get("significant")}
    named = {
        next(k for k, n in NAME.items() if f"baseline: {n}," in r["text"]) for r in reasons(api, "s-1")
    }
    assert significant == named == {"heart_rate", "respiratory_rate"}
    assert [r["pathway"] for r in reasons(api, "s-1")] == ["circulatory", "respiratory"]


# --- The cost of the finding on a ward read ---------------------------------------------------


def test_a_ward_read_runs_the_same_statements_with_or_without_the_finding(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = [0]
    real = psycopg.Connection.execute

    def counting(self: Any, *args: Any, **kw: Any) -> Any:
        seen[0] += 1
        return real(self, *args, **kw)

    def statements() -> int:
        api.wall.now += timedelta(seconds=61)  # outside the minute: each read writes its row
        seen[0] = 0
        api.ward("rn-delacruz")
        return seen[0]

    start(api, patients=[calm(f"s-{i}", rows(f"s-{i}", "heart_rate", FLAGGED)) for i in (1, 2, 3)])
    api.as_("rn-delacruz")  # sign in first: its statements are not the read's
    monkeypatch.setattr(psycopg.Connection, "execute", counting)
    with_finding = statements()
    assert reasons(api, "s-1")
    monkeypatch.setattr("biosentry.services.scoring.drift", lambda state: ())
    without = statements()
    assert reasons(api, "s-1") == []
    assert with_finding > 0
    assert with_finding == without
