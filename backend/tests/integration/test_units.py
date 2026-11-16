"""Hospital units: a run holds 1 to 12 units, a patient row is one stay in one unit, and a unit is a
filter on top of the assignment, never a way around it (I7). `kind` decides whether a unit ranks."""

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from samples import admit_input  # isort: skip
from test_api import VALID_BODIES
from test_open_view import shared
from test_overview import ADMIN, add_admin, write
from test_store import Api, api, batch, new_run, obs, sim_patient, start  # noqa: F401 (api: fixture)

from biosentry.api.app import create_app
from biosentry.db.passphrase import hash_passphrase
from biosentry.db.seed import DEV_PASSPHRASE

UNITS = [
    {"id": "W4", "name": "Ward W4", "floor": 4, "kind": "ranked"},
    {"id": "ER", "name": "Emergency Room", "floor": 0, "kind": "shown"},
    {"id": "ICU", "name": "Intensive Care", "floor": 2, "kind": "unranked"},
    {"id": "MED3A", "name": "Medicine 3A", "floor": 3, "kind": "ranked"},
]
OTHERS = {"ER": "e-1", "ICU": "i-1", "MED3A": "m-1"}  # the patients md-santos is not assigned to


def stay(pid: str, unit: str, rn: str, md: str | None, bed: str, **over: Any) -> dict[str, Any]:
    return {**sim_patient(pid, rn, md), "unit": unit, "bed": bed, **over}


PATIENTS = [
    stay("w-1", "W4", "rn-delacruz", "md-santos", "4A"),
    stay("e-1", "ER", "cn-er", None, "E1"),
    stay("i-1", "ICU", "cn-icu", None, "I1"),
    stay("m-1", "MED3A", "cn-med3a", None, "M1"),
]


def hospital(api: Api, patients: list[dict[str, Any]] = PATIENTS) -> int:
    """A run of four units. The migration's charge nurses have no passphrase: give them one."""
    for cn in ("cn-er", "cn-icu", "cn-med3a"):
        write(api, "UPDATE clinician SET passphrase_hash = %s WHERE id = %s", hash_passphrase(DEV_PASSPHRASE), cn)
    response = api.post("/sim/runs", {**new_run(patients=patients), "units": UNITS})
    assert response.status_code == 201, response.text
    run_id: int = response.json()["run_id"]
    return run_id


def get(api: Api, who: str, path: str) -> Any:
    return api.client.get(path, headers=api.as_(who))


def send(api: Api, who: str, path: str, body: Any) -> Any:
    return api.client.post(path, json=body, headers=api.as_(who))


def depart(api: Api, run_id: int, *departures: dict[str, Any], **over: Any) -> Any:
    body = batch(minute=30, patients=[], observations=[], bins=[], events=[], departures=list(departures), **over)
    return api.post(f"/sim/runs/{run_id}/batch", body)


def reading(**over: Any) -> dict[str, Any]:
    """A good batch for w-1: a reading, a bin and an event."""
    return batch(
        minute=5,
        patients=[{"id": "w-1", "trend": "stable", "signal_quality": 90, "last_sample_minute": 5}],
        observations=[obs("w-1", "heart_rate", 5, num=120)],
        bins=[{"patient_id": "w-1", "vital": "heart_rate", "minute": 5, "value": 120.0}],
        events=[{"minute": 5, "kind": "call", "patient_id": "w-1", "staff_id": None, "text": "t"}],
        **over,
    )


def count(api: Api, table: str) -> int:
    return int(api.sql(f"SELECT count(*) AS n FROM {table}")[0]["n"])  # noqa: S608 (a fixed name)


# --- The run and its units ---------------------------------------------------------------------


def test_a_body_with_no_units_is_one_ranked_unit_w4(api: Api) -> None:
    start(api)
    assert api.sql("SELECT id, name, floor, kind, position FROM run_unit WHERE run_id = (SELECT max(id) FROM run)") == [
        {"id": "W4", "name": "Ward W4", "floor": None, "kind": "ranked", "position": 0}
    ]
    assert api.sql("SELECT unit, person FROM patient WHERE id = 's-1'") == [{"unit": "W4", "person": None}]
    [unit] = get(api, "rn-delacruz", "/wards").json()
    assert unit == {"id": "W4", "name": "Ward W4", "floor": None, "kind": "ranked", "patients": 1, "score_note": None}


