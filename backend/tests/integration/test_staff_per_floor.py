"""Staff on every floor (migration 0019): one charge nurse, two staff nurses and one physician per unit, all
synthetic, with NO passphrase, and a `display_name` for every clinician. The sign-in answer and the admin
overview carry display_name, unit and floor as labels. A scope is still the patients assigned, never the unit."""

import re
from pathlib import Path
from typing import Any

import pytest
from test_overview import ADMIN, add_admin, write
from test_store import Api, api  # noqa: F401 (api: fixture)
from test_units import PATIENTS, UNITS, get, hospital, send, stay

from biosentry.db.passphrase import hash_passphrase
from biosentry.db.seed import DEV_PASSPHRASE

NEW = {
    "ER": ("rn-pangilinan", "rn-guevarra", "md-sarmiento"),
    "ICU": ("rn-abad", "rn-lacson", "md-ocampo"),
    "MED3A": ("rn-pascual", "rn-domingo", "md-hizon"),
    "MED3B": ("rn-salazar", "rn-tolentino", "md-evangelista"),
    "SURG5A": ("rn-ilagan", "rn-cabrera", "md-estrada"),
}
CHARGE = {"ER": "cn-er", "ICU": "cn-icu", "MED3A": "cn-med3a", "MED3B": "cn-med3b", "SURG5A": "cn-surg5a"}
ALL_NEW = [i for ids in NEW.values() for i in ids]
W4 = {"rn-manalo": "Manalo", "rn-delacruz": "de la Cruz", "rn-reyes": "Reyes", "md-santos": "Santos", "md-aquino": "Aquino"}
README = Path(__file__).resolve().parents[3] / "deploy" / "README.md"


def people(api: Api) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in api.sql("SELECT id, role, unit, display_name, passphrase_hash FROM clinician WHERE role <> 'admin'")}


def sign_in(api: Api, who: str, passphrase: str = DEV_PASSPHRASE) -> Any:
    return api.client.post("/session", json={"clinician_id": who, "passphrase": passphrase})


def give_passphrase(api: Api, *ids: str) -> None:
    for i in ids:
        write(api, "UPDATE clinician SET passphrase_hash = %s WHERE id = %s", hash_passphrase(DEV_PASSPHRASE), i)


def test_every_floor_has_one_charge_nurse_two_staff_nurses_and_one_physician_and_w4_its_five(api: Api) -> None:
    found = people(api)
    for unit, ids in NEW.items():
        staff = {i: p for i, p in found.items() if p["unit"] == unit}
        assert sorted(p["role"] for p in staff.values()) == ["charge_rn", "md", "rn", "rn"], unit
        assert set(staff) == {CHARGE[unit], *ids}
    w4 = {i: p["role"] for i, p in found.items() if p["unit"] == "W4"}
    assert set(w4) == set(W4)  # the five, unchanged
    assert sorted(w4.values()) == ["charge_rn", "md", "md", "rn", "rn"]
    assert len(found) == 25


def test_every_clinician_has_a_display_name_and_the_names_are_all_different(api: Api) -> None:
    names = {i: p["display_name"] for i, p in people(api).items()}
    assert all(names.values()) and len(set(names.values())) == len(names)
    assert {i: names[i] for i in W4} == W4
    assert all(names[c] for c in CHARGE.values())  # the five charge nurses are named too


def test_the_fifteen_new_people_and_the_charge_nurses_have_no_passphrase(api: Api) -> None:
    found = people(api)
    assert {i for i in (*ALL_NEW, *CHARGE.values()) if found[i]["passphrase_hash"] is not None} == set()
    for who in (*ALL_NEW, *CHARGE.values()):
        for given in (DEV_PASSPHRASE, "x" * 40, "a different long passphrase"):
            assert sign_in(api, who, given).status_code == 401, (who, given)
    assert api.sql("SELECT 1 FROM session WHERE clinician_id = ANY(%s)", [*ALL_NEW, *CHARGE.values()]) == []


