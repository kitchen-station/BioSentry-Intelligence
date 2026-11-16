"""The bed card (GET /wards/{ward}/open-view/beds/{bed}): one occupied bed for a caller nobody
identified, behind the open-view setting. A ranked bed gives its NEWS2 score and band, five stored
vitals with time and source, and the trend word. Any other bed gives its state and the reason.
Nothing that names the patient or a clinician is in it, and a stay that ended is a 404."""

from datetime import timedelta
from typing import Any

import pytest
from test_api import VALID_BODIES, _route_table
from test_open_view import NOT_FOUND, watch  # noqa: F401 (watch: the API with the setting on)
from test_store import Api, api, obs  # noqa: F401 (api: fixture)
from test_units import PATIENTS, depart, hospital, stay

from biosentry.services import ward as ward_module

URL = "/wards/W4/open-view/beds/4A"
TEMPLATE = "/wards/{ward}/open-view/beds/{bed}"
TOP = {"unit", "floor", "kind", "bed", "synthetic", "state", "score", "risk", "reason", "vitals", "trend", "history"}
FIVE = {"heart_rate", "respiratory_rate", "spo2", "temperature", "blood_pressure"}
MARKED = {"name": "NAME-MARK", "context": "CONTEXT-MARK", "story": "STORY-MARK", "person": "PERSON-MARK"}


def get(api: Api, path: str = URL, **headers: str) -> Any:
    return api.client.get(path, headers=headers)


