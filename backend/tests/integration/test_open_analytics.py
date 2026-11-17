"""GET /wards/{ward}/open-view/analytics: counts for a watcher nobody identified, behind the open-view
setting. The seeded run below is counted by hand in the comments; the answer carries no id, name,
bed, person or clinician."""

from datetime import timedelta
from typing import Any

import pytest
from test_api import VALID_BODIES, _route_table
from test_open_view import NOT_FOUND, watch  # noqa: F401 (watch: the API with the setting on)
from test_store import Api, api, batch, obs, sim_patient  # noqa: F401 (api: fixture)
from test_units import UNITS

from biosentry.api.app import OPEN_ANALYTICS_ACTION

TEMPLATE = "/wards/{ward}/open-view/analytics"
URL = "/wards/W4/open-view/analytics?hours=3"
TOP = {"synthetic", "origin", "hourly", "shifts", "finished_stays"}
HOUR = {"from_minute", "to_minute", "beds_in_use", "admitted", "discharged", "transferred_in", "transferred_out", "news2"}
NEWS2 = {"zero_to_four", "five_to_six", "seven_plus", "not_ranked"}
SHIFT = {"from_minute", "to_minute", "alerts", "patient_minutes"}
STAY = {"in_minute", "out_minute", "count"}
GOOD = {"heart_rate": 70, "respiratory_rate": 16, "spo2": 97, "temperature": 36.8}
SEVEN = {"heart_rate": 120, "respiratory_rate": 22, "spo2": 93, "temperature": 38.5}  # 2 + 2 + 2 + 1
FIVE = {"heart_rate": 100, "respiratory_rate": 22, "spo2": 94, "temperature": 38.5}  # 1 + 2 + 1 + 1
SINGLE = {**GOOD, "respiratory_rate": 26}  # one 3, total 3


def get(api: Api, path: str = URL, **headers: str) -> Any:
    return api.client.get(path, headers=headers)


def rows(api: Api) -> list[dict[str, Any]]:
    return api.sql(
        "SELECT clinician_id, role, action, resource, outcome FROM audit_log WHERE action = %s ORDER BY seq",
        OPEN_ANALYTICS_ACTION,
    )


def person(pid: str, unit: str, minute: int | None, bed: str, rn: str, **over: Any) -> dict[str, Any]:
    """A patient with the four charted inputs normal and no bins (the caller adds them)."""
    charted = [
        obs(pid, "blood_pressure", minute or 0, "nurse", num=120, num2=80),
        obs(pid, "consciousness", minute or 0, "nurse", txt="A"),
        obs(pid, "supplemental_o2", minute or 0, "nurse", num=0),
        obs(pid, "spo2_scale", minute or 0, "nurse", num=1),
    ]
    base = {**sim_patient(pid, rn, None), "unit": unit, "bed": bed, "name": f"NAME-{pid}", "observations": charted, "bins": []}
    return {**base, **({} if minute is None else {"minute": minute}), **over}


def bins(pid: str, minute: int, values: dict[str, float]) -> list[dict[str, Any]]:
    return [{"patient_id": pid, "vital": v, "minute": minute, "value": float(x)} for v, x in values.items()]


def seed(api: Api) -> None:
    """W4 (ranked) and ICU (unranked), ward time minute 180. W4's stays:
    mk-0 on the ward before the run, left 45 (higher care) | mk-1 before the run, still here
    mk-2 in 60, out 125 (discharged) | mk-5 in 60, out 125 (died) | mk-3 in 100, out 150 (to ICU) | mk-4 in 170
    ICU: mk-6 in 150, the same person as mk-3."""
    start = {
        "client_id": "client-a", "source": "synthetic", "seed": 7, "origin": "2026-01-01T00:00:00Z",
        "units": [UNITS[0], UNITS[2]],
        "patients": [
            person("mk-0", "W4", None, "4A", "rn-delacruz"),
            {**person("mk-1", "W4", None, "4B", "rn-delacruz"), "bins": [
                *bins("mk-1", 60, GOOD), *bins("mk-1", 120, SEVEN), *bins("mk-1", 180, SINGLE)
            ]},
        ],
    }  # fmt: skip
    response = api.post("/sim/runs", start)
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    admissions = [
        person("mk-2", "W4", 60, "4C", "rn-delacruz", bins=[*bins("mk-2", 75, FIVE), *bins("mk-2", 120, FIVE)]),
        person("mk-5", "W4", 60, "4D", "rn-delacruz", person="mk-person-5"),
        person("mk-3", "W4", 100, "4E", "rn-delacruz", person="mk-person-3"),
        person("mk-4", "W4", 170, "4F", "rn-delacruz"),
        person("mk-6", "ICU", 150, "I1", "cn-icu", person="mk-person-3"),
    ]
    departures = [
        {"patient_id": "mk-0", "minute": 45, "outcome": "higher_care"},
        {"patient_id": "mk-2", "minute": 125, "outcome": "discharged"},
        {"patient_id": "mk-5", "minute": 125, "outcome": "died"},
        {"patient_id": "mk-3", "minute": 150, "outcome": "transferred", "to_unit": "ICU"},
    ]
    done = api.post(
        f"/sim/runs/{run_id}/batch",
        batch(minute=180, patients=[], observations=[], bins=[], events=[], admissions=admissions, departures=departures),
    )
    assert done.status_code == 204, done.text


