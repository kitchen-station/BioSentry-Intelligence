"""GET /wards/{ward}/research: the whole ward as one research table for a physician or charge nurse. Scope is
decided on the server: a patient who is not the reader's is a bed label and numbers, with no id and no name.
Every number is the one another route serves; a stored episode and a model flag change nothing."""

import json
from typing import Any

import psycopg
import pytest
from test_history_route import SICK, sick_bins
from test_model_flags_api import REAL, client_for, force, world
from test_model_inputs import stay
from test_overview import ADMIN, _last_seq, _rows_since, add_admin, keys, write
from test_sim_episodes import ep, live, put
from test_store import Api, api, start  # noqa: F401 (api: fixture)

from biosentry.schemas import WardResearch
from biosentry.schemas.research_view import RESEARCH_LABEL

URL = "/wards/W4/research"
RANKED = {"bed", "assigned", "id", "name", "rank", "score", "level", "baseline", "models", "alert"}
ABSTAINED = {"bed", "assigned", "id", "name", "reason", "instruction"}
MINE = {"p-001", "p-002", "p-006", "p-008", "p-011"}  # md-santos's patients in the fixture ward
# Measured: the statements one read runs for the fixture ward of 12 patients (about five for each: the ward's rows,
# the baseline and the stay history's alert bins are read for every patient, plus the audit row). It pins the cost, so a new
# query on this route shows up here.
STATEMENTS = 67


def get(api: Api, who: str = "md-santos", path: str = URL) -> Any:
    return api.client.get(path, headers=api.as_(who))


def research(api: Api, who: str = "md-santos") -> dict[str, Any]:
    response = get(api, who)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    WardResearch.model_validate(body)
    return body


def tag(api: Api) -> None:
    """A marker in every free-text field of the fixture ward, and a note on some patients."""
    write(
        api,
        "UPDATE patient SET name = 'MARKNAME-' || id, admitting_context = 'MARKCONTEXT-' || id,"
        " mechanism = 'MARKMECH-' || id, supporting = 'MARKSUPPORT-' || id, story = 'MARKSTORY-' || id",
    )
    for pid in ("p-001", "p-003", "p-004", "p-007"):
        note = {"author": "MARKAUTHOR", "role": "RN", "content": f"MARKNOTE-{pid}"}
        assert api.client.post(f"/patients/{pid}/notes", json=note, headers=api.as_("rn-manalo")).status_code == 201


# --- Who may read, and the audit row --------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "role", "ward", "status", "outcome"),
    [
        ("md-santos", "md", "W4", 200, "ok"),
        ("rn-manalo", "charge_rn", "W4", 200, "ok"),
        ("rn-reyes", "rn", "W4", 403, "denied"),
        ("rn-reyes", "rn", "W9", 403, "denied"),
        (ADMIN, "admin", "W4", 403, "denied"),
        ("md-santos", "md", "W9", 404, "not_found"),
        ("rn-manalo", "charge_rn", "W9", 404, "not_found"),
    ],
)
def test_each_role_and_outcome_writes_one_audit_row(api: Api, who: str, role: str, ward: str, status: int, outcome: str) -> None:
    add_admin(api)
    api.as_(who)  # the sign-in writes its own audit row: it comes before the snapshot
    seq = _last_seq(api)
    response = get(api, who, f"/wards/{ward}/research")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert _rows_since(api, seq) == [(who, role, "view_ward_research", ward, outcome)]
    if status != 200:
        assert "bed" not in response.text


def test_every_read_writes_a_row_because_the_page_has_no_timer(api: Api) -> None:
    api.as_("md-santos")  # sign in first: the sign-in has its own audit row
    seq = _last_seq(api)
    research(api)
    research(api)
    assert len(_rows_since(api, seq)) == 2


# --- Scope --------------------------------------------------------------------------------------