def test_wards_lists_the_units_in_order_with_the_callers_own_counts(api: Api) -> None:
    hospital(api)
    listed = get(api, "md-santos", "/wards")
    assert [(u["id"], u["name"], u["floor"], u["kind"], u["patients"]) for u in listed.json()] == [
        ("W4", "Ward W4", 4, "ranked", 1), ("ER", "Emergency Room", 0, "shown", 0),
        ("ICU", "Intensive Care", 2, "unranked", 0), ("MED3A", "Medicine 3A", 3, "ranked", 0),
    ]  # fmt: skip
    add_admin(api)
    assert [u["patients"] for u in get(api, ADMIN, "/wards").json()] == [1, 1, 1, 1]
    assert [u["patients"] for u in get(api, "cn-er", "/wards").json()] == [0, 1, 0, 0]
    assert api.client.get("/wards").status_code == 401
    assert [r["resource"] for r in api.sql("SELECT resource FROM audit_log WHERE action = 'list_wards'")] == ["-"] * 3


def test_the_home_unit_is_the_first_and_a_patient_with_no_unit_lives_there(api: Api) -> None:
    body = {**new_run(patients=[stay("w-1", "ER", "cn-er", None, "E1"), sim_patient("w-2")]), "units": UNITS[1:]}
    run_id = api.post("/sim/runs", body).json()["run_id"]
    assert api.sql("SELECT ward FROM run WHERE id = %s", run_id) == [{"ward": "ER"}]
    assert api.sql("SELECT id, unit FROM patient WHERE id IN ('w-1', 'w-2') ORDER BY id") == [
        {"id": "w-1", "unit": "ER"}, {"id": "w-2", "unit": "ER"}
    ]  # fmt: skip


@pytest.mark.parametrize(
    "change",
    [
        {"units": []},
        {"units": [{**UNITS[0], "id": f"U{i}"} for i in range(13)]},
        {"units": [UNITS[0], UNITS[0]]},
        {"units": [{**UNITS[0], "id": "-bad"}]},
        {"units": [{**UNITS[0], "id": "W" * 21}]},
        {"units": [{**UNITS[0], "name": ""}]},
        {"units": [{**UNITS[0], "floor": 201}]},
        {"units": [{**UNITS[0], "floor": -6}]},
        {"units": [{**UNITS[0], "kind": "morgue"}]},
        {"units": UNITS, "patients": [stay("w-1", "NOPE", "rn-delacruz", None, "1")]},
        {"patients": [stay("w-1", "ER", "rn-delacruz", None, "1")]},  # no units: only W4
        {"patients": [{**sim_patient(), "person": ""}]},
        {"patients": [{**sim_patient(), "person": "x" * 65}]},
        {"patients": [{**sim_patient(f"s-{i}")} for i in range(401)]},
    ],
)
def test_a_malformed_run_is_a_422_and_writes_nothing(api: Api, change: dict[str, Any]) -> None:
    before = count(api, "run"), count(api, "patient"), count(api, "run_unit")
    assert api.post("/sim/runs", {**new_run(), **change}).status_code == 422
    assert (count(api, "run"), count(api, "patient"), count(api, "run_unit")) == before


def test_four_hundred_patients_are_accepted(api: Api) -> None:
    patients = [{**sim_patient(f"s-{i}"), "observations": [], "bins": []} for i in range(400)]
    assert api.post("/sim/runs", new_run(patients=patients)).status_code == 201


def test_a_charge_nurse_is_assigned_only_the_patients_of_her_own_unit(api: Api) -> None:
    hospital(api)
    rows = api.sql("SELECT patient_id, clinician_id FROM care_assignment WHERE patient_id ~ '^[a-z]-[0-9]$' ORDER BY 1, 2")
    care = {p: [r["clinician_id"] for r in rows if r["patient_id"] == p] for p in ("w-1", *OTHERS.values())}
    assert care == {
        "w-1": ["md-santos", "rn-delacruz", "rn-manalo"], "e-1": ["cn-er"],
        "i-1": ["cn-icu"], "m-1": ["cn-med3a"],
    }  # fmt: skip


# --- kind ---------------------------------------------------------------------------------------


def test_a_ranked_unit_ranks_its_own_patients(api: Api) -> None:
    hospital(api)
    body = get(api, "md-santos", "/wards/W4/rows").json()
    assert (body["ward"], body["name"], body["floor"], body["kind"]) == ("W4", "Ward W4", 4, "ranked")
    [row] = body["ranked"]
    assert (row["id"], row["rank"], row["unit"], row["floor"], row["person"]) == ("w-1", 1, "W4", 4, None)
    assert body["abstained"] == []
    assert get(api, "cn-med3a", "/wards/MED3A/rows").json()["ranked"][0]["rank"] == 1  # a rank is within one unit