@pytest.fixture
def seeded(watch: Api) -> Api:  # noqa: F811
    seed(watch)
    return watch


def counts(hour: dict[str, Any]) -> list[int]:
    return [hour[k] for k in ("beds_in_use", "admitted", "discharged", "transferred_in", "transferred_out")] + [
        hour["news2"][k] for k in ("zero_to_four", "five_to_six", "seven_plus", "not_ranked")
    ]


# --- Off: the route does not exist ---------------------------------------------------------------


def test_off_it_is_the_frameworks_404_and_writes_no_row(api: Api) -> None:
    unknown = get(api, "/no-such-path")
    response = get(api)
    assert (response.status_code, response.content) == (404, unknown.content)
    assert response.json() == NOT_FOUND
    assert all(path != TEMPLATE for _, path in _route_table(api.client.app))
    assert api.sql("SELECT * FROM audit_log WHERE action <> 'sign_in'") == []


# --- On: no credential is read -------------------------------------------------------------------


def test_on_the_same_bytes_for_any_credential_and_it_reads_no_session(seeded: Api) -> None:
    plain = get(seeded)
    assert plain.status_code == 200
    assert plain.headers["cache-control"] == "no-store"
    for headers in (
        {"Authorization": seeded.as_("md-santos")["Authorization"]}, {"Authorization": "Bearer junk"},
        {"Cookie": "session=x"}, {"X-Ingest-Key": "test-ingest-key"}, {"Authorization": "Basic abc"},
    ):  # fmt: skip
        assert get(seeded, **headers).content == plain.content
    assert seeded.sql("SELECT * FROM audit_log WHERE action = 'authenticate'") == []


def test_on_it_is_the_only_route_gained_and_every_other_still_wants_a_token(seeded: Api) -> None:
    assert ("GET", TEMPLATE) in _route_table(seeded.client.app)
    assert VALID_BODIES.get(("GET", TEMPLATE)) is None
    assert seeded.client.get("/wards/W4/rows").status_code == 401


# --- The answer ------------------------------------------------------------------------------------


def test_the_answer_has_exactly_the_contracts_keys_at_every_level(seeded: Api) -> None:
    body = get(seeded).json()
    assert set(body) == TOP
    assert all(set(h) == HOUR and set(h["news2"]) == NEWS2 for h in body["hourly"])
    assert all(set(s) == SHIFT for s in body["shifts"])
    assert all(set(f) == STAY for f in body["finished_stays"])
    assert body["synthetic"] is True


def test_no_id_name_bed_person_or_clinician_is_in_the_answer(seeded: Api) -> None:
    text = get(seeded, "/wards/W4/open-view/analytics?hours=168").text + get(seeded, "/wards/ICU/open-view/analytics").text
    marks = [f"mk-{i}" for i in range(7)] + [f"NAME-mk-{i}" for i in range(7)] + ["mk-person", "Sim, Pat", "rn-delacruz", "cn-icu"]
    marks += ["4A", "4B", "4C", "4D", "4E", "4F", "I1", "W4", "ICU"]
    for mark in marks:
        assert mark not in text, mark


def test_counts_on_the_seeded_run_match_the_hand_count(seeded: Api) -> None:
    body = get(seeded).json()
    assert body["origin"] == "2026-01-01T00:00:00Z"
    assert [(h["from_minute"], h["to_minute"]) for h in body["hourly"]] == [(0, 60), (60, 120), (120, 180)]
    # beds, admitted, discharged, transferred in, transferred out | 0-4, 5-6, 7+, not ranked
    assert counts(body["hourly"][0]) == [3, 2, 0, 0, 0, 1, 0, 0, 2]  # mk-1 (bin 60) | mk-2, mk-5 have no bin by 60
    assert counts(body["hourly"][1]) == [4, 1, 0, 0, 0, 0, 1, 1, 2]  # mk-2 five, mk-1 seven | mk-3, mk-5
    assert counts(body["hourly"][2]) == [2, 1, 1, 0, 1, 1, 0, 0, 1]  # mk-1 single 3 is total 3 | mk-4
    # alerts: mk-1 at 120 and 180, mk-2 at 75 and 120. Patient-minutes: 45 + 180 + 65 + 65 + 50 + 10.
    assert body["shifts"] == [{"from_minute": 0, "to_minute": 180, "alerts": 4, "patient_minutes": 415}]
    assert body["finished_stays"] == [
        {"in_minute": None, "out_minute": 45, "count": 1},
        {"in_minute": 60, "out_minute": 125, "count": 2},
        {"in_minute": 100, "out_minute": 150, "count": 1},
    ]