def test_a_row_of_a_patient_who_is_not_the_readers_has_no_id_and_no_name(api: Api) -> None:
    body = research(api)
    assert set(body) == {"ward", "now", "synthetic", "label", "ranked", "abstained"}
    assert (body["ward"], body["synthetic"], body["label"]) == ("W4", True, RESEARCH_LABEL)
    rows = [*body["ranked"], *body["abstained"]]
    assert len(rows) == 12  # the whole ward, whoever is assigned
    assert all(set(r) == (RANKED if "rank" in r else ABSTAINED) for r in rows)
    assert {r["id"] for r in rows if r["assigned"]} == MINE
    others = [r for r in rows if not r["assigned"]]
    assert len(others) == 7 and all(r["id"] is None and r["name"] is None and r["bed"] for r in others)
    assert all(r["id"] and r["name"] for r in rows if r["assigned"])
    charge = research(api, "rn-manalo")
    assert all(r["assigned"] and r["id"] and r["name"] for r in [*charge["ranked"], *charge["abstained"]])


NUMBERS = {
    "baseline_median", "latest", "latest_at", "latest_age_s", "baseline_from", "anchored_at", "delta", "z",
    "band_low", "band_high", "run_bins", "since", "pathway", "unit",
}  # fmt: skip


def test_a_patient_who_is_not_the_readers_has_the_flag_of_each_vital_and_no_number(api: Api) -> None:
    body = research(api)
    mine = [v for r in body["ranked"] if r["assigned"] for v in r["baseline"]]
    others = [v for r in body["ranked"] if not r["assigned"] for v in r["baseline"]]
    assert others and mine
    assert {v["status"] for v in mine} >= {"computed"}  # the reader's own patients keep every number
    assert all(NUMBERS <= set(v) for v in mine if v["status"] == "computed")
    assert {v["status"] for v in others} >= {"flag_only"}  # a computed vital is only its flag
    assert {v["status"] for v in others} <= {"flag_only", "forming", "unavailable"}
    for v in others:
        assert not NUMBERS & set(v), v
        if v["status"] == "flag_only":
            assert set(v) == {"vital", "status", "significant", "direction"}
    charge = research(api, "rn-manalo")  # the charge nurse owns every patient: nothing is held back
    assert "flag_only" not in {v["status"] for r in charge["ranked"] for v in r["baseline"]}


