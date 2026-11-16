"""Stored vitals of a patient in a unit that is shown or unranked (ER, ICU), for a signed-in reader who
may see the patient: an admin (every unit, read-only) or the unit's own charge nurse. Never a score or
a rank (I4), and the public open view and bed card keep giving state only."""

from typing import Any

import pytest
from test_open_view import watch  # noqa: F401 (watch: the API with the setting on)
from test_overview import ADMIN, add_admin
from test_store import Api, api  # noqa: F401 (api: fixture)
from test_units import PATIENTS, depart, get, hospital

FIVE = {"heart_rate", "respiratory_rate", "spo2", "temperature", "blood_pressure"}
NO_SCORE = {"score", "rank", "tier_breakdown", "models", "escalation_reasons", "sort_tier", "tier_status"}


def test_the_same_vitals_and_trends_a_ranked_row_has_are_on_a_shown_or_unranked_row(api: Api) -> None:
    hospital(api)
    [own] = get(api, "md-santos", "/wards/W4/rows").json()["ranked"]
    for who, unit, pid in (("cn-er", "ER", "e-1"), ("cn-icu", "ICU", "i-1")):
        [row] = get(api, who, f"/wards/{unit}/rows").json()["abstained"]
        assert row["id"] == pid and not NO_SCORE & row.keys()
        assert row["vitals"].keys() == own["vitals"].keys() and set(own["vitals"]) >= FIVE
        assert row["vital_trends"].keys() == own["vital_trends"].keys()
        assert row["vitals"]["heart_rate"]["data"]["value"] is not None  # a stored value, with its provenance
        assert row["vitals"]["heart_rate"]["provenance"]["observed_at"]


def test_an_admin_reads_every_unit_and_a_charge_nurse_reads_only_their_own(api: Api) -> None:
    hospital(api)
    add_admin(api)
    for unit, pid in (("ER", "e-1"), ("ICU", "i-1")):
        [row] = get(api, ADMIN, f"/wards/{unit}/rows").json()["abstained"]
        assert (row["id"], row["vitals"] is not None, row["vital_trends"] is not None) == (pid, True, True)
        assert get(api, ADMIN, f"/patients/{pid}").status_code == 200
    # another unit's charge nurse and the W4 doctor are given nothing: an empty unit, a 404 for the page
    for who in ("cn-icu", "cn-med3a", "md-santos"):
        assert get(api, who, "/wards/ER/rows").json()["abstained"] == []
        assert get(api, who, "/patients/e-1").status_code == 404
    assert get(api, "cn-er", "/patients/i-1").status_code == 404


@pytest.mark.parametrize(("who", "pid"), [("cn-er", "e-1"), ("cn-icu", "i-1"), (ADMIN, "e-1"), (ADMIN, "i-1")])
def test_the_patient_page_gives_the_vitals_the_stored_history_and_no_score_or_rank(api: Api, who: str, pid: str) -> None:
    hospital(api)
    add_admin(api)
    response = get(api, who, f"/patients/{pid}")
    page = response.json()
    assert response.status_code == 200 and page["id"] == pid
    assert not NO_SCORE & page.keys() and page["reason"]
    assert page["vitals"].keys() >= FIVE and page["vital_trends"] and page["vital_history"]
    assert page["vitals"] == get(api, who, f"/wards/{page['unit']}/rows").json()["abstained"][0]["vitals"]
    for path in ("/baseline", "/history"):  # NEWS2 history and baseline stay ward-only: they are scores
        assert get(api, who, f"/patients/{pid}{path}").status_code == 404


def test_a_ranked_unit_row_that_is_not_ranked_is_unchanged(api: Api) -> None:
    gone = {**PATIENTS[0], "observations": [o for o in PATIENTS[0]["observations"] if o["code"] != "spo2"]}
    hospital(api, [gone, *PATIENTS[1:]])
    [row] = get(api, "md-santos", "/wards/W4/rows").json()["abstained"]
    assert row["vitals"] is None and row["vital_trends"] is None and row["last_seen"]


def test_a_stay_that_ended_is_gone_for_the_charge_nurse_and_the_admin(api: Api) -> None:
    run_id = hospital(api)
    add_admin(api)
    ending = {"patient_id": "e-1", "minute": 30, "outcome": "discharged"}
    assert depart(api, run_id, ending).status_code == 204
    for who in ("cn-er", ADMIN):
        assert get(api, who, "/wards/ER/rows").json()["abstained"] == []
        assert get(api, who, "/patients/e-1").status_code == 404


def test_the_public_views_still_give_state_only_for_er_and_icu(watch: Api) -> None:
    hospital(watch)
    for unit, bed in (("ER", "E1"), ("ICU", "I1")):
        card = watch.client.get(f"/wards/{unit}/open-view/beds/{bed}").json()
        assert (card["state"], card["vitals"], card["score"], card["risk"], card["trend"]) == ("not_ranked", None, None, None, None)
        view = watch.client.get(f"/wards/{unit}/open-view").text
        assert "vital" not in view and "heart_rate" not in view
    assert watch.client.get("/wards/ER/rows").status_code == 401  # no sign-in, no rows


def test_each_read_writes_its_audit_row_for_the_reader_and_none_holds_a_value(api: Api) -> None:
    hospital(api)
    add_admin(api)
    get(api, ADMIN, "/wards/ER/rows")
    get(api, ADMIN, "/patients/e-1")
    get(api, "cn-icu", "/patients/e-1")  # not hers: not_found
    seen = api.sql(
        "SELECT clinician_id, action, resource, outcome FROM audit_log"
        " WHERE action IN ('view_ward', 'view_patient') ORDER BY seq"
    )
    assert seen == [
        {"clinician_id": ADMIN, "action": "view_ward", "resource": "ER", "outcome": "ok"},
        {"clinician_id": ADMIN, "action": "view_patient", "resource": "e-1", "outcome": "ok"},
        {"clinician_id": "cn-icu", "action": "view_patient", "resource": "e-1", "outcome": "not_found"},
    ]
