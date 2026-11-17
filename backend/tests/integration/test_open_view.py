"""GET /wards/{ward}/open-view (contract-open-view v1.4): the ward for a watcher nobody identified.
The route exists only with BIOSENTRY_OPEN_WARD_VIEW on, reads no credential, and answers from an
allow-list: a bed, its rank and its NEWS2 band, and the simulator's figures with no name or id."""

import json
import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_api import PATH_VALUES, PUBLIC_ROUTES, VALID_BODIES, _route_table
from test_model_inputs import stay
from test_overview import ADMIN, add_admin, keys
from test_store import Api, admission, api, batch, new_run, sim_patient, start  # noqa: F401 (api: fixture)

from biosentry.api.app import OPEN_VIEW_WARNING, create_app
from biosentry.core.config import Settings
from biosentry.repositories import postgres
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.services import ward as ward_module
from biosentry.services.scoring import decide

NOT_FOUND = {"detail": "Not Found"}  # the framework's own, not the project's "Not found"
URL = "/wards/W4/open-view"
TOP = {"ward", "synthetic", "can_control", "sim", "beds", "capacity", "nurses_on_duty", "awaiting_icu", "outliers", "er", "transfers_24h", "deaths_24h"}
SIM = {"clockS", "speed", "ended", "staff", "moves"}
STAFF = {"key", "role", "x", "y", "facing", "walking", "moved", "stage", "timer"}
MOVE = {"seq", "bed", "kind"}
BED = {"bed", "state", "rank", "risk"}


@pytest.fixture
def watch(api: Api) -> Api:
    """The same database, served by an API with the setting on."""
    api.client = TestClient(create_app(replace(api.settings, open_ward_view=True), clock=api.wall))
    return api


def figure(i: int = 0, **over: Any) -> dict[str, Any]:
    return {
        "id": f"STAFFID-{i}", "name": f"STAFFNAME-{i}", "title": f"STAFFTITLE-{i}", "role": "rn",
        "x": 10.5, "y": 20, "path": [[1, 2]], "facing": "back", "moved": 3, "stage": "message",
        "timer": -5, "task": {"kind": "TASKKIND", "patient": "TASKPATIENT", "rn": "TASKRN"}, **over,
    }  # fmt: skip


def shared(**over: Any) -> dict[str, Any]:
    return {
        "clockS": 120, "speed": 10, "ended": False, "counts": {"received": 5, "rejected": 1},
        "staff": [figure(0), figure(1, role="md", path=[], facing="front", stage="home")],
        "tasks": [{"kind": "TASKKIND", "patient": "TASKPATIENT", "rn": "TASKRN"}],
        "moves": [{"bed": "9A", "kind": "discharged", "seq": 1}, {"bed": "3B", "kind": "in", "seq": 2}],
        "beds": ["9A"], "secret": "SECRETKEY", **over,
    }  # fmt: skip


def publish(api: Api, run_id: int, shared: Any) -> None:
    body = {"shared": shared, "patients": [{"id": "s-1", "view": {"name": "VIEWNAME", "bed": "VIEWBED"}}]}
    assert api.post(f"/sim/runs/{run_id}/view", body).status_code == 204


def get(api: Api, path: str = URL, **headers: str) -> Any:
    return api.client.get(path, headers=headers)