def test_a_ranked_row_without_a_news2_decision_is_an_error_not_a_silent_row(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("biosentry.services.ward_research.decide", lambda state: None)
    with pytest.raises(RuntimeError, match="no NEWS2 decision"):
        get(api)


def test_no_free_text_of_a_patient_who_is_not_the_readers_reaches_the_answer(api: Api) -> None:
    tag(api)
    text = get(api).text
    for n in range(1, 13):
        pid = f"p-{n:03d}"
        for word in (pid, f"MARKNAME-{pid}"):
            assert (word in text) is (pid in MINE), word
    # no note, no other free text and no clinician id, even for the reader's own patients
    for word in ("MARKNOTE", "MARKAUTHOR", "MARKCONTEXT", "MARKMECH", "MARKSUPPORT", "MARKSTORY", "rn-", "md-"):
        assert word not in text, word
    assert keys(json.loads(text)) >= RANKED | ABSTAINED


def test_the_audit_trail_holds_no_patient_name(api: Api) -> None:
    tag(api)
    get(api)
    get(api, "rn-reyes")
    assert "MARK" not in json.dumps(api.sql("SELECT * FROM audit_log WHERE action = 'view_ward_research'"), default=str)


# --- The order and the numbers ----------------------------------------------------------------------


def test_the_order_and_the_rank_are_the_ward_lists_for_a_reader_who_sees_the_whole_ward(api: Api) -> None:
    add_admin(api)
    listed = api.ward(ADMIN)
    body = research(api)
    assert [(r["bed"], r["rank"], r["score"]) for r in body["ranked"]] == [(r["bed"], r["rank"], r["score"]) for r in listed["ranked"]]
    assert [r["models"] for r in body["ranked"]] == [r["models"] for r in listed["ranked"]]
    assert [(a["bed"], a["reason"], a["instruction"]) for a in body["abstained"]] == [
        (a["bed"], a["reason"], a["instruction"]) for a in listed["abstained"]
    ]
    assert [r["rank"] for r in body["ranked"]] == list(range(1, len(body["ranked"]) + 1))
    assert body["ranked"][0]["level"] == "high" and {"low", "medium"} <= {r["level"] for r in body["ranked"]}
    assert len(body["abstained"]) == 2 and all(a["reason"] and a["instruction"] for a in body["abstained"])


def test_the_baseline_equals_the_baseline_route_for_the_same_patient(api: Api) -> None:
    body = research(api, "rn-manalo")
    for row in body["ranked"]:
        served = api.client.get(f"/patients/{row['id']}/baseline", headers=api.as_("rn-manalo"))
        assert served.status_code == 200
        assert row["baseline"] == served.json()["vitals"], row["id"]
    assert {v["status"] for r in body["ranked"] for v in r["baseline"]} >= {"computed"}


def check_alert(api: Api, body: dict[str, Any], who: str) -> None:
    """Each ranked row's alert is what its patient's history route says: the bins of its alert runs."""
    for row in body["ranked"]:
        served = api.client.get(f"/patients/{row['id']}/history", headers=api.as_(who))
        assert served.status_code == 200
        history = served.json()
        runs = [r for r in history["runs"] if r["state"] == "alert"]
        if history["unavailable"]:
            assert row["alert"] == {"status": "unavailable", "reason": history["unavailable"]}
        else:
            bins = sum(r["bins"] for r in runs)
            assert row["alert"] == {"status": "counted", "bins": bins, "minutes": bins * 15, "ongoing": any(r["ongoing"] for r in runs)}


def test_the_alert_numbers_are_what_the_history_route_implies(api: Api) -> None:
    run = sick_bins(stay("s-1", 20), {8: SICK, 9: SICK, 19: SICK})  # a run of two, then one at the latest bin
    start(api, patients=[run, {**stay("s-2", 5), "bed": "2A"}, {**stay("s-3", 0), "bed": "3A"}, {**stay("s-4", 673), "bed": "4A"}])
    body = research(api)
    by_bed = {r["bed"]: r["alert"] for r in body["ranked"]}
    assert by_bed["9A"] == {"status": "counted", "bins": 3, "minutes": 45, "ongoing": True}
    assert by_bed["2A"] == {"status": "counted", "bins": 0, "minutes": 0, "ongoing": False}
    assert by_bed["3A"] == {"status": "unavailable", "reason": "no_bins"}
    assert by_bed["4A"] == {"status": "unavailable", "reason": "past_cap"}
    check_alert(api, body, "md-santos")


def test_the_fixture_ward_alert_numbers_are_the_histories(api: Api) -> None:
    check_alert(api, research(api, "rn-manalo"), "rn-manalo")


def test_a_model_flag_does_not_move_a_row(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    world(api)
    quiet = research(api)
    api.client = client_for(api, REAL)
    force(monkeypatch, p=1.0, alert=True)
    flagged = research(api)
    assert {r["models"]["prediction"]["state"] for r in flagged["ranked"]} >= {"flag"}
    assert [(r["bed"], r["id"], r["rank"], r["score"]) for r in flagged["ranked"]] == [
        (r["bed"], r["id"], r["rank"], r["score"]) for r in quiet["ranked"]
    ]
    assert [r["models"] for r in flagged["ranked"]] != [r["models"] for r in quiet["ranked"]]
    assert "probability" not in json.dumps(flagged)
    assert [a["id"] for a in flagged["abstained"]] == [a["id"] for a in quiet["abstained"]] == ["w-4"]
    assert flagged["abstained"][0]["reason"] and flagged["abstained"][0]["instruction"]


def test_a_stored_episode_changes_nothing(api: Api) -> None:
    run_id = live(api, [sick_bins(stay("s-1", 20), {8: SICK, 9: SICK})])
    before = research(api)
    assert put(api, run_id, ep()).status_code == 204
    assert api.sql("SELECT count(*) AS n FROM sim_episode")[0]["n"] == 1
    after = research(api)
    assert after == before
    assert "episode" not in json.dumps(after)


# --- The cost -----------------------------------------------------------------------------------------


def test_the_statements_for_a_ward_of_twelve(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    research(api)
    seen = [0]
    real = psycopg.Connection.execute

    def counting(self: Any, *args: Any, **kw: Any) -> Any:
        seen[0] += 1
        return real(self, *args, **kw)

    monkeypatch.setattr(psycopg.Connection, "execute", counting)
    assert get(api).status_code == 200
    assert seen[0] == STATEMENTS
