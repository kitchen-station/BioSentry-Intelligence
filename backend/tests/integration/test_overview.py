"""The admin role: the read-only ward overview, a write refused on every route, no role that a
route can change, and the command that grants and revokes an admin."""

import io
import json
import runpy
import sys
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from conftest import dump_database
from fastapi.testclient import TestClient
from test_api import PATH_VALUES, PUBLIC_ROUTES, VALID_BODIES, _route_table
from test_store import DATA, NOW, Api, api, new_run, sim_patient  # noqa: F401 (api: fixture)

from biosentry.api.app import REFUSED_ACTIONS, WRITE_ACTIONS, create_app
from biosentry.core.audit import AuditLog
from biosentry.core.config import Settings
from biosentry.db import admin as admin_module
from biosentry.db import passphrase as passphrase_module
from biosentry.db.passphrase import hash_passphrase
from biosentry.db.seed import DEV_PASSPHRASE
from biosentry.repositories.postgres import PostgresStore, connect
from biosentry.schemas import WardOverview
from biosentry.services.ward import WardService

ADMIN = "admin-ops"
IDS = [r["id"] for r in DATA["ranked"]] + [a["id"] for a in DATA["abstained"]]
FORBIDDEN_KEYS = {"name", "age", "sex", "vitals", "notes", "admitting_context", "labs"}


def write(api: Api, statement: str, *args: Any) -> None:
    with connect(api.url) as conn:
        conn.execute(statement, args)


def add_admin(api: Api, admin_id: str = ADMIN) -> dict[str, str]:
    """An admin with the dev passphrase, signed in (the grant command sets none)."""
    write(
        api,
        "INSERT INTO clinician (id, role, passphrase_hash) VALUES (%s, 'admin', %s)",
        admin_id,
        hash_passphrase(DEV_PASSPHRASE),
    )
    return api.as_(admin_id)


def overview(api: Api, who: str = ADMIN, ward: str = "W4") -> Any:
    return api.client.get(f"/wards/{ward}/overview", headers=api.as_(who))


def audit_of(api: Api, who: str) -> list[tuple[str, str, str]]:
    rows = api.sql(
        "SELECT action, resource, outcome FROM audit_log"
        " WHERE clinician_id = %s AND action <> 'sign_in' ORDER BY seq",
        who,
    )
    return [(r["action"], r["resource"], r["outcome"]) for r in rows]


def keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys(v)}
    return set()


def snapshot(api: Api) -> list[str]:
    """Every row but the audit trail and the sessions, which a refused request still adds to."""
    with connect(api.url) as conn:
        text = dump_database(conn)
    return [ln for ln in text.splitlines() if not ln.startswith(("audit_log:", "session:"))]


# --- The overview --------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["rn-reyes", "md-santos", "rn-manalo"])
def test_a_clinician_who_is_not_an_admin_is_refused_and_it_is_audited(api: Api, who: str) -> None:
    response = overview(api, who)
    assert response.status_code == 403 and response.json() == {"detail": "Forbidden"}
    assert "p-001" not in response.text
    assert audit_of(api, who) == [("view_overview", "W4", "denied")]


def test_a_non_admin_is_refused_for_a_wrong_ward_too(api: Api) -> None:
    assert overview(api, "rn-reyes", "W9").status_code == 403
    assert audit_of(api, "rn-reyes") == [("view_overview", "W9", "denied")]


def test_the_admin_sees_every_patient_on_the_ward_and_one_audit_row_is_written(api: Api) -> None:
    add_admin(api)
    response = overview(api)
    assert response.status_code == 200
    body = WardOverview.model_validate(response.json())
    assert audit_of(api, ADMIN) == [("view_overview", "W4", "ok")]
    assert api.sql("SELECT role FROM audit_log WHERE action = 'view_overview'") == [
        {"role": "admin"}
    ]
    assert sorted(p.id for p in body.patients) == sorted(IDS) and len(IDS) == 12
    store = PostgresStore(connect(api.url), api.wall)
    ranked, abstained = WardService(store, AuditLog(store.conn, api.wall, store.role_of)).compute(
        store.on_ward()
    )
    assert [(p.id, p.state, p.score) for p in body.patients] == [
        *((r.id, "ranked", r.score) for r in ranked),
        *((a.id, "not_ranked", None) for a in abstained),
    ]
    assert all(p.reason for p in body.patients if p.state == "not_ranked")
    assert all("rn-manalo" in p.care for p in body.patients)  # the charge nurse is on every one
    assert keys(response.json()) & FORBIDDEN_KEYS == set()
    assert ADMIN not in [c.id for c in body.clinicians]
    reyes = next(c for c in body.clinicians if c.id == "rn-reyes")
    assert reyes.patients == len(DATA["clinicians"]["rn-reyes"]["patients"])
    assert body.clinicians[0].id == min(c.id for c in body.clinicians)