def test_a_shown_unit_lists_every_patient_as_not_ranked_with_only_the_news2_band(api: Api) -> None:
    hospital(api)
    body = get(api, "cn-er", "/wards/ER/rows").json()
    assert (body["ward"], body["name"], body["floor"], body["kind"]) == ("ER", "Emergency Room", 0, "shown")
    assert body["ranked"] == []
    [row] = body["abstained"]
    assert (row["id"], row["news2_risk"], row["unit"], row["floor"]) == ("e-1", "high", "ER", 0)
    assert row["reason"] == "Emergency Room is shown, not ranked: NEWS2 is a ward score."
    assert not {"rank", "score", "models"} & row.keys()


def test_a_shown_patient_with_a_missing_input_has_no_band(api: Api) -> None:
    gone = {**PATIENTS[1], "observations": [o for o in PATIENTS[1]["observations"] if o["code"] != "spo2"]}
    hospital(api, [PATIENTS[0], gone])
    [row] = get(api, "cn-er", "/wards/ER/rows").json()["abstained"]
    assert row["news2_risk"] is None


def test_an_unranked_unit_never_gives_a_band_a_score_or_a_rank_in_any_answer(api: Api) -> None:
    hospital(api)
    body = get(api, "cn-icu", "/wards/ICU/rows").json()
    assert body["ranked"] == []
    [row] = body["abstained"]
    assert (row["id"], row["news2_risk"]) == ("i-1", None)
    assert row["reason"] == "Intensive Care is not ranked: NEWS2 is a ward score."
    for who, pid, band in (("cn-icu", "i-1", None), ("cn-er", "e-1", "high")):
        page = get(api, who, f"/patients/{pid}").json()  # the NOT RANKED page: the row and the stored history
        assert not {"score", "rank", "tier_breakdown", "models", "escalation"} & page.keys()
        assert (page["id"], page["news2_risk"], "vital_history" in page) == (pid, band, True)
        for path in ("/baseline", "/history"):
            assert get(api, who, f"/patients/{pid}{path}").status_code == 404
        [line] = get(api, who, "/patients").json()
        assert (line["id"], line["ranked"]) == (pid, False)
        noted = send(api, who, f"/patients/{pid}/vitals", {"heart_rate": 80}).json()
        assert (noted["ranked"], noted["rank"], noted["score"]) == (False, None, None)
        assert "NEWS2 is a ward score" in noted["not_ranked_reason"]


def test_a_question_about_a_patient_of_an_unranked_unit_is_asked_in_that_unit(api: Api) -> None:
    hospital(api)
    assert send(api, "cn-icu", "/patients/i-1/questions", {"question": "What changed?"}).status_code == 201
    assert api.sql("SELECT ward, patient_id FROM lab_run WHERE kind = 'patient_question'") == [{"ward": "ICU", "patient_id": "i-1"}]


def test_research_is_empty_for_a_unit_that_is_not_ranked(api: Api) -> None:
    hospital(api, [*PATIENTS, stay("e-2", "ER", "cn-er", None, "E2")])
    for unit, nurse in (("ER", "cn-er"), ("ICU", "cn-icu")):
        body = get(api, nurse, f"/wards/{unit}/research").json()
        assert (body["ward"], body["ranked"], body["abstained"]) == (unit, [], [])
    assert len(get(api, "cn-med3a", "/wards/MED3A/research").json()["ranked"]) == 1


def test_the_open_view_of_a_unit_that_is_not_ranked_shows_every_bed_as_not_ranked(api: Api) -> None:
    hospital(api)
    api.client = TestClient(create_app(replace(api.settings, open_ward_view=True), clock=api.wall))
    beds = {u: api.client.get(f"/wards/{u}/open-view").json() for u in ("W4", "ER", "ICU")}
    assert beds["W4"]["beds"] == [{"bed": "4A", "state": "ranked", "rank": 1, "risk": "high"}]
    assert beds["ER"]["beds"] == [{"bed": "E1", "state": "not_ranked", "rank": None, "risk": None}]
    assert beds["ICU"]["beds"] == [{"bed": "I1", "state": "not_ranked", "rank": None, "risk": None}]
    assert beds["ICU"]["ward"] == "ICU"
    # a move out is "out" whatever the ending: where a patient went is not shown
    moves = [{"bed": "E1", "kind": kind, "seq": i} for i, kind in enumerate(("transferred", "died", "in"), 1)]
    run_id = api.sql("SELECT max(id) AS id FROM run")[0]["id"]
    assert api.post(f"/sim/runs/{run_id}/view", {"unit": "ER", "shared": shared(moves=moves), "patients": []}).status_code == 204
    assert [m["kind"] for m in api.client.get("/wards/ER/open-view").json()["sim"]["moves"]] == ["out", "out", "in"]
    assert api.client.get("/wards/NOPE/open-view").status_code == 404
    assert api.sql("SELECT resource, outcome FROM audit_log WHERE action = 'view_open_ward' AND outcome = 'not_found'") == [
        {"resource": "-", "outcome": "not_found"}
    ]