def test_the_sign_in_answer_carries_the_name_role_unit_and_floor_as_labels(api: Api) -> None:
    hospital(api)
    add_admin(api)
    give_passphrase(api, "rn-pangilinan", "md-sarmiento", "rn-reyes")
    seen = {w: sign_in(api, w).json() for w in ("rn-pangilinan", "md-sarmiento", "cn-er", "rn-reyes", ADMIN)}
    assert seen["rn-pangilinan"] | {"token": ""} == {"token": "", "role": "rn", "display_name": "Pangilinan", "unit": "ER", "floor": 0}
    assert (seen["md-sarmiento"]["role"], seen["md-sarmiento"]["display_name"]) == ("md", "Sarmiento")
    assert (seen["cn-er"]["role"], seen["cn-er"]["display_name"], seen["cn-er"]["unit"]) == ("charge_rn", "Dizon", "ER")
    assert (seen["rn-reyes"]["display_name"], seen["rn-reyes"]["unit"], seen["rn-reyes"]["floor"]) == ("Reyes", "W4", 4)
    assert (seen[ADMIN]["role"], seen[ADMIN]["display_name"], seen[ADMIN]["unit"], seen[ADMIN]["floor"]) == ("admin", None, None, None)


def ids_of(response: Any) -> set[str]:
    body = response.json()
    return {r["id"] for r in (*body["ranked"], *body["abstained"])}


def test_each_role_still_sees_only_the_patients_assigned_to_them_whatever_their_floor(api: Api) -> None:
    ward = [
        *PATIENTS,
        stay("e-2", "ER", "rn-pangilinan", "md-sarmiento", "E2"),
        stay("e-3", "ER", "rn-guevarra", None, "E3"),
        stay("m-2", "MED3A", "rn-pascual", "md-hizon", "M2"),
    ]
    hospital(api, ward)
    give_passphrase(api, *ALL_NEW)
    seen = {
        ("rn-pangilinan", "ER"): {"e-2"},
        ("md-sarmiento", "ER"): {"e-2"},
        ("rn-guevarra", "ER"): {"e-3"},
        ("cn-er", "ER"): {"e-1", "e-2", "e-3"},  # the API assigns every patient of a unit to its charge nurse
        ("rn-abad", "ICU"): set(),  # a staff nurse with no patient assigned sees none, even on their own floor
        ("rn-pascual", "MED3A"): {"m-2"},
        ("md-hizon", "MED3A"): {"m-2"},
    }
    for (who, unit), expected in seen.items():
        assert ids_of(get(api, who, f"/wards/{unit}/rows")) == expected, (who, unit)
    # another floor, another person's patient: nothing, and the same 404 as an unknown id
    assert ids_of(get(api, "cn-er", "/wards/MED3A/rows")) == set()  # a charge nurse reads their own unit only
    assert ids_of(get(api, "rn-pangilinan", "/wards/MED3A/rows")) == set()
    assert ids_of(get(api, "md-hizon", "/wards/ER/rows")) == set()
    assert get(api, "rn-pangilinan", "/patients/e-3").status_code == 404
    assert get(api, "rn-guevarra", "/patients/e-2").status_code == 404
    assert get(api, "md-sarmiento", "/patients/m-2").status_code == 404
    assert get(api, "rn-pangilinan", "/patients/e-2").status_code == 200
    admin = add_admin(api)
    assert ids_of(api.client.get("/wards/ER/rows", headers=admin)) == {"e-1", "e-2", "e-3"}  # an admin reads the unit, read-only


def test_the_admin_overview_lists_each_person_of_the_unit_with_name_unit_and_floor(api: Api) -> None:
    hospital(api, [*PATIENTS, stay("e-2", "ER", "rn-pangilinan", "md-sarmiento", "E2")])
    admin = add_admin(api)
    body = api.client.get("/wards/ER/overview", headers=admin).json()
    by_id = {c["id"]: c for c in body["clinicians"]}
    assert set(by_id) == {"cn-er", "rn-pangilinan", "rn-guevarra", "md-sarmiento"}
    assert by_id["rn-guevarra"] == {"id": "rn-guevarra", "role": "rn", "patients": 0, "display_name": "Guevarra", "unit": "ER", "floor": 0}
    assert by_id["rn-pangilinan"]["patients"] == 1 and by_id["md-sarmiento"]["display_name"] == "Sarmiento"


def test_the_readme_lists_every_new_id_and_how_to_give_it_a_passphrase() -> None:
    text = README.read_text(encoding="utf-8")
    for who in (*ALL_NEW, *CHARGE.values(), *W4):
        assert f"`{who}`" in text, who
    assert "make db-passphrase id=" in text and "None has a passphrase" in text


def test_a_database_that_already_had_the_five_gets_their_names_and_a_new_one_never_loses_its_own(api: Api) -> None:
    write(api, "UPDATE clinician SET display_name = 'Own Name' WHERE id = 'cn-er'")
    ran = re.sub(r"(?s)^.*?(UPDATE clinician c SET)", r"\1", Path(__file__).resolve().parents[2].joinpath(
        "src/biosentry/db/migrations/0019_staff_per_floor.sql").read_text(encoding="utf-8"), count=1)
    write(api, ran)
    assert people(api)["cn-er"]["display_name"] == "Own Name"  # only a null name is filled