def test_no_patient_name_appears_anywhere_in_the_overview(api: Api) -> None:
    add_admin(api)
    text = overview(api).text
    for p in [*DATA["ranked"], *DATA["abstained"]]:
        assert p["name"] not in text


def test_a_wrong_ward_is_a_404_and_audited_as_not_found(api: Api) -> None:
    add_admin(api)
    assert overview(api, ward="W9").status_code == 404
    assert audit_of(api, ADMIN) == [("view_overview", "W9", "not_found")]


def test_a_patient_who_has_left_is_not_on_the_overview(api: Api) -> None:
    add_admin(api)
    write(api, "UPDATE patient SET left_at = %s, outcome = 'discharged' WHERE id = 'p-001'", NOW)
    body = overview(api).json()
    assert "p-001" not in [p["id"] for p in body["patients"]]
    assert all("p-001" not in json.dumps(c) for c in body["clinicians"])


def test_the_shared_view_is_served_without_patient_entries(api: Api) -> None:
    add_admin(api)
    assert overview(api).json()["shared"] is None
    write(
        api,
        "UPDATE run SET live_view = %s::jsonb WHERE id = (SELECT max(id) FROM run)",
        json.dumps({"shared": {"clock": "08:00"}, "patients": [{"id": "p-001", "note": "x"}]}),
    )
    body = overview(api).json()
    assert body["shared"] == {"clock": "08:00"}
    assert "patients" not in body["shared"] and "note" not in json.dumps(body)


# --- Activity ------------------------------------------------------------------------------


def log(api: Api, who: str | None, role: str | None, action: str, at: Any = NOW) -> None:
    write(
        api,
        "INSERT INTO audit_log (at, clinician_id, role, action, resource, outcome)"
        " VALUES (%s, %s, %s, %s, 'p-001', 'ok')",
        at,
        who,
        role,
        action,
    )


def test_activity_is_the_last_ten_per_clinician_in_the_last_day_without_sign_ins(
    api: Api,
) -> None:
    add_admin(api)
    for i in range(12):
        log(api, "rn-reyes", "rn", f"act{i}")
    log(api, "rn-reyes", "rn", "old", NOW - timedelta(hours=25))
    log(api, "md-santos", "md", "sign_in")
    log(api, "md-santos", "md", "authenticate")
    log(api, "md-santos", "md", "view_ward")
    log(api, None, None, "view_ward")
    log(api, "rn-manalo", "charge_rn", "view_ward")
    activity = overview(api).json()["activity"]
    reyes = [a["action"] for a in activity if a["clinician_id"] == "rn-reyes"]
    assert reyes == [f"act{i}" for i in range(11, 1, -1)]  # newest first, ten of twelve
    assert [a["action"] for a in activity if a["clinician_id"] == "md-santos"] == ["view_ward"]
    assert {a["clinician_id"] for a in activity} == {"rn-reyes", "md-santos", "rn-manalo"}
    assert [a["clinician_id"] for a in activity] == sorted(a["clinician_id"] for a in activity)
    assert all(set(a) == {"seq", "at", "clinician_id", "action", "resource", "outcome"} for a in activity)
    assert activity[0]["at"].endswith("Z")


def test_a_note_never_reaches_the_overview_and_typed_text_in_a_failed_row_is_hidden(
    api: Api,
) -> None:
    add_admin(api)
    pid = DATA["clinicians"]["rn-reyes"]["patients"][0]
    note = {"author": "a", "role": "RN", "content": "MARKER-7f3a"}
    headers = api.as_("rn-reyes")
    assert api.client.post(f"/patients/{pid}/notes", json=note, headers=headers).status_code == 201
    assert api.client.get("/patients/Juan%20Cruz", headers=headers).status_code == 404
    response = overview(api)
    assert "MARKER-7f3a" not in response.text and "Juan Cruz" not in response.text
    seen = [(a["action"], a["resource"], a["outcome"]) for a in response.json()["activity"]]
    assert ("add_note", pid, "ok") in seen
    assert ("view_patient", "-", "not_found") in seen


# --- The admin writes nothing --------------------------------------------------------------


def _writes(api: Api) -> list[tuple[str, str]]:
    table = _route_table(api.client.app)
    return [r for r in table if r not in PUBLIC_ROUTES and r[0] != "GET"]