def test_the_overview_of_a_unit_is_the_admins_and_names_that_unit_only(api: Api) -> None:
    hospital(api)
    add_admin(api)
    body = get(api, ADMIN, "/wards/ER/overview").json()
    assert (body["ward"], [(p["unit"], p["floor"], p["state"], p["score"]) for p in body["patients"]]) == (
        "ER", [("ER", 0, "not_ranked", None)]
    )
    assert [c["id"] for c in body["clinicians"]] == ["cn-er", "md-sarmiento", "rn-guevarra", "rn-pangilinan"]
    assert get(api, "md-santos", "/wards/NOPE/overview").status_code == 403  # the admin check comes first
    assert get(api, ADMIN, "/wards/NOPE/overview").status_code == 404
    assert api.sql("SELECT clinician_id, outcome FROM audit_log WHERE action = 'view_overview' AND resource = 'NOPE' ORDER BY seq") == [
        {"clinician_id": "md-santos", "outcome": "denied"}, {"clinician_id": ADMIN, "outcome": "not_found"}
    ]  # fmt: skip


# --- I7: a unit is a filter on top of the assignment ----------------------------------------------


def test_a_clinician_of_one_unit_gets_nothing_of_another_unit_through_any_route(api: Api) -> None:
    run_id = hospital(api)
    for unit, pid in OTHERS.items():
        view = {"unit": unit, "shared": {"clockS": 1}, "patients": [{"id": pid, "view": {"name": "VIEWNAME"}}]}
        assert api.post(f"/sim/runs/{run_id}/view", view).status_code == 204
    for unit, pid in OTHERS.items():
        rows = get(api, "md-santos", f"/wards/{unit}/rows").json()
        assert (rows["ranked"], rows["abstained"]) == ([], [])
        assert get(api, "md-santos", f"/wards/{unit}/stays").json() == []
        # the answers that hold the whole unit are for the unit's own clinicians
        for path in ("live", "research", "lab/runs", "lab/questions"):
            assert get(api, "md-santos", f"/wards/{unit}/{path}").status_code == 403, (unit, path)
        asked = VALID_BODIES[("POST", "/wards/{ward}/lab/runs")]
        assert send(api, "md-santos", f"/wards/{unit}/lab/runs", asked).status_code == 403
        assert send(api, "md-santos", f"/wards/{unit}/lab/questions", {"question": "What if?"}).status_code == 403
        assert send(api, "md-santos", f"/wards/{unit}/sim/commands", {"kind": "set_speed", "speed": 10}).status_code == 403
        for path in ("", "/baseline", "/history", "/notes", "/questions", "/questions/1"):
            assert get(api, "md-santos", f"/patients/{pid}{path}").status_code == 404, (pid, path)
        for path, body in (
            ("/notes", VALID_BODIES[("POST", "/patients/{patient_id}/notes")]),
            ("/labs", {"potassium": 4.0}),
            ("/vitals", {"heart_rate": 80}),
            ("/acknowledgement", {"score": 3}),
            ("/questions", {"question": "What changed?"}),
        ):
            assert send(api, "md-santos", f"/patients/{pid}{path}", body).status_code == 404, (pid, path)
        command = {"kind": "inject_deterioration", "patient_id": pid}
        assert send(api, "md-santos", f"/wards/{unit}/sim/commands", command).status_code == 403
        assert send(api, "md-santos", "/wards/W4/sim/commands", command).status_code == 404  # not his patient
    assert [p["id"] for p in get(api, "md-santos", "/patients").json()] == ["w-1"]
    own = {"kind": "inject_deterioration", "patient_id": "w-1"}
    assert send(api, "md-santos", "/wards/MED3A/sim/commands", own).status_code == 403  # his patient, not his unit
    assert send(api, "cn-er", "/wards/ER/sim/commands", own).status_code == 404  # her unit, not her patient
    assert api.sql("SELECT count(*) AS n FROM sim_command") == [{"n": 0}]
    assert api.sql("SELECT count(*) AS n FROM lab_run") == [{"n": 0}]
    denied = api.sql("SELECT DISTINCT action FROM audit_log WHERE clinician_id = 'md-santos' AND outcome = 'denied' AND resource = 'ER'")
    assert {r["action"] for r in denied} == {
        "view_live", "view_ward_research", "view_lab_runs", "view_lab_questions", "lab_request", "lab_question", "sim_command"
    }
    # and the other way: the charge nurse of the ER sees nothing of W4
    assert get(api, "cn-er", "/wards/W4/rows").json()["ranked"] == []
    assert get(api, "cn-er", "/patients/w-1").status_code == 404
    live = get(api, "cn-er", "/wards/ER/live").json()
    assert [p["id"] for p in live["patients"]] == ["e-1"]
    assert get(api, "cn-er", "/wards/W4/live").status_code == 403
    assert get(api, "cn-er", "/wards/W4/research").status_code == 403
    # a clinician with a patient in a unit that is not their own belongs there while the patient does
    assert get(api, "rn-delacruz", "/wards/W4/live").status_code == 200
    write(api, "UPDATE clinician SET unit = NULL WHERE id = 'rn-delacruz'")  # no unit of her own: her patient's
    assert get(api, "rn-delacruz", "/wards/W4/live").status_code == 200
    assert get(api, "rn-delacruz", "/wards/ER/live").status_code == 403