def test_an_unranked_unit_counts_arrivals_and_never_a_score(seeded: Api) -> None:
    body = get(seeded, "/wards/ICU/open-view/analytics?hours=3").json()
    assert [counts(h) for h in body["hourly"]] == [[0] * 9, [0] * 9, [1, 0, 0, 1, 0, 0, 0, 0, 1]]
    assert body["shifts"] == [{"from_minute": 0, "to_minute": 180, "alerts": 0, "patient_minutes": 30}]
    assert body["finished_stays"] == []


def test_hours_before_the_run_and_a_second_shift_are_zero_filled_and_clipped(seeded: Api) -> None:
    body = get(seeded, "/wards/W4/open-view/analytics?hours=168").json()
    assert len(body["hourly"]) == 168
    assert body["hourly"][0]["to_minute"] == 180 - 167 * 60
    assert counts(body["hourly"][0]) == [2, 0, 0, 0, 0, 0, 0, 0, 2]  # the two there before the run count from the window's start
    assert [(s["from_minute"], s["to_minute"]) for s in body["shifts"]][-2:] == [(-720, 0), (0, 180)]
    assert len(body["shifts"]) == 15 and body["shifts"][0]["from_minute"] == 180 - 168 * 60
    one = get(seeded, "/wards/W4/open-view/analytics?hours=1").json()
    assert [(h["from_minute"], h["to_minute"]) for h in one["hourly"]] == [(120, 180)]
    assert one["finished_stays"] == [{"in_minute": 60, "out_minute": 125, "count": 2}, {"in_minute": 100, "out_minute": 150, "count": 1}]
    assert len(get(seeded, "/wards/W4/open-view/analytics").json()["hourly"]) == 24


def test_a_shift_edge_inside_the_window_splits_it(seeded: Api) -> None:
    run = seeded.sql("SELECT max(id) AS i FROM run")[0]["i"]
    seeded.post(f"/sim/runs/{run}/batch", batch(minute=800, patients=[], observations=[], bins=[], events=[]))
    shifts = get(seeded, "/wards/W4/open-view/analytics?hours=14").json()["shifts"]
    assert [(s["from_minute"], s["to_minute"]) for s in shifts] == [(-40, 0), (0, 720), (720, 800)]


def test_a_window_counts_only_the_bins_and_minutes_inside_it(seeded: Api) -> None:
    shift = get(seeded, "/wards/W4/open-view/analytics?hours=1").json()["shifts"][0]
    # bins ending in (120, 180]: mk-1 at 180 only. Minutes from 120: 60 + 5 + 5 + 30 + 10.
    assert (shift["alerts"], shift["patient_minutes"]) == (1, 110)


# --- Parameters and unknowns -----------------------------------------------------------------------


@pytest.mark.parametrize("hours", ["0", "-1", "169", "x", "1.5", ""])
def test_hours_outside_1_to_168_is_a_422(seeded: Api, hours: str) -> None:
    assert get(seeded, f"/wards/W4/open-view/analytics?hours={hours}").status_code == 422
    assert rows(seeded) == []


def test_an_unknown_unit_is_the_frameworks_404_with_one_row(seeded: Api) -> None:
    response = get(seeded, "/wards/W9/open-view/analytics")
    assert (response.status_code, response.content) == (404, get(seeded, "/no-such-path").content)
    assert rows(seeded) == [{"clinician_id": None, "role": None, "action": OPEN_ANALYTICS_ACTION, "resource": "-", "outcome": "not_found"}]


def test_a_run_that_is_not_synthetic_is_the_same_404(seeded: Api) -> None:
    real = seeded.client.app.state.open_ctx

    def not_synthetic() -> Any:
        ctx = real()
        ctx.store.synthetic = False
        return ctx

    seeded.client.app.state.open_ctx = not_synthetic
    assert get(seeded).content == get(seeded, "/no-such-path").content
    assert [(r["resource"], r["outcome"]) for r in rows(seeded)] == [("-", "not_found")]


# --- The audit row ---------------------------------------------------------------------------------


def test_one_row_a_minute_for_each_unit_and_none_names_the_hours(seeded: Api) -> None:
    for _ in range(3):
        get(seeded)
        get(seeded, "/wards/W4/open-view/analytics?hours=5")
        get(seeded, "/wards/ICU/open-view/analytics")
        get(seeded, "/wards/W9/open-view/analytics")
    assert [(r["clinician_id"], r["role"], r["resource"], r["outcome"]) for r in rows(seeded)] == [
        (None, None, "W4", "ok"), (None, None, "ICU", "ok"), (None, None, "-", "not_found"),
    ]  # fmt: skip
    seeded.wall.now += timedelta(seconds=61)
    get(seeded)
    assert [(r["resource"], r["outcome"]) for r in rows(seeded)][-1] == ("W4", "ok")
    assert seeded.sql("SELECT * FROM audit_log WHERE action = 'view_open_ward'") == []