def rows(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT clinician_id, role, action, resource, outcome FROM audit_log WHERE action = 'view_open_bed' ORDER BY seq")


def card(api: Api, unit: str, bed: str) -> dict[str, Any]:
    response = get(api, f"/wards/{unit}/open-view/beds/{bed}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def not_ranked_w4() -> dict[str, Any]:
    """A W4 patient with no heart rate: NOT RANKED in a ranked unit. The reason sent is a marker."""
    p = stay("w-2", "W4", "rn-delacruz", "md-santos", "4B")
    kept = [o for o in p["observations"] if o["code"] != "heart_rate"]
    return {**p, "observations": [*kept, obs("w-2", "heart_rate", -1, absent_reason="ABSENT-MARK")]}


# --- Off: the route does not exist --------------------------------------------------------------


def test_off_the_path_is_an_unknown_path_and_writes_nothing(api: Api) -> None:
    hospital(api)
    unknown = get(api, "/no-such-path")
    for headers in ({}, api.as_("rn-delacruz")):
        response = get(api, **headers)
        assert (response.status_code, response.content) == (404, unknown.content)
    assert all(path != TEMPLATE for _, path in _route_table(api.client.app))
    for method in ("post", "head", "options"):
        assert getattr(api.client, method)(URL).status_code == 404
    assert rows(api) == []


# --- On: no credential is read -------------------------------------------------------------------


def test_it_answers_with_no_header_and_the_same_bytes_with_any_credential(watch: Api) -> None:
    hospital(watch)
    plain = get(watch)
    assert plain.status_code == 200 and plain.headers["cache-control"] == "no-store"
    token = watch.as_("md-santos")["Authorization"]
    for headers in (
        {"Authorization": token}, {"Authorization": "Bearer junk"}, {"Cookie": "session=x"},
        {"X-Ingest-Key": "test-ingest-key"}, {"Authorization": "Basic abc"},
    ):  # fmt: skip
        assert get(watch, **headers).content == plain.content
    assert watch.sql("SELECT * FROM audit_log WHERE action = 'authenticate'") == []


def test_it_takes_get_only(watch: Api) -> None:
    hospital(watch)
    assert [getattr(watch.client, m)(URL).status_code for m in ("post", "head", "put", "delete")] == [405] * 4
    assert rows(watch) == []


# --- A ranked bed -------------------------------------------------------------------------------


def test_a_ranked_bed_gives_the_score_the_band_five_vitals_with_time_and_source_and_the_trend(watch: Api) -> None:
    hospital(watch)
    body = card(watch, "W4", "4A")
    [own] = watch.ward("rn-delacruz")["ranked"]  # what the patient's own nurse is served
    assert body.keys() == TOP and body["vitals"].keys() == FIVE
    place = {k: body[k] for k in ("unit", "floor", "kind", "bed", "synthetic", "state", "reason")}
    assert place == {"unit": "W4", "floor": 4, "kind": "ranked", "bed": "4A", "synthetic": True, "state": "ranked", "reason": None}
    assert (body["score"], body["risk"], body["trend"]) == (own["score"], "high", own["trend"])
    for code in FIVE - {"blood_pressure"}:
        stored = own["vitals"][code]
        assert body["vitals"][code] == {
            "value": stored["data"]["value"],
            "observed_at": stored["provenance"]["observed_at"],
            "source": stored["provenance"]["source"],
        }
    pressure = own["vitals"]["blood_pressure"]
    assert body["vitals"]["blood_pressure"] == {
        **pressure["data"]["value"],
        "observed_at": pressure["provenance"]["observed_at"],
        "source": "nurse",
    }


def test_nothing_that_names_the_patient_or_a_clinician_is_on_the_card(watch: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    marked = {**stay("PID-MARK", "W4", "rn-delacruz", "md-santos", "4A"), **MARKED}
    marked["observations"] = [{**o, "patient_id": "PID-MARK"} for o in marked["observations"]]
    marked["bins"] = [{**b, "patient_id": "PID-MARK"} for b in marked["bins"]]
    hospital(watch, [marked, not_ranked_w4(), *PATIENTS[1:]])
    nurse = watch.as_("rn-delacruz")
    note = {**VALID_BODIES[("POST", "/patients/{patient_id}/notes")], "content": "NOTE-MARK"}
    assert watch.client.post("/patients/PID-MARK/notes", json=note, headers=nurse).status_code == 201
    assert watch.client.post("/patients/PID-MARK/labs", json={"potassium": 4.44}, headers=nurse).status_code == 200

    def boom(*args: Any) -> Any:
        raise AssertionError("a model ran for a bed card")

    monkeypatch.setattr(ward_module, "ward_model_signals", boom)
    for unit, bed in (("W4", "4A"), ("W4", "4B"), ("ER", "E1"), ("ICU", "I1")):
        response = get(watch, f"/wards/{unit}/open-view/beds/{bed}")
        assert response.status_code == 200 and response.json().keys() == TOP, (unit, bed)
        for mark in ("MARK", "Sim, Pat", "rn-delacruz", "md-santos", "cn-er", "cn-icu", "w-2", "e-1", "i-1", "potassium", "4.44"):
            assert mark not in response.text, (unit, bed, mark)
        for word in ("name", "age", "sex", "context", "note", "lab", "baseline", "model", "clinician", "person", "patient"):
            assert f'"{word}' not in response.text, (unit, bed, word)


# --- A bed that is not ranked -------------------------------------------------------------------


def test_a_bed_that_is_not_ranked_gives_its_state_and_the_reason_only(watch: Api) -> None:
    hospital(watch, [*PATIENTS, not_ranked_w4()])
    body = card(watch, "W4", "4B")
    [own] = watch.ward("rn-delacruz")["abstained"]
    assert body == {
        "unit": "W4", "floor": 4, "kind": "ranked", "bed": "4B", "synthetic": True, "state": "not_ranked",
        "score": None, "risk": None, "reason": own["reason"], "vitals": None, "trend": None, "history": None,
    }  # fmt: skip
    assert own["reason"] and "ABSENT-MARK" not in own["reason"]


@pytest.mark.parametrize(
    ("unit", "bed", "floor", "kind", "reason"),
    [
        ("ER", "E1", 0, "shown", "Emergency Room is shown, not ranked: NEWS2 is a ward score."),
        ("ICU", "I1", 2, "unranked", "Intensive Care is not ranked: NEWS2 is a ward score."),
    ],
)
def test_a_bed_of_a_unit_that_is_not_ranked_gives_no_vital_no_score_and_no_band(
    watch: Api, unit: str, bed: str, floor: int, kind: str, reason: str
) -> None:
    hospital(watch)  # both patients have every NEWS2 input stored
    assert card(watch, unit, bed) == {
        "unit": unit, "floor": floor, "kind": kind, "bed": bed, "synthetic": True, "state": "not_ranked",
        "score": None, "risk": None, "reason": reason, "vitals": None, "trend": None, "history": None,
    }  # fmt: skip


# --- No such bed ----------------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["discharged", "higher_care", "died", "transferred"])
def test_a_stay_that_ended_is_the_framework_404(watch: Api, outcome: str) -> None:
    run_id = hospital(watch)
    assert get(watch).status_code == 200
    ending = {"patient_id": "w-1", "minute": 30, "outcome": outcome, **({"to_unit": "ICU"} if outcome == "transferred" else {})}
    assert depart(watch, run_id, ending).status_code == 204
    response = get(watch)
    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    assert response.content == get(watch, "/no-such-path").content


def test_an_unknown_unit_or_bed_is_the_same_404_and_the_row_holds_no_path_text(watch: Api) -> None:
    hospital(watch)
    for path in (
        "/wards/W9/open-view/beds/4A", "/wards/W4/open-view/beds/9Z", "/wards/ER/open-view/beds/4A",
        "/wards/W4/open-view/beds/" + "x" * 300, "/wards/W4/open-view/beds/a%00b", "/wards/W4/open-view/beds/%E2%82%AC%20'--",
        "/wards/W4/open-view/beds/4a", "/wards/W4/open-view/beds/%204A",
    ):  # fmt: skip
        response = get(watch, path)
        assert (response.status_code, response.json()) == (404, NOT_FOUND), path
        assert response.headers["cache-control"] == "no-store"
    assert rows(watch) == [{"clinician_id": None, "role": None, "action": "view_open_bed", "resource": "-", "outcome": "not_found"}]


def test_a_bed_label_two_patients_share_and_a_label_that_breaks_the_rule_are_404(watch: Api) -> None:
    twin = stay("w-9", "W4", "rn-reyes", None, "4A")
    odd = stay("w-8", "W4", "rn-reyes", None, "B!")
    hospital(watch, [*PATIENTS, twin, odd])
    assert get(watch).status_code == 404  # 4A names two patients: neither is picked
    assert get(watch, "/wards/W4/open-view/beds/B%21").status_code == 404
    assert card(watch, "MED3A", "M1")["bed"] == "M1"


def test_a_run_that_is_not_synthetic_is_the_same_404(watch: Api) -> None:
    hospital(watch)
    real = watch.client.app.state.open_ctx

    def not_synthetic() -> Any:
        ctx = real()
        ctx.store.synthetic = False
        return ctx

    watch.client.app.state.open_ctx = not_synthetic
    response = get(watch)
    assert (response.status_code, response.content) == (404, get(watch, "/no-such-path").content)
    assert [(r["resource"], r["outcome"]) for r in rows(watch)] == [("-", "not_found")]


# --- The audit row ----------------------------------------------------------------------------------


def test_one_row_a_minute_for_each_unit_with_no_clinician_no_bed_and_no_patient(watch: Api) -> None:
    hospital(watch, [*PATIENTS, not_ranked_w4()])
    for _ in range(5):
        card(watch, "W4", "4A")
        card(watch, "W4", "4B")
        card(watch, "ER", "E1")
        get(watch, "/wards/W4/open-view/beds/9Z")
        get(watch, **watch.as_("rn-delacruz"))
    seen = rows(watch)
    assert [(r["clinician_id"], r["role"], r["resource"], r["outcome"]) for r in seen] == [
        (None, None, "W4", "ok"), (None, None, "ER", "ok"), (None, None, "-", "not_found"),
    ]  # fmt: skip
    watch.wall.now += timedelta(seconds=61)
    card(watch, "W4", "4B")
    assert [(r["resource"], r["outcome"]) for r in rows(watch)][-1] == ("W4", "ok")
    stored = watch.sql("SELECT * FROM audit_log WHERE action = 'view_open_bed'")
    for mark in ("4A", "4B", "E1", "w-1", "w-2", "e-1"):
        assert mark not in str(stored), mark