def rows(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT clinician_id, role, action, resource, outcome FROM audit_log WHERE action = 'view_open_ward' ORDER BY seq")


def opened(api: Api, ward: str = "W4") -> dict[str, Any]:
    response = get(api, f"/wards/{ward}/open-view")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- Off: the route does not exist --------------------------------------------------------------


def test_off_the_path_is_an_unknown_path_and_writes_nothing(api: Api) -> None:
    unknown = get(api, "/no-such-path")
    assert (unknown.status_code, unknown.json()) == (404, NOT_FOUND)
    for headers in ({}, api.as_("rn-reyes")):
        response = get(api, **headers)
        assert (response.status_code, response.content) == (404, unknown.content)
    assert all(path != "/wards/{ward}/open-view" for _, path in _route_table(api.client.app))
    assert api.sql("SELECT * FROM audit_log WHERE action <> 'sign_in'") == []


@pytest.mark.parametrize("method", ["post", "head", "options"])
def test_off_other_methods_are_unknown_too(api: Api, method: str) -> None:
    assert getattr(api.client, method)(URL).status_code == 404
    assert rows(api) == []


# --- On: no credential is read -------------------------------------------------------------------


def test_on_it_answers_with_no_header_and_the_same_bytes_with_any_credential(watch: Api) -> None:
    start(watch)
    plain = get(watch)
    assert plain.status_code == 200
    token = watch.as_("md-santos")["Authorization"]
    for headers in (
        {"Authorization": token}, {"Authorization": "Bearer junk"}, {"Cookie": "session=x"},
        {"X-Ingest-Key": "test-ingest-key"}, {"Authorization": "Basic abc"},
    ):  # fmt: skip
        assert get(watch, **headers).content == plain.content
    assert [r for r in watch.sql("SELECT * FROM audit_log WHERE action = 'authenticate'")] == []
    assert plain.headers["cache-control"] == "no-store"


def test_on_the_route_table_gains_only_this_route_and_every_other_still_wants_a_token(watch: Api) -> None:
    off = create_app(replace(watch.settings, open_ward_view=False), clock=watch.wall)
    table = _route_table(watch.client.app)
    opened = ["/open-view", "/wards/{ward}/open-view", "/wards/{ward}/open-view/beds/{bed}", "/wards/{ward}/open-view/analytics"]
    assert sorted(set(table) - set(_route_table(off))) == [("GET", path) for path in sorted(opened)]
    assert set(_route_table(off)) <= set(table)
    cases = [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}, {"Authorization": "Bearer rn-nobody"}]
    walked = 0
    for method, template in table:
        if (method, template) in PUBLIC_ROUTES or template in opened:
            continue
        for headers in cases:
            response = watch.client.request(
                method, template.format(**PATH_VALUES), headers=headers, json=VALID_BODIES.get((method, template))
            )
            assert response.status_code == 401, (method, template, headers)
            walked += 1
    assert walked >= 12 * len(cases)
    assert get(watch, "/wards/W4/live").status_code == 401
    assert get(watch, "/wards/W4/live", **watch.as_("rn-manalo")).status_code == 200


def test_on_the_other_reads_are_the_same_bytes_as_off(watch: Api) -> None:
    run_id = start(watch)
    publish(watch, run_id, shared())
    off = TestClient(create_app(replace(watch.settings, open_ward_view=False), clock=watch.wall))
    for who, paths in (
        ("rn-delacruz", ["/wards/W4/rows", "/wards/W4/live", "/patients/s-1", "/patients", "/wards/W4/stays"]),
    ):
        for path in paths:
            headers = watch.as_(who)
            assert watch.client.get(path, headers=headers).content == off.get(path, headers=headers).content, path