def _expected_resource(template: str) -> str:
    if "{patient_id}" in template:
        return PATH_VALUES["patient_id"]
    return PATH_VALUES["ward"] if "{ward}" in template else "-"


def test_the_admin_is_refused_on_every_write_route_and_nothing_is_written(api: Api) -> None:
    headers = add_admin(api)
    writes = _writes(api)
    assert len(writes) == 9 and set(writes) == set(REFUSED_ACTIONS)
    before = snapshot(api)
    for method, template in writes:
        url = template.format(**PATH_VALUES)
        for body in (VALID_BODIES.get((method, template)), {"bogus": 1}):
            response = api.client.request(method, url, headers=headers, json=body)
            assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"}), (
                method,
                template,
                body,
            )
        # A body that is not JSON fails before any handler runs: still a 403, not a 422.
        raw = api.client.request(
            method, url, headers={**headers, "Content-Type": "application/json"}, content="{nope"
        )
        assert raw.status_code == 403, (method, template)
    assert snapshot(api) == before
    expected = [
        (REFUSED_ACTIONS[w], _expected_resource(w[1]), "denied") for w in writes for _ in range(3)
    ]
    assert audit_of(api, ADMIN) == expected


def test_the_write_actions_are_still_the_body_bearing_routes_only(api: Api) -> None:
    assert set(WRITE_ACTIONS) < set(REFUSED_ACTIONS)


def test_an_admin_reads_nothing_beyond_the_overview(api: Api) -> None:
    headers = add_admin(api)
    get = api.client.get
    rows = get("/wards/W4/rows", headers=headers).json()
    assert rows["ranked"] == [] and rows["abstained"] == []
    assert get("/patients", headers=headers).json() == []
    for path in ("/patients/p-001", "/patients/p-001/notes", "/patients/p-001/baseline"):
        assert get(path, headers=headers).status_code == 404, path
    assert get("/wards/W4/stays", headers=headers).json() == []
    live = get("/wards/W4/live", headers=headers).json()
    assert live["patients"] == [] and live["can_control"] is False
    assert get("/acknowledgements", headers=headers).json() == []
    mine = get("/audit", headers=headers).json()
    assert mine and {r["clinician_id"] for r in api.sql("SELECT clinician_id FROM audit_log")} == {ADMIN}


def test_the_charge_nurses_ward_is_byte_for_byte_the_same_once_an_admin_exists(api: Api) -> None:
    before = api.client.get("/wards/W4/rows", headers=api.as_("rn-manalo")).content
    add_admin(api)
    assert overview(api).status_code == 200
    after = api.client.get("/wards/W4/rows", headers=api.as_("rn-manalo")).content
    assert before == after


# --- Roles ---------------------------------------------------------------------------------


def test_no_route_with_any_token_can_change_a_role(api: Api) -> None:
    add_admin(api)
    tokens = [api.as_(c) for c in ("rn-reyes", "md-santos", "rn-manalo", ADMIN)] + [{}]
    before = api.sql("SELECT id, role FROM clinician ORDER BY id")
    for method, template in _route_table(api.client.app):
        if (method, template) in PUBLIC_ROUTES:
            continue
        url = template.format(**PATH_VALUES)
        for headers in tokens:
            api.client.request(method, url, headers=headers, json=VALID_BODIES.get((method, template)))
    assert api.sql("SELECT id, role FROM clinician ORDER BY id") == before