def test_every_view_of_a_unit_writes_its_audit_row_and_an_unknown_unit_is_a_miss(api: Api) -> None:
    hospital(api)
    for path in ("rows", "stays", "live", "research", "lab/runs", "lab/questions"):
        assert get(api, "cn-er", f"/wards/ER/{path}").status_code == 200
        assert get(api, "cn-er", f"/wards/NOPE/{path}").status_code == 404
    seen = {(r["action"], r["resource"], r["outcome"]) for r in api.sql("SELECT action, resource, outcome FROM audit_log WHERE clinician_id = 'cn-er'")}
    for action in ("view_ward", "view_stays", "view_live", "view_ward_research", "view_lab_runs", "view_lab_questions"):
        assert (action, "ER", "ok") in seen and (action, "NOPE", "not_found") in seen, action
    command = {"kind": "set_speed", "speed": 10}
    assert send(api, "md-santos", "/wards/NOPE/sim/commands", command).status_code == 404
    assert ("sim_command", "NOPE", "not_found") in {(r["action"], r["resource"], r["outcome"]) for r in api.sql("SELECT action, resource, outcome FROM audit_log")}


def test_the_lab_is_per_unit_and_for_the_units_own_clinicians(api: Api) -> None:
    hospital(api)
    made = send(api, "cn-er", "/wards/ER/lab/runs", VALID_BODIES[("POST", "/wards/{ward}/lab/runs")])
    assert made.status_code == 201
    assert api.sql("SELECT ward FROM lab_run") == [{"ward": "ER"}]
    assert [r["id"] for r in get(api, "cn-er", "/wards/ER/lab/runs").json()] == [made.json()["id"]]
    assert get(api, "md-santos", "/wards/W4/lab/runs").json() == []  # another unit's list holds none of it
    assert get(api, "md-santos", f"/wards/W4/lab/runs/{made.json()['id']}").status_code == 404
    assert get(api, "cn-er", f"/wards/ER/lab/runs/{made.json()['id']}").status_code == 200
    question = send(api, "cn-icu", "/wards/ICU/lab/questions", {"question": "What if?"})
    assert question.status_code == 201
    assert get(api, "cn-er", "/wards/ER/lab/questions").json() == []
    assert [q["id"] for q in get(api, "cn-icu", "/wards/ICU/lab/questions").json()] == [question.json()["id"]]


def test_a_command_carries_the_unit_it_was_sent_from(api: Api) -> None:
    run_id = hospital(api)
    assert send(api, "md-santos", "/wards/W4/sim/commands", {"kind": "set_speed", "speed": 10}).status_code == 204
    own = {"kind": "inject_deterioration", "patient_id": "w-1"}
    assert send(api, "md-santos", "/wards/W4/sim/commands", own).status_code == 204
    sent = api.client.get(f"/sim/runs/{run_id}/commands", headers={"X-Ingest-Key": "test-ingest-key"}).json()
    assert [(c["kind"], c["unit"]) for c in sent["commands"]] == [("set_speed", "W4"), ("inject_deterioration", "W4")]


# --- Admitting in the app --------------------------------------------------------------------------