def test_on_the_ward_list_runs_the_same_statements(watch: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    start(watch)
    off = TestClient(create_app(replace(watch.settings, open_ward_view=False), clock=watch.wall))
    headers = watch.as_("rn-delacruz")
    seen = [0]
    real = psycopg.Connection.execute

    def counting(self: Any, *args: Any, **kw: Any) -> Any:
        seen[0] += 1
        return real(self, *args, **kw)

    for client in (watch.client, off):  # the first read of a minute writes its audit row; the next does not
        assert client.get("/wards/W4/rows", headers=headers).status_code == 200
    monkeypatch.setattr(psycopg.Connection, "execute", counting)
    counts = []
    for client in (watch.client, off):
        seen[0] = 0
        assert client.get("/wards/W4/rows", headers=headers).status_code == 200
        counts.append(seen[0])
    assert counts[0] == counts[1] > 0


# --- 404 -------------------------------------------------------------------------------------------


def test_a_wrong_ward_is_the_framework_404_and_the_row_holds_no_path_text(watch: Api) -> None:
    for path in ("/wards/W9/open-view", "/wards/" + "x" * 300 + "/open-view", "/wards/a%00b/open-view", "/wards/%E2%82%AC%20'--/open-view"):
        response = get(watch, path)
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
        assert response.headers["cache-control"] == "no-store"
    assert rows(watch) == [{"clinician_id": None, "role": None, "action": "view_open_ward", "resource": "-", "outcome": "not_found"}]
    assert get(watch, "/no-such-path").content == get(watch, "/wards/W9/open-view").content


def test_a_run_that_is_not_synthetic_is_the_same_404(watch: Api) -> None:
    real = watch.client.app.state.open_ctx

    def not_synthetic() -> Any:
        ctx = real()
        ctx.store.synthetic = False
        return ctx

    watch.client.app.state.open_ctx = not_synthetic
    response = get(watch)
    assert (response.status_code, response.content) == (404, get(watch, "/no-such-path").content)
    assert [(r["resource"], r["outcome"]) for r in rows(watch)] == [("-", "not_found")]


def test_a_ward_of_more_than_100_patients_is_a_404_and_100_is_served(watch: Api) -> None:
    def ward(count: int) -> list[dict[str, Any]]:
        return [{**sim_patient(f"c-{i}"), "bed": f"B{i}"} for i in range(count)]

    start(watch, patients=ward(100))
    assert len(opened(watch)["beds"]) == 100
    start(Api(watch.url), patients=ward(101), client_id="client-a")
    assert get(watch).status_code == 404
    assert [r["outcome"] for r in rows(watch)] == ["ok", "not_found"]


# --- The answer ------------------------------------------------------------------------------------


def test_the_answer_has_exactly_the_contracts_keys_at_every_level(watch: Api) -> None:
    run_id = start(watch)
    publish(watch, run_id, shared())
    body = opened(watch)
    assert set(body) == TOP and set(body["sim"]) == SIM
    assert all(set(s) == STAFF for s in body["sim"]["staff"]) and body["sim"]["staff"]
    assert all(set(m) == MOVE for m in body["sim"]["moves"]) and body["sim"]["moves"]
    assert all(set(b) == BED for b in body["beds"]) and body["beds"]
    assert body["sim"] == {
        "clockS": 120, "speed": 10, "ended": False,
        "staff": [
            {"key": 0, "role": "rn", "x": 10.5, "y": 20, "facing": "back", "walking": True, "moved": 3, "stage": "message", "timer": -5},
            {"key": 1, "role": "md", "x": 10.5, "y": 20, "facing": "front", "walking": False, "moved": 3, "stage": "home", "timer": -5},
        ],
        "moves": [{"seq": 1, "bed": "9A", "kind": "out"}, {"seq": 2, "bed": "3B", "kind": "in"}],
    }  # fmt: skip
    assert (body["ward"], body["synthetic"], body["can_control"]) == ("W4", True, False)
    assert (body["capacity"], body["nurses_on_duty"], body["awaiting_icu"], body["outliers"], body["er"]) == (None, None, None, None, None)
    assert (body["transfers_24h"], body["deaths_24h"]) == (0, 0)
    assert body["beds"] == [{"bed": "9A", "state": "ranked", "rank": 1, "risk": "high"}]


def test_can_control_is_false_even_for_a_run_a_physician_could_control(watch: Api) -> None:
    start(watch)
    token = watch.as_("md-santos")
    assert get(watch, "/wards/W4/live", **token).json()["can_control"] is True
    assert opened(watch)["can_control"] is False
    assert get(watch, **token).json()["can_control"] is False


def test_no_free_text_of_the_store_reaches_the_answer(watch: Api) -> None:
    marked = {
        **sim_patient("MARKID-1"), "name": "MARKNAME", "context": "MARKCONTEXT",
        "story": "MARKSTORY", "bed": "<b>MARKBED",
    }  # fmt: skip
    ok = {**sim_patient("MARKID-2", rn="rn-reyes", md=None), "name": "MARKNAME2", "bed": "8C"}
    run_id = start(watch, patients=[marked, ok])
    publish(watch, run_id, shared(moves=[{"bed": "3B", "kind": "in", "seq": 1}]))
    note = {"author": "MARKAUTHOR", "role": "RN", "content": "MARKNOTETEXT"}
    assert watch.client.post("/patients/MARKID-2/notes", json=note, headers=watch.as_("rn-reyes")).status_code == 201
    text = get(watch).text
    for word in ("MARK", "STAFF", "TASK", "SECRET", "VIEW", "counts", "path", "title", "name", "context", "rn-", "md-", "s-1"):
        assert word not in text, word
    body = json.loads(text)
    assert [b["bed"] for b in body["beds"]] == ["8C"]  # the label that breaks the rule is left out
    assert keys(body) == TOP | SIM | STAFF | MOVE | BED


def test_the_beds_are_the_wards_own_ranking_for_an_admin(watch: Api) -> None:
    add_admin(watch)  # the fixture ward: ranked, and two NOT RANKED
    body = opened(watch)
    assert body["sim"] is None  # nothing published for this run
    listed = watch.client.get("/wards/W4/rows", headers=watch.as_(ADMIN)).json()
    store = PostgresStore(connect(watch.url), watch.wall)
    expected = [
        {"bed": r["bed"], "state": "ranked", "rank": r["rank"], "risk": decide(store.read(r["id"])).news2.risk.value}
        for r in listed["ranked"]
    ] + [{"bed": a["bed"], "state": "not_ranked", "rank": None, "risk": None} for a in listed["abstained"]]  # fmt: skip
    assert body["beds"] == sorted(expected, key=lambda b: (b["bed"], b["rank"] or 0))
    assert len(body["beds"]) == 12 and {b["risk"] for b in body["beds"]} >= {"high", "medium", "low"}
    assert [b["risk"] is None for b in body["beds"]] == [b["state"] == "not_ranked" for b in body["beds"]]


def test_the_models_do_not_run_and_the_answer_holds_no_model_text(watch: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any) -> Any:
        raise AssertionError("a model ran for the open view")

    monkeypatch.setattr(ward_module, "ward_model_signals", boom)
    text = get(watch).text
    assert "model" not in text and "reason" not in text and "prediction" not in text


def test_a_discharged_patient_has_no_bed(watch: Api) -> None:
    run_id = start(watch)
    body = batch(minute=5, admissions=[admission("s-2")], departures=[{"patient_id": "s-1", "minute": 5, "outcome": "discharged"}])
    assert watch.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert [b["bed"] for b in opened(watch)["beds"]] == ["9A"]  # s-2 only; s-1 left


def test_a_ranked_and_a_not_ranked_patient_in_a_simulated_run(watch: Api) -> None:
    gone = {"patient_id": "n-1", "code": "blood_pressure", "minute": -10, "source": "nurse", "quality": "missing", "absent_reason": "not_recorded"}
    start(watch, patients=[stay("r-1", 3), {**stay("n-1", 3), "bed": "2D", "observations": [gone]}])
    assert [(b["bed"], b["state"], b["rank"]) for b in opened(watch)["beds"]] == [("2D", "not_ranked", None), ("9A", "ranked", 1)]


# --- sim: no partial view -------------------------------------------------------------------------

BAD = {
    "no view": None,
    "21 staff": {"staff": [figure(i) for i in range(21)]},
    "21 moves": {"moves": [{"bed": "9A", "kind": "in", "seq": i + 1} for i in range(21)]},
    "x is text": {"staff": [figure(0, x="10")]},
    "x out of range": {"staff": [figure(0, x=101)]},
    "speed is a bool": {"speed": True},
    "x is a bool": {"staff": [figure(0, x=True)]},
    "y is a bool": {"staff": [figure(0, y=False)]},
    "moved is a bool": {"staff": [figure(0, moved=True)]},
    "timer is a bool": {"staff": [figure(0, timer=True)]},
    "move seq is a bool": {"moves": [{"bed": "9A", "kind": "in", "seq": True}]},
    "speed 5": {"speed": 5},
    "ended is 1": {"ended": 1},
    "negative clock": {"clockS": -1},
    "path is text": {"staff": [figure(0, path="abc")]},
    "path missing": {"staff": [{k: v for k, v in figure(0).items() if k != "path"}]},
    "unknown role": {"staff": [figure(0, role="nurse")]},
    "unknown stage": {"staff": [figure(0, stage="sleep")]},
    "move kind": {"moves": [{"bed": "9A", "kind": "teleport", "seq": 1}]},
    "move kind is a list": {"moves": [{"bed": "9A", "kind": [], "seq": 1}]},
    "move bed": {"moves": [{"bed": "<script>", "kind": "in", "seq": 1}]},
    "move seq 0": {"moves": [{"bed": "9A", "kind": "in", "seq": 0}]},
    "staff is text": {"staff": "abc"},
    "moves is missing": {"moves": None},
}


@pytest.mark.parametrize("name", BAD)
def test_a_view_that_is_not_as_read_gives_a_null_sim_and_the_beds_are_still_served(watch: Api, name: str) -> None:
    run_id = start(watch)
    if BAD[name] is not None:
        publish(watch, run_id, {**shared(), **BAD[name]})
    body = opened(watch)
    assert body["sim"] is None
    assert [b["bed"] for b in body["beds"]] == ["9A"]


def test_the_largest_valid_view_is_served_and_small(watch: Api) -> None:
    run_id = start(watch)
    publish(watch, run_id, shared(staff=[figure(i) for i in range(20)], moves=[{"bed": "9A", "kind": "in", "seq": 2**53 - 1}] * 20))
    body = opened(watch)
    assert len(body["sim"]["staff"]) == len(body["sim"]["moves"]) == 20
    assert len(json.dumps(body)) < 65_536


def test_a_shared_part_that_is_not_as_read_is_a_null_sim(watch: Api) -> None:
    run_id = start(watch)
    body = {"shared": {"staff": 1}, "patients": []}
    assert watch.post(f"/sim/runs/{run_id}/view", body).status_code == 204
    assert opened(watch)["sim"] is None


# --- Audit -------------------------------------------------------------------------------------------


def test_one_ok_row_a_minute_for_all_callers_and_one_not_found_row(watch: Api) -> None:
    start(watch)
    for _ in range(5):
        opened(watch)
        get(watch, "/wards/W9/open-view")
        get(watch, **watch.as_("rn-reyes"))
    watch.wall.now += timedelta(seconds=30)
    opened(watch)
    assert [(r["clinician_id"], r["role"], r["resource"], r["outcome"]) for r in rows(watch)] == [
        (None, None, "W4", "ok"), (None, None, "-", "not_found"),
    ]  # fmt: skip
    watch.wall.now += timedelta(seconds=31)  # 61 seconds after the first
    opened(watch)
    get(watch, "/wards/W9/open-view")
    assert [r["outcome"] for r in rows(watch)] == ["ok", "not_found", "ok", "not_found"]
    assert watch.sql("SELECT * FROM audit_log WHERE action = 'authenticate'") == []


# --- Methods, headers ------------------------------------------------------------------------------------


def test_head_and_post_are_405_and_write_nothing(watch: Api) -> None:
    for method in ("head", "post", "delete", "put"):
        response = getattr(watch.client, method)(URL)
        assert response.status_code == 405, method
    assert watch.client.post(URL).json() == {"detail": "Method Not Allowed"}
    assert rows(watch) == []


def test_the_answer_is_not_stored_and_cors_is_unchanged(watch: Api) -> None:
    ok, missing = get(watch), get(watch, "/wards/W9/open-view")
    assert ok.headers["cache-control"] == missing.headers["cache-control"] == "no-store"
    own = watch.settings.cors_origins[0]
    assert get(watch, Origin=own).headers["access-control-allow-origin"] == own
    assert "access-control-allow-origin" not in get(watch, Origin="https://elsewhere.example").headers
    preflight = watch.client.options(URL, headers={"Origin": own, "Access-Control-Request-Method": "GET"})
    assert preflight.headers["access-control-allow-origin"] == own
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


# --- Commands are not opened ----------------------------------------------------------------------------------


def test_the_commands_route_still_needs_a_token_and_a_role(watch: Api) -> None:
    body = {"kind": "reset"}
    assert watch.client.post("/wards/W4/sim/commands", json=body).status_code == 401
    assert watch.client.post("/wards/W4/sim/commands", json=body, headers=watch.as_("rn-reyes")).status_code == 403
    assert watch.sql("SELECT * FROM sim_command") == []


# --- Start-up ---------------------------------------------------------------------------------------------


def test_on_the_start_logs_the_one_warning_line_and_off_logs_none(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    line = (
        "BIOSENTRY_OPEN_WARD_VIEW is on: GET /wards/{ward}/open-view shows the ward to anyone who can reach"
        " this API, without sign-in. Never use it on a public address."
    )
    assert OPEN_VIEW_WARNING == line
    with caplog.at_level(logging.INFO):
        create_app(api.settings, clock=api.wall)
        assert [r for r in caplog.records if "OPEN_WARD_VIEW" in r.getMessage()] == []
        create_app(replace(api.settings, open_ward_view=True), clock=api.wall)
    warned = [r for r in caplog.records if "OPEN_WARD_VIEW" in r.getMessage()]
    assert [(r.name, r.levelno, r.getMessage()) for r in warned] == [("biosentry.api.app", logging.WARNING, line)]


def test_in_production_mode_the_app_starts_with_the_setting_on_and_warns(
    api: Api, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BIOSENTRY_ENV", "production")
    monkeypatch.setattr(postgres, "DEV_DATABASE_PASSWORD", "something-else")  # let the test database pass the guard
    settings = replace(api.settings, open_ward_view=True)
    with caplog.at_level(logging.WARNING):
        app = create_app(settings, clock=api.wall)
    assert [r.getMessage() for r in caplog.records if "OPEN_WARD_VIEW" in r.getMessage()] == [OPEN_VIEW_WARNING]
    assert ("GET", "/wards/{ward}/open-view") in _route_table(app)


def test_one_read_runs_three_statements_for_each_patient_and_seven_of_its_own(watch: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seven: the time zone, run, ward patients, stored view, operations, outcomes, and audit count
    (an eighth, the audit insert, once a minute). No statement belongs to a model."""
    opened(watch)  # the minute's row is written
    seen = [0]
    real = psycopg.Connection.execute

    def counting(self: Any, *args: Any, **kw: Any) -> Any:
        seen[0] += 1
        return real(self, *args, **kw)

    monkeypatch.setattr(psycopg.Connection, "execute", counting)
    opened(watch)
    assert seen[0] == 7 + 3 * 12  # the fixture ward holds 12 patients


# --- The unit list: GET /open-view ----------------------------------------------------------------

UNITS_URL = "/open-view"
HOSPITAL = [
    {"id": "ER", "name": "Emergency Room", "floor": 0, "kind": "shown"},
    {"id": "ICU", "name": "Intensive Care", "floor": 2, "kind": "unranked"},
    {"id": "W4", "name": "Ward W4", "floor": 4, "kind": "ranked"},
]


def test_off_the_unit_list_is_an_unknown_path_and_writes_nothing(api: Api) -> None:
    unknown = get(api, "/no-such-path")
    for headers in ({}, api.as_("rn-reyes")):
        response = get(api, UNITS_URL, **headers)
        assert (response.status_code, response.content) == (404, unknown.content)
    assert all(path != UNITS_URL for _, path in _route_table(api.client.app))
    for method in ("post", "head", "options"):
        assert getattr(api.client, method)(UNITS_URL).status_code == 404
    assert rows(api) == []


def test_the_unit_list_reads_no_credential_and_is_the_same_bytes_for_every_caller(watch: Api) -> None:
    start(watch)
    plain = get(watch, UNITS_URL)
    assert plain.status_code == 200 and plain.headers["cache-control"] == "no-store"
    token = watch.as_("md-santos")["Authorization"]
    for headers in (
        {"Authorization": token}, {"Authorization": "Bearer junk"}, {"Cookie": "session=x"},
        {"X-Ingest-Key": "test-ingest-key"}, {"Authorization": "Basic abc"},
    ):  # fmt: skip
        assert get(watch, UNITS_URL, **headers).content == plain.content
    assert watch.sql("SELECT * FROM audit_log WHERE action = 'authenticate'") == []


def test_an_old_run_of_one_unit_answers_that_one_unit_with_exactly_the_contracts_keys(watch: Api) -> None:
    start(watch)  # a body with no units: one ranked unit, W4
    assert get(watch, UNITS_URL).json() == {
        "synthetic": True,
        "units": [{"id": "W4", "name": "Ward W4", "floor": None, "kind": "ranked", "score_note": None, "capacity": None}],
    }


def test_the_unit_list_is_the_runs_units_in_order_and_holds_no_count_and_no_person(watch: Api) -> None:
    patients = [
        {**sim_patient("PID-MARK-1", "cn-er", None), "unit": "ER", "name": "NAME-MARK", "bed": "BED-MARK", "person": "PERSON-MARK"},
        {**sim_patient("PID-MARK-2", "rn-delacruz", "md-santos"), "unit": "W4"},
    ]
    made = watch.post("/sim/runs", {**new_run(patients=patients), "units": HOSPITAL})
    assert made.status_code == 201, made.text
    response = get(watch, UNITS_URL)
    assert response.json() == {"synthetic": True, "units": [{**u, "score_note": None, "capacity": None} for u in HOSPITAL]}
    for mark in ("MARK", "cn-er", "rn-delacruz", "md-santos", "patients", "clinician"):
        assert mark not in response.text, mark


def test_the_unit_list_of_a_run_that_is_not_synthetic_is_the_framework_404(watch: Api) -> None:
    real = watch.client.app.state.open_ctx

    def not_synthetic() -> Any:
        ctx = real()
        ctx.store.synthetic = False
        return ctx

    watch.client.app.state.open_ctx = not_synthetic
    response = get(watch, UNITS_URL)
    assert (response.status_code, response.content) == (404, get(watch, "/no-such-path").content)
    assert [(r["resource"], r["outcome"]) for r in rows(watch)] == [("*", "not_found")]


def test_the_unit_list_writes_one_row_a_minute_with_no_clinician_and_its_own_resource(watch: Api) -> None:
    start(watch)
    for _ in range(5):
        get(watch, UNITS_URL)
        get(watch, UNITS_URL, **watch.as_("rn-reyes"))
    opened(watch)  # the ward's own open view keeps its own row
    assert [(r["clinician_id"], r["role"], r["resource"], r["outcome"]) for r in rows(watch)] == [
        (None, None, "*", "ok"), (None, None, "W4", "ok"),
    ]  # fmt: skip
    watch.wall.now += timedelta(seconds=61)
    get(watch, UNITS_URL)
    assert [(r["resource"], r["outcome"]) for r in rows(watch)] == [("*", "ok"), ("W4", "ok"), ("*", "ok")]


def test_the_unit_list_takes_get_only(watch: Api) -> None:
    start(watch)
    assert [getattr(watch.client, m)(UNITS_URL).status_code for m in ("post", "head", "put", "delete")] == [405] * 4
    assert rows(watch) == []
