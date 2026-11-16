"""A unit's `score_note`: one printable line the run sends with a unit (the ER is ranked "as an aid")
and the API serves with it: in the public unit list, in GET /wards and in the unit's rows. An ER of
kind `ranked` is ranked like any ward, so its patients have a history and a baseline."""

from typing import Any

import pytest
from psycopg.errors import CheckViolation
from test_open_view import watch  # noqa: F401 (watch: the API with the setting on)
from test_overview import write
from test_store import Api, api, new_run  # noqa: F401 (api: fixture)
from test_units import PATIENTS, UNITS, get

from biosentry.db.passphrase import hash_passphrase
from biosentry.db.seed import DEV_PASSPHRASE

NOTE = "NEWS2 as an aid: mixed evidence in the emergency department (RCP 2017/2020; studies AUROC 0.59-0.88)"
ER_RANKED = [{**u, "kind": "ranked", "score_note": NOTE} if u["id"] == "ER" else u for u in UNITS]


def run(api: Api, units: list[dict[str, Any]] = ER_RANKED) -> Any:
    for cn in ("cn-er", "cn-icu", "cn-med3a"):
        write(api, "UPDATE clinician SET passphrase_hash = %s WHERE id = %s", hash_passphrase(DEV_PASSPHRASE), cn)
    return api.post("/sim/runs", {**new_run(patients=PATIENTS), "units": units})


def test_the_note_is_served_in_the_public_list_in_wards_and_in_the_rows_of_its_unit(watch: Api) -> None:
    assert run(watch).status_code == 201
    listed = watch.client.get("/open-view").json()["units"]
    assert {u["id"]: u["score_note"] for u in listed} == {"W4": None, "ER": NOTE, "ICU": None, "MED3A": None}
    assert all(u.keys() == {"id", "name", "floor", "kind", "score_note"} for u in listed)
    assert [(u["id"], u["kind"]) for u in listed][1] == ("ER", "ranked")
    assert {u["id"]: u["score_note"] for u in get(watch, "md-santos", "/wards").json()} == {
        "W4": None, "ER": NOTE, "ICU": None, "MED3A": None,
    }  # fmt: skip
    assert get(watch, "cn-er", "/wards/ER/rows").json()["score_note"] == NOTE
    assert get(watch, "md-santos", "/wards/W4/rows").json()["score_note"] is None


def test_a_run_with_no_note_serves_null_and_a_run_of_one_unit_still_starts(api: Api) -> None:
    assert run(api, UNITS).status_code == 201
    assert {u["score_note"] for u in get(api, "md-santos", "/wards").json()} == {None}
    old = api.post("/sim/runs", new_run(patients=[PATIENTS[0]]))  # today's body: no units at all
    assert old.status_code == 201


@pytest.mark.parametrize(
    "note",
    ["", "x" * 301, "two\nlines", "tab\there", "nul\x00", "bell\x07", "c1\x85", "c1\x80", "c1\x9f", "line\u2028sep", "para\u2029sep", "del\x7f", 7, ["a"], {"a": 1}],
)
def test_a_note_that_is_not_one_printable_line_of_300_characters_or_less_is_422(api: Api, note: Any) -> None:
    units = [{**u, "score_note": note} if u["id"] == "ER" else u for u in UNITS]
    assert run(api, units).status_code == 422


def test_a_note_of_300_characters_and_one_with_unicode_text_are_kept(api: Api) -> None:
    for note in ("x" * 300, "NEWS2 — an aid (RCP 2017/2020) · AUROC 0.59–0.88"):
        units = [{**u, "score_note": note} if u["id"] == "ER" else u for u in UNITS]
        assert run(api, units).status_code == 201
        assert get(api, "cn-er", "/wards/ER/rows").json()["score_note"] == note
        api.wall.now += __import__("datetime").timedelta(seconds=31)  # the next run may replace this one


def test_a_ranked_er_ranks_its_patient_and_its_history_and_baseline_answer_like_a_ward(api: Api) -> None:
    assert run(api).status_code == 201
    [row] = get(api, "cn-er", "/wards/ER/rows").json()["ranked"]
    assert (row["id"], row["rank"], row["unit"]) == ("e-1", 1, "ER")
    assert get(api, "cn-er", "/wards/ER/rows").json()["abstained"] == []
    page = get(api, "cn-er", "/patients/e-1").json()
    assert page["score"] == row["score"] and "tier_breakdown" in page
    for path in ("/history", "/baseline"):
        assert get(api, "cn-er", f"/patients/e-1{path}").status_code == 200, path
    assert get(api, "cn-icu", "/patients/e-1/history").status_code == 404  # another unit's nurse: nothing


@pytest.mark.parametrize("note", ["x\x80", "x\x85", "x\x9f", "x\x7f", "x\x1f", "x\u2028", "x\u2029", "", "x" * 301])
def test_the_table_refuses_the_same_notes_whatever_the_database_locale(api: Api, note: str) -> None:
    assert run(api, UNITS).status_code == 201
    with pytest.raises(CheckViolation):
        write(api, "UPDATE run_unit SET score_note = %s WHERE id = 'ER'", note)


@pytest.mark.parametrize("note", ["é\u00a0·—", "x" * 300, "\u00a0", "\u2027\u202a"])
def test_the_table_keeps_printable_text_next_to_the_refused_ranges(api: Api, note: str) -> None:
    assert run(api, UNITS).status_code == 201
    write(api, "UPDATE run_unit SET score_note = %s WHERE id = 'ER'", note)
    assert api.sql("SELECT score_note FROM run_unit WHERE id = 'ER'") == [{"score_note": note}]