def test_an_admission_into_a_unit_that_is_not_the_callers_is_refused_and_opens_nothing(api: Api) -> None:
    """The admitter is assigned the new patient: without the refusal one admission would make any
    unit hers, and open its research view, live view, lab and commands."""
    hospital(api)
    before = count(api, "patient")
    for unit in ("MED3A", "W4", "ICU"):
        refused = send(api, "cn-er", "/patients", {**admit_input(), "bed": "Z1", "unit": unit})
        assert refused.status_code == 403, unit
    assert count(api, "patient") == before and count(api, "clinician_action") == 0
    denied = api.sql("SELECT resource FROM audit_log WHERE clinician_id = 'cn-er' AND action = 'admit_patient' AND outcome = 'denied' ORDER BY seq")
    assert [r["resource"] for r in denied] == ["MED3A", "W4", "ICU"]
    for path in ("live", "research", "lab/runs", "lab/questions"):
        assert get(api, "cn-er", f"/wards/MED3A/{path}").status_code == 403, path
    assert send(api, "cn-er", "/wards/MED3A/sim/commands", {"kind": "set_speed", "speed": 10}).status_code == 403


def test_an_admission_goes_to_the_callers_unit_or_to_a_named_unit_that_is_already_theirs(api: Api) -> None:
    run_id = hospital(api)
    form = admit_input()  # bed 4A
    assert send(api, "md-santos", "/patients", form).status_code == 409  # W4 has a patient in 4A
    assert send(api, "cn-er", "/patients", form).json()["unit"] == "ER"  # per unit: the same bed in the ER is free
    own = send(api, "cn-er", "/patients", {**form, "bed": "E7", "unit": "ER"})  # her own unit, named
    assert (own.status_code, own.json()["unit"], own.json()["floor"]) == (201, "ER", 0)
    # a unit that is not hers by her row, and already hers by a patient she has there now
    write(api, "INSERT INTO care_assignment (run_id, patient_id, clinician_id) VALUES (%s, 'm-1', 'cn-er')", run_id)
    named = send(api, "cn-er", "/patients", {**form, "unit": "MED3A"})
    assert (named.status_code, named.json()["unit"], named.json()["floor"]) == (201, "MED3A", 3)
    refused = send(api, "cn-er", "/patients", {**form, "bed": "Z9", "unit": "NOPE"})
    assert refused.status_code == 409
    assert api.sql("SELECT count(*) AS n FROM patient WHERE bed = 'Z9'") == [{"n": 0}]
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'admit_patient' ORDER BY seq DESC LIMIT 1") == [{"outcome": "rejected"}]
    admitted = api.client.get(f"/sim/runs/{run_id}/actions", headers={"X-Ingest-Key": "test-ingest-key"}).json()["actions"]
    assert [(a["kind"], a["unit"]) for a in admitted] == [("admit_patient", "ER"), ("admit_patient", "ER"), ("admit_patient", "MED3A")]
    assert send(api, "cn-er", "/patients", {**form, "bed": "Z8", "unit": "bad id"}).status_code == 422


def test_an_admission_in_a_shown_unit_is_not_ranked_with_the_units_reason(api: Api) -> None:
    hospital(api)
    row = send(api, "cn-er", "/patients", admit_input()).json()
    assert row["reason"] == "Emergency Room is shown, not ranked: NEWS2 is a ward score."
    assert row["news2_risk"] is None  # no patch, no readings: nothing to band


# --- A stay ends: died, transferred -----------------------------------------------------------------


def moved_hospital(api: Api) -> int:
    run_id = hospital(
        api, [*PATIENTS, stay("w-2", "W4", "rn-delacruz", "md-santos", "4B", person="h-2"), stay("w-3", "W4", "rn-reyes", None, "4C")]
    )
    api.post(f"/sim/runs/{run_id}/view", {"shared": {"clockS": 1}, "patients": [{"id": p, "view": {}} for p in ("w-1", "w-2", "w-3")]})
    move = {
        "admissions": [{**stay("i-2", "ICU", "cn-icu", None, "I2", person="h-2"), "minute": 30}],
        "departures": [
            {"patient_id": "w-2", "minute": 30, "outcome": "transferred", "to_unit": "ICU"},
            {"patient_id": "w-3", "minute": 31, "outcome": "died"},
        ],
    }  # fmt: skip
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=31, patients=[], observations=[], bins=[], events=[], **move)).status_code == 204
    return run_id