def test_the_database_refuses_a_role_change_and_an_admin_on_a_patient(api: Api) -> None:
    add_admin(api)
    for statement in (
        "UPDATE clinician SET role = 'admin' WHERE id = 'rn-reyes'",
        "UPDATE clinician SET role = 'rn' WHERE id = 'admin-ops'",
        "INSERT INTO clinician (id, role) VALUES ('x-1', 'boss')",
        "INSERT INTO care_assignment (run_id, patient_id, clinician_id)"
        " SELECT run_id, id, 'admin-ops' FROM patient LIMIT 1",
        "UPDATE care_assignment SET clinician_id = 'admin-ops' WHERE clinician_id = 'rn-reyes'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            api.sql(statement)


def test_a_session_names_the_stored_role(api: Api) -> None:
    add_admin(api)
    roles = {c: e["role"] for c, e in DATA["clinicians"].items()} | {ADMIN: "admin"}
    for who, role in roles.items():
        body = {"clinician_id": who, "passphrase": DEV_PASSPHRASE}
        assert api.client.post("/session", json=body).json()["role"] == role, who


# --- A sim run that names an admin ---------------------------------------------------------


def test_a_sim_run_naming_an_admin_as_nurse_is_refused_by_the_database_and_writes_nothing(
    api: Api,
) -> None:
    add_admin(api)
    api.client = TestClient(create_app(api.settings, clock=api.wall), raise_server_exceptions=False)
    runs = api.sql("SELECT count(*) AS n FROM run")
    patients = api.sql("SELECT count(*) AS n FROM patient")
    assigned = api.sql("SELECT count(*) AS n FROM care_assignment")
    response = api.post("/sim/runs", new_run(patients=[sim_patient(rn=ADMIN)]))
    print("status for an admin named as nurse:", response.status_code)
    assert response.status_code >= 400
    assert api.sql("SELECT count(*) AS n FROM run") == runs
    assert api.sql("SELECT count(*) AS n FROM patient") == patients
    assert api.sql("SELECT count(*) AS n FROM care_assignment") == assigned


# --- The grant command ---------------------------------------------------------------------


@pytest.fixture
def cli(api: Api, monkeypatch: pytest.MonkeyPatch) -> Api:
    monkeypatch.setenv("BIOSENTRY_DATABASE_URL", api.url)
    return api


def test_grant_makes_an_admin_who_cannot_sign_in_until_a_passphrase_is_set(
    cli: Api, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert admin_module.main(["grant", ADMIN]) == 0
    assert "granted" in capsys.readouterr().out
    assert cli.sql("SELECT role, passphrase_hash FROM clinician WHERE id = %s", ADMIN) == [
        {"role": "admin", "passphrase_hash": None}
    ]
    body = {"clinician_id": ADMIN, "passphrase": DEV_PASSPHRASE}
    assert cli.client.post("/session", json=body).status_code == 401
    monkeypatch.setattr(sys, "stdin", io.StringIO("a passphrase for admin\n"))
    assert passphrase_module.main([ADMIN]) == 0
    body["passphrase"] = "a passphrase for admin"
    signed = cli.client.post("/session", json=body)
    assert signed.status_code == 200 and signed.json()["role"] == "admin"
    headers = {"Authorization": f"Bearer {signed.json()['token']}"}
    assert cli.client.get("/wards/W4/overview", headers=headers).status_code == 200


@pytest.mark.parametrize("bad", ["rn-reyes", "admin-", "Admin-x", "admin-x y", "admin-" + "a" * 59])
def test_grant_refuses_a_bad_id_and_writes_nothing(cli: Api, bad: str) -> None:
    before = cli.sql("SELECT id FROM clinician ORDER BY id")
    with pytest.raises(SystemExit, match="an admin id is"):
        admin_module.main(["grant", bad])
    assert cli.sql("SELECT id FROM clinician ORDER BY id") == before


def test_grant_of_an_existing_id_changes_nothing(cli: Api) -> None:
    add_admin(cli)
    before = cli.sql("SELECT * FROM clinician ORDER BY id")
    with pytest.raises(SystemExit, match="already exists"):
        admin_module.main(["grant", ADMIN])
    assert cli.sql("SELECT * FROM clinician ORDER BY id") == before


def test_revoke_deletes_the_sessions_and_the_row_and_keeps_the_audit_rows(cli: Api) -> None:
    headers = add_admin(cli)
    assert overview(cli).status_code == 200
    assert admin_module.main(["revoke", ADMIN]) == 0
    assert cli.sql("SELECT 1 FROM clinician WHERE id = %s", ADMIN) == []
    assert cli.sql("SELECT 1 FROM session WHERE clinician_id = %s", ADMIN) == []
    assert cli.client.get("/wards/W4/overview", headers=headers).status_code == 401
    assert cli.sql(
        "SELECT 1 FROM audit_log WHERE clinician_id = %s AND action = 'view_overview'", ADMIN
    )


def test_revoke_only_touches_an_admin(cli: Api) -> None:
    with pytest.raises(SystemExit, match="no such admin"):
        admin_module.main(["revoke", "admin-nobody"])
    with pytest.raises(SystemExit, match="an admin id is"):
        admin_module.main(["revoke", "rn-reyes"])
    assert cli.sql("SELECT 1 FROM clinician WHERE id = 'rn-reyes'")


@pytest.mark.filterwarnings("ignore:.*found in sys.modules:RuntimeWarning")
def test_the_command_runs_as_a_module(cli: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["biosentry.db.admin", "grant", "admin-mod"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("biosentry.db.admin", run_name="__main__")
    assert exited.value.code == 0
    assert cli.sql("SELECT role FROM clinician WHERE id = 'admin-mod'") == [{"role": "admin"}]