def test_the_answers_that_hold_a_whole_unit_refuse_another_units_new_staff(api: Api) -> None:
    hospital(api)
    give_passphrase(api, *ALL_NEW)  # a test of the API, so these people may sign in
    for who in ("md-sarmiento", "rn-pangilinan", "cn-er"):  # ER people ask for MED3A and ICU
        for unit in ("MED3A", "ICU"):
            for path in ("live", "research", "lab/runs", "lab/questions"):
                assert get(api, who, f"/wards/{unit}/{path}").status_code == 403, (who, unit, path)
            assert send(api, who, f"/wards/{unit}/sim/commands", {"kind": "set_speed", "speed": 10}).status_code == 403
    assert get(api, "md-hizon", "/wards/ER/live").status_code == 403  # a MED3A physician asks for the ER
    assert get(api, "md-hizon", "/wards/ER/research").status_code == 403
    assert get(api, "rn-pascual", "/wards/ER/live").status_code == 403
    assert get(api, "md-sarmiento", "/wards/ER/live").status_code == 200  # their own unit
    assert get(api, "rn-pangilinan", "/wards/ER/research").status_code == 403  # a staff nurse: never the research view
    assert api.sql("SELECT count(*) AS n FROM sim_command") == [{"n": 0}]


def test_only_rn_manalo_and_md_santos_may_set_the_speed_of_skip_or_reset_the_whole_run(api: Api) -> None:
    # Owner decision orch-s2: a command that acts on the whole shared run is for these two. Any other physician
    # or charge nurse (the new ones too) gets 403 and a `denied` audit row, and no command is queued.
    run_id = hospital(api)
    give_passphrase(api, *ALL_NEW)
    set_speed = {"kind": "set_speed", "speed": 10}
    skip = {"kind": "skip", "minutes": 60}
    reset = {"kind": "reset"}
    denied = [
        ("md-sarmiento", "ER"), ("cn-er", "ER"), ("md-ocampo", "ICU"), ("cn-icu", "ICU"), ("md-hizon", "MED3A"),
        ("md-aquino", "W4"), ("md-sarmiento", "W4"), ("rn-pangilinan", "ER"), ("rn-abad", "ICU"), ("rn-delacruz", "W4"),
    ]  # a physician or charge nurse of any unit, a W4 physician who is not md-santos, and the staff nurses
    for who, unit in denied:
        for body in (set_speed, skip, reset):
            assert send(api, who, f"/wards/{unit}/sim/commands", body).status_code == 403, (who, unit, body)
    admin = add_admin(api)
    assert api.client.post("/wards/W4/sim/commands", json=set_speed, headers=admin).status_code == 403
    assert api.sql("SELECT count(*) AS n FROM sim_command") == [{"n": 0}]
    rows = api.sql("SELECT clinician_id, outcome FROM audit_log WHERE action = 'sim_command'")
    assert len(rows) == 3 * len(denied) + 1 and {r["outcome"] for r in rows} == {"denied"}  # every refusal is audited
    for who in ("md-santos", "rn-manalo"):
        assert send(api, who, "/wards/W4/sim/commands", set_speed).status_code == 204, who
        assert send(api, who, "/wards/W4/sim/commands", reset).status_code == 204, who
    assert send(api, "md-santos", "/wards/W4/sim/commands", skip).status_code == 204
    assert api.sql("SELECT count(*) AS n FROM sim_command") == [{"n": 5}]
    assert run_id  # the one run of the hospital


def test_a_new_physician_still_sends_the_patient_commands_of_their_own_units_patients(api: Api) -> None:
    hospital(api, [*PATIENTS, stay("e-2", "ER", "rn-pangilinan", "md-sarmiento", "E2")])
    give_passphrase(api, *ALL_NEW)
    own = {"kind": "inject_deterioration", "patient_id": "e-2"}
    assert send(api, "md-sarmiento", "/wards/ER/sim/commands", own).status_code == 204  # their own patient, unchanged
    assert send(api, "md-ocampo", "/wards/ER/sim/commands", own).status_code == 403  # another unit's physician
    assert send(api, "rn-pangilinan", "/wards/ER/sim/commands", own).status_code == 403  # a staff nurse