def test_a_transfer_ends_one_stay_and_starts_another_for_the_same_person(api: Api) -> None:
    moved_hospital(api)
    old = {s["id"]: s for s in get(api, "md-santos", "/wards/W4/stays").json()}
    assert (old["w-2"]["outcome"], old["w-2"]["to_unit"], old["w-2"]["person"], old["w-2"]["unit"], old["w-2"]["floor"]) == (
        "transferred", "ICU", "h-2", "W4", 4
    )
    new = {s["id"]: s for s in get(api, "cn-icu", "/wards/ICU/stays").json()}
    assert (new["i-2"]["person"], new["i-2"]["outcome"], new["i-2"]["to_unit"]) == ("h-2", None, None)
    assert "w-2" not in new  # the stays of a unit are that unit's only


def test_a_dead_or_transferred_patient_is_on_no_live_list(api: Api) -> None:
    run_id = moved_hospital(api)
    stays = {s["id"]: s for s in get(api, "rn-manalo", "/wards/W4/stays").json()}
    assert (stays["w-3"]["outcome"], stays["w-3"]["to_unit"], stays["w-3"]["left_at"]) == ("died", None, "2026-01-01T00:31:00Z")
    api.post(f"/sim/runs/{run_id}/view", {"shared": {"clockS": 1}, "patients": [{"id": p, "view": {}} for p in ("w-1", "w-2", "w-3")]})
    add_admin(api)
    api.client = TestClient(create_app(replace(api.settings, open_ward_view=True), clock=api.wall))
    for who in ("md-santos", "rn-delacruz", "rn-reyes", "rn-manalo"):
        rows = get(api, who, "/wards/W4/rows").json()
        assert {r["id"] for r in (*rows["ranked"], *rows["abstained"])} <= {"w-1"}
        assert {p["id"] for p in get(api, who, "/patients").json()} <= {"w-1"}
        assert {p["id"] for p in get(api, who, "/wards/W4/live").json()["patients"]} <= {"w-1"}
    assert [p["id"] for p in get(api, "rn-reyes", "/patients").json()] == []  # w-3 was rn-reyes's only patient
    assert [r["id"] for r in get(api, "md-santos", "/wards/W4/research").json()["ranked"] if r["id"]] == ["w-1"]
    assert [p["bed"] for p in get(api, ADMIN, "/wards/W4/overview").json()["patients"]] == ["4A"]
    assert [b["bed"] for b in api.client.get("/wards/W4/open-view").json()["beds"]] == ["4A"]
    assert get(api, ADMIN, "/wards").json()[0]["patients"] == 1
    assert get(api, "md-santos", "/patients/w-3").status_code == 404


@pytest.mark.parametrize(
    "departure",
    [
        {"outcome": "transferred"},
        {"outcome": "transferred", "to_unit": "NOPE"},
        {"outcome": "transferred", "to_unit": "W4"},  # its own unit
        {"outcome": "transferred", "to_unit": "bad id"},
        {"outcome": "discharged", "to_unit": "ICU"},
        {"outcome": "died", "to_unit": "ICU"},
        {"outcome": "higher_care", "to_unit": "ICU"},
        {"outcome": "buried"},
    ],
)
def test_a_departure_with_a_bad_to_unit_is_a_422_and_the_whole_batch_writes_nothing(api: Api, departure: dict[str, Any]) -> None:
    run_id = hospital(api)
    before = count(api, "observation"), count(api, "sim_event")
    assert api.post(f"/sim/runs/{run_id}/batch", reading(departures=[{"patient_id": "w-1", "minute": 5, **departure}])).status_code == 422
    # the batch also carries a good reading and an event: they are not kept
    assert (count(api, "observation"), count(api, "sim_event")) == before
    assert api.sql("SELECT left_at, outcome, to_unit FROM patient WHERE id = 'w-1'") == [{"left_at": None, "outcome": None, "to_unit": None}]


def test_the_batch_of_the_refusals_above_is_good_without_the_bad_departure(api: Api) -> None:
    run_id = hospital(api)
    assert api.post(f"/sim/runs/{run_id}/batch", reading()).status_code == 204
    assert api.post(f"/sim/runs/{run_id}/batch", reading(departures=[{"patient_id": "w-1", "minute": 5, "outcome": "transferred", "to_unit": "ER"}])).status_code == 204


def test_the_old_outcomes_still_work_and_a_transfer_may_send_only_its_first_half(api: Api) -> None:
    run_id = hospital(api)
    old = [
        {"patient_id": "w-1", "minute": 5, "outcome": "higher_care"},
        {"patient_id": "e-1", "minute": 5, "outcome": "discharged"},
        {"patient_id": "i-1", "minute": 5, "outcome": "transferred", "to_unit": "W4"},
    ]  # fmt: skip
    assert depart(api, run_id, *old).status_code == 204
    assert api.sql("SELECT id, outcome, to_unit FROM patient ORDER BY id")[:3] == [
        {"id": "e-1", "outcome": "discharged", "to_unit": None},
        {"id": "i-1", "outcome": "transferred", "to_unit": "W4"},
        {"id": "m-1", "outcome": None, "to_unit": None},
    ]


def test_an_admission_to_an_unknown_unit_is_a_422_and_writes_nothing(api: Api) -> None:
    run_id = hospital(api)
    before = count(api, "patient")
    body = batch(minute=5, patients=[], observations=[], bins=[], events=[], admissions=[{**stay("n-1", "NOPE", "cn-er", None, "N1"), "minute": 5}])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 422
    assert count(api, "patient") == before


def test_a_batch_admission_with_no_unit_goes_to_the_home_unit(api: Api) -> None:
    run_id = hospital(api)
    body = batch(minute=5, patients=[], observations=[], bins=[], events=[], admissions=[{**sim_patient("n-1"), "minute": 5}])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert api.sql("SELECT unit FROM patient WHERE id = 'n-1'") == [{"unit": "W4"}]


# --- The live view is one per unit ---------------------------------------------------------------------


def test_a_unit_has_its_own_live_view_and_a_post_replaces_that_unit_only(api: Api) -> None:
    run_id = hospital(api)
    post = lambda unit, n, patients=(): api.post(  # noqa: E731
        f"/sim/runs/{run_id}/view", {**({} if unit is None else {"unit": unit}), "shared": {"n": n}, "patients": list(patients)}
    )
    assert [post(None, 1).status_code, post("ER", 2).status_code, post("W4", 3).status_code] == [204] * 3
    assert get(api, "md-santos", "/wards/W4/live").json()["shared"] == {"n": 3}  # no unit means the home unit
    assert get(api, "cn-er", "/wards/ER/live").json()["shared"] == {"n": 2}
    assert get(api, "cn-icu", "/wards/ICU/live").json()["shared"] is None
    assert api.sql("SELECT live_view IS NULL AS none FROM run WHERE id = %s", run_id) == [{"none": True}]


def test_a_view_of_an_unknown_unit_is_a_422_and_stores_nothing(api: Api) -> None:
    run_id = hospital(api)
    api.post(f"/sim/runs/{run_id}/view", {"unit": "ER", "shared": {"n": 1}, "patients": []})
    body = {"unit": "NOPE", "shared": {"n": 9}, "patients": [{"id": "w-1", "view": {}}]}
    assert api.post(f"/sim/runs/{run_id}/view", body).status_code == 422
    assert api.sql("SELECT id, live_view FROM run_unit WHERE live_view IS NOT NULL") == [{"id": "ER", "live_view": {"shared": {"n": 1}, "patients": []}}]


def test_a_view_entry_for_a_patient_of_another_unit_is_never_served(api: Api) -> None:
    """Stored as sent (today's runner may publish before the batch that admits), served to nobody:
    the read keeps only the caller's own patients of that unit."""
    run_id = hospital(api)
    entries = [{"id": pid, "view": {"name": "VIEWNAME"}} for pid in ("e-1", "w-1", "zz-9")]
    assert api.post(f"/sim/runs/{run_id}/view", {"unit": "ER", "shared": {"n": 1}, "patients": entries}).status_code == 204
    assert [p["id"] for p in get(api, "cn-er", "/wards/ER/live").json()["patients"]] == ["e-1"]
    write(api, "INSERT INTO care_assignment (run_id, patient_id, clinician_id) VALUES (%s, 'e-1', 'rn-delacruz')", run_id)
    assert [p["id"] for p in get(api, "rn-delacruz", "/wards/ER/live").json()["patients"]] == ["e-1"]  # w-1 is hers, not the ER's


def test_an_acknowledgement_is_refused_where_no_score_exists(api: Api) -> None:
    hospital(api)
    for nurse, pid in (("cn-er", "e-1"), ("cn-icu", "i-1")):
        assert send(api, nurse, f"/patients/{pid}/acknowledgement", {"score": 3}).status_code == 404
    assert send(api, "rn-delacruz", "/patients/w-1/acknowledgement", {"score": 3}).status_code == 204
    assert api.sql("SELECT patient_id FROM clinician_action WHERE kind = 'acknowledge'") == [{"patient_id": "w-1"}]
    missed = api.sql("SELECT clinician_id FROM audit_log WHERE action = 'acknowledge' AND outcome = 'not_found' ORDER BY 1")
    assert [r["clinician_id"] for r in missed] == ["cn-er", "cn-icu"]
