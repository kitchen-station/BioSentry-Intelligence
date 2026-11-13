"""GET /patients/{id}/history: a stay as a short history, rebuilt on read from stored bins. A run is posted the way
the simulator posts it (the bin's minute is the END of its window), then read back through the API."""

import json
import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from test_baseline_route import served
from test_model_inputs import MODELS, Counting, read, stay
from test_overview import ADMIN, _last_seq, _rows_since, add_admin, write
from test_store import NOW, ORIGIN, Api, api, obs, start  # noqa: F401 (api: fixture)

from biosentry.core.audit import AuditLog
from biosentry.schemas.history import HISTORY_LABEL
from biosentry.services.history import NOT_REBUILT, HistoryService
from biosentry.services.ward import WardService

FIRST = 15  # the END minute of bin 0, as `stay` posts it
SICK = {"respiratory_rate": 25.0, "spo2": 91.0, "heart_rate": 131.0}  # NEWS2 total 9: high, and three single 3s


def minute(i: int) -> int:
    return FIRST + 15 * i


def sick_bins(body: dict[str, Any], values: dict[int, dict[str, float]]) -> dict[str, Any]:
    """The stay with these bins (index -> vital -> value) replaced."""
    for b in body["bins"]:
        i = (b["minute"] - FIRST) // 15
        if b["vital"] in values.get(i, {}):
            b["value"] = values[i][b["vital"]]
    return body


def get(api: Api, pid: str = "s-1", who: str = "rn-delacruz") -> Any:
    return api.client.get(f"/patients/{pid}/history", headers=api.as_(who))


def history(api: Api, pid: str = "s-1") -> dict[str, Any]:
    response = get(api, pid)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def counts(api: Api) -> dict[str, int]:
    tables = [r["tablename"] for r in api.sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    return {t: api.sql(f'SELECT count(*) AS n FROM "{t}"')[0]["n"] for t in tables}


# --- Who may read ------------------------------------------------------------------------------


def test_the_scope_and_the_audit_row_of_every_caller_and_patient(api: Api) -> None:
    add_admin(api)
    write(api, "UPDATE patient SET left_at = %s, outcome = 'discharged' WHERE id = 'p-002'", NOW)
    cases = [
        ("rn-delacruz", "p-001", 200),  # the assigned nurse
        ("md-santos", "p-001", 200),  # the assigned doctor
        ("rn-manalo", "p-001", 200),  # the charge nurse
        (ADMIN, "p-001", 200),  # an admin reads, and writes nothing
        ("rn-reyes", "p-001", 404),  # not assigned
        ("rn-delacruz", "p-zzz", 404),  # missing
        (ADMIN, "p-zzz", 404),
        (ADMIN, "p-004", 404),  # NOT RANKED
        (ADMIN, "p-002", 404),  # left the ward
    ]
    misses = set()
    for who, pid, status in cases:
        api.as_(who)  # the sign-in row is not the read's
        seq = _last_seq(api)
        response = get(api, pid, who)
        role = api.sql("SELECT role FROM clinician WHERE id = %s", who)[0]["role"]
        outcome = "ok" if status == 200 else "not_found"
        assert response.status_code == status, (who, pid)
        assert _rows_since(api, seq) == [(who, role, "view_history", pid, outcome)], (who, pid)
        assert response.headers["cache-control"] == "no-store"
        if status == 404:
            misses.add(response.text)
        else:
            assert response.json()["patient_id"] == pid and response.json()["label"] == HISTORY_LABEL
    assert len(misses) == 1  # the same answer for every kind of miss


def test_a_read_writes_nothing_but_its_audit_row(api: Api) -> None:
    start(api, patients=[sick_bins(stay("s-1", 20), {8: SICK}), stay("s-2", 5)])
    api.as_("rn-delacruz")  # the sign-in is not the read
    before = counts(api)
    assert history(api)["bins"] == 20
    after = counts(api)
    assert after.pop("audit_log") == before.pop("audit_log") + 1
    assert after == before  # every other table, row for row in number


# --- What it says ------------------------------------------------------------------------------


def test_a_stay_where_news2_rises_into_a_trigger_and_falls_again_is_one_alert_run(api: Api) -> None:
    body = stay("s-1", 20)
    sick_bins(body, {8: {"spo2": 91.0}, 9: SICK, 10: SICK})  # a single 3, then a total of 9 for two bins, then quiet
    start(api, patients=[body])
    got = history(api)
    assert (got["synthetic"], got["bins"], got["runs_cut"], got["events_cut"]) == (True, 20, False, False)
    assert got["grid_from"] == served(api, FIRST - 15) and got["grid_to"] == served(api, minute(19))
    (run,) = got["runs"]
    assert (run["state"], run["bins"], run["ongoing"]) == ("alert", 3, False)
    assert (run["start"], run["end"]) == (served(api, minute(8) - 15), served(api, minute(10)))
    assert (run["peak_score"], run["peak_level"], run["peak_at"]) == (9, "high", served(api, minute(9) - 15))
    assert run["reasons"] == ["Total 9 (≥7): high", "Single parameter at 3: respiratory rate, SpO2, heart rate"]
    assert run["provenance"] == {
        "source": "derived", "tier": "A", "rule": "news2_as_of_bins", "synthetic": True,
        "computed_at": run["provenance"]["computed_at"],
    }  # fmt: skip
    assert run["provenance"]["computed_at"] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert got["events"] == []


def test_a_single_3_alone_is_an_alert_and_a_run_still_going_is_ongoing(api: Api) -> None:
    start(api, patients=[sick_bins(stay("s-1", 12), {10: {"spo2": 91.0}, 11: {"spo2": 91.0}})])
    (run,) = history(api)["runs"]
    assert (run["bins"], run["ongoing"], run["peak_score"], run["peak_level"]) == (2, True, 3, "single_3")
    assert run["reasons"] == ["Single parameter at 3: SpO2"]


def test_a_gap_in_heart_rate_is_no_reading(api: Api) -> None:
    body = stay("s-1", 12)
    body["bins"] = [b for b in body["bins"] if not (b["vital"] == "heart_rate" and b["minute"] == minute(3))]
    body["bins"].append({"patient_id": "s-1", "vital": "heart_rate", "minute": minute(4), "value": None})
    start(api, patients=[body])
    (run,) = history(api)["runs"]  # a missing row and a null are both gaps
    assert (run["state"], run["bins"], run["peak_score"], run["peak_at"]) == ("no_reading", 2, None, None)
    assert run["reasons"] == ["No usable heart rate reading"]
    assert (run["start"], run["end"]) == (served(api, minute(3) - 15), served(api, minute(4)))


def test_bins_before_the_charted_values_were_stored_are_not_rebuilt_and_the_later_ones_are(api: Api) -> None:
    body = stay("s-1", 12)
    for o in body["observations"]:
        if o["source"] == "nurse":
            o["minute"] = minute(5)  # blood pressure, ACVPU, oxygen and scale first charted at bin 5's end
    start(api, patients=[body])
    (run,) = history(api)["runs"]
    assert (run["state"], run["bins"], run["reasons"], run["ongoing"]) == ("not_rebuilt", 5, [NOT_REBUILT], False)
    assert (run["start"], run["end"]) == (served(api, 0), served(api, minute(4)))


def test_a_nurse_typed_heart_rate_makes_the_bins_after_it_not_rebuilt_for_12_hours_and_not_after(api: Api) -> None:
    body = stay("s-1", 60)  # bins ending at minutes 15 to 900
    body["observations"].append(obs("s-1", "heart_rate", 120, "nurse", num=80))
    start(api, patients=[body])
    (run,) = history(api)["runs"]
    assert (run["state"], run["bins"], run["reasons"]) == ("not_rebuilt", 49, [NOT_REBUILT])
    assert (run["start"], run["end"]) == (served(api, 105), served(api, 840))  # ends 120 to 840: 12 hours, inclusive


def test_the_answer_of_an_older_run_with_the_same_patient_id_never_appears(api: Api) -> None:
    old = sick_bins(stay("s-1", 20), {8: SICK, 9: SICK})
    start(api, patients=[old])
    old_run = api.sql("SELECT max(id) AS i FROM run")[0]["i"]
    inside = datetime.fromisoformat(ORIGIN) + timedelta(minutes=60)  # run time, inside the new run's window
    write(api, "INSERT INTO clinician_action (run_id, kind, patient_id, clinician_id, at, data)"
              " VALUES (%s, 'acknowledge', 's-1', 'rn-delacruz', %s, '{\"score\": 9}')", old_run, inside)
    write(api, "INSERT INTO clinical_note (run_id, id, patient_id, author, role, timestamp, content, synthetic)"
              " VALUES (%s, 'n-900', 's-1', 'a', 'rn', %s, 'OLD-RUN-NOTE', true)", old_run, inside)
    start(api, patients=[stay("s-1", 20)])
    got = history(api)
    assert got["runs"] == [] and got["events"] == [] and "OLD-RUN" not in json.dumps(got)


# --- What was done -------------------------------------------------------------------------------


def test_each_event_kind_carries_its_fields_and_no_note_text_and_nothing_of_another_patient(api: Api) -> None:
    cuff = stay("s-1", 20)
    cuff["observations"].append(obs("s-1", "blood_pressure", 45, "nurse", num=110, num2=70))  # charted at the bedside
    start(api, patients=[cuff, stay("s-2", 20)])
    run_id = api.sql("SELECT max(id) AS i FROM run")[0]["i"]
    write(api, "UPDATE patient SET admitted_at = %s WHERE run_id = %s AND id IN ('s-1', 's-2')",
          datetime.fromisoformat(ORIGIN), run_id)  # run time: served time is this plus the run's shift
    nurse, doctor = api.as_("rn-delacruz"), api.as_("md-santos")
    post = api.client.post

    def at(minutes: int) -> None:
        api.wall.now = NOW + timedelta(minutes=minutes)

    at(-60)
    assert post("/patients/s-1/acknowledgement", headers=nurse, json={"score": 1}).status_code == 204  # before the stay
    at(60)
    assert post("/patients/s-1/acknowledgement", headers=nurse, json={"score": 7}).status_code == 204
    at(70)
    assert post("/patients/s-2/acknowledgement", headers=nurse, json={"score": 5}).status_code == 204
    at(90)
    assert post("/patients/s-1/vitals", headers=nurse, json={"systolic": 118, "diastolic": 76}).status_code == 200
    at(100)
    assert post("/patients/s-2/vitals", headers=nurse, json={"systolic": 99, "diastolic": 60}).status_code == 200
    at(120)
    kept = post("/patients/s-1/notes", headers=doctor, json={"author": "Dr Zed", "role": "MD", "content": "MARKER-9f3a"})
    assert kept.status_code == 201
    at(125)
    gone = post("/patients/s-1/notes", headers=nurse, json={"author": "Dr Zed", "role": "RN", "content": "MARKER-GONE"})
    assert api.client.delete(f"/patients/s-1/notes/{gone.json()['id']}", headers=nurse).status_code == 204
    at(130)
    assert post("/patients/s-2/notes", headers=nurse, json={"author": "x", "role": "rn", "content": "MARKER-OTHER"}).status_code == 201
    at(400)  # after the latest bin
    assert post("/patients/s-1/acknowledgement", headers=nurse, json={"score": 9}).status_code == 204
    at(0)
    response = get(api)
    assert response.status_code == 200
    for text in ("MARKER", "Dr Zed", "content"):
        assert text not in response.text
    events = response.json()["events"]
    assert [(e["kind"], e["at"]) for e in events] == [
        ("admitted", served(api, 0)), ("bp_charted", served(api, 45)), ("acknowledged", served(api, 60)),
        ("vitals_recorded", served(api, 90)), ("note_added", served(api, 120)),
    ]  # the pressure typed in the app at 90 is one act: no second entry for its row
    admitted, bp, ack, vitals, note = events
    assert (admitted["by"], admitted["source"]) == (None, "patient")
    assert (ack["by"], ack["score"], ack["role"], ack["note_id"], ack["source"]) == ("rn-delacruz", 7, None, None, "clinician_action")
    assert (vitals["by"], vitals["score"], vitals["source"]) == ("rn-delacruz", None, "clinician_action")
    assert (bp["by"], bp["role"], bp["source"]) == (None, None, "observation")
    assert (note["by"], note["role"], note["note_id"], note["source"]) == (None, "md", kept.json()["id"], "clinical_note")
    assert set(note) == {"kind", "at", "by", "role", "score", "note_id", "source"}
    assert [e["kind"] for e in response.json()["events"] if e["score"] == 5] == []
    at(450)  # read again later: what was done after the last stored bin is part of the stay
    later = get(api).json()
    assert [(e["kind"], e["at"], e["score"]) for e in later["events"]][-1] == ("acknowledged", served(api, 400), 9)
    assert later["grid_to"] == response.json()["grid_to"] and later["runs"] == response.json()["runs"]


# --- The ward read, and the cost --------------------------------------------------------------------


def statements(api: Api, run: Any) -> list[str]:
    store, _ = read(api)
    audit = AuditLog(store.conn, api.wall, store.role_of)
    wards = WardService(store, audit, MODELS)
    counting = Counting(store.conn)
    store.conn = counting
    run(store, audit, wards)
    return [q for q in counting.sql]


def test_the_ward_read_runs_the_same_statements_as_before_and_reads_nothing_of_the_history(api: Api) -> None:
    start(api, patients=[stay("s-1", 20), stay("s-2", 20)])
    ran = statements(api, lambda s, a, w: w.ward("rn-delacruz"))
    # Who may see (the role twice and the assigned set), 3 reads per patient, and the two reads of the models.
    assert len(ran) == 3 + 3 * 2 + 2
    joined = " ".join(ran)
    for table in ("clinician_action", "clinical_note"):
        assert table not in joined


def test_one_history_read_runs_four_statements_of_its_own(api: Api) -> None:
    start(api, patients=[stay("s-1", 20)])
    ran = statements(api, lambda s, a, w: HistoryService(s, a, w).compute("rn-delacruz", "s-1"))
    assert len(ran) == 2 + 3 + 3 + 1  # scope, the patient read, then temperature, observations and events, and the episode
    assert sum("sim_episode" in q for q in ran) == 1
    assert sum("clinician_action" in q for q in ran) == 1 and sum("temperature" in q for q in ran) == 1
    assert sum("FROM observation" in q or "JOIN observation" in q for q in ran) >= 1


def test_a_stay_of_672_bins_is_read_in_reasonable_time_and_the_runs_are_capped(api: Api) -> None:
    body = stay("s-1", 672)
    sick_bins(body, {i: SICK for i in range(1, 672, 2)})  # an alert every other bin: 336 runs
    start(api, patients=[body])
    history(api)  # a warm-up: the first request opens the connection
    t0 = time.perf_counter()
    response = get(api)
    seconds = time.perf_counter() - t0
    print(f"history of 672 bins: {seconds:.3f} s")
    assert response.status_code == 200 and seconds < 2.0
    got = response.json()
    assert got["bins"] == 672 and got["runs_cut"] is True and len(got["runs"]) == 200
    assert got["runs"][-1]["ongoing"] is True and got["runs"][-1]["end"] == got["grid_to"]
    assert [r["start"] for r in got["runs"]] == sorted(r["start"] for r in got["runs"])


def test_a_patient_with_no_stored_bins_has_an_empty_grid(api: Api) -> None:
    start(api, patients=[stay("s-1", 0)])
    got = history(api)
    assert (got["grid_from"], got["grid_to"], got["bins"], got["runs"], got["events"]) == (None, None, 0, [], [])
    assert got["unavailable"] == "no_bins"


def test_a_stay_with_more_bins_than_the_grid_says_so_and_a_quiet_stay_says_nothing(api: Api) -> None:
    start(api, patients=[stay("s-1", 673), stay("s-2", 20)])
    long = history(api)
    assert (long["unavailable"], long["bins"], long["runs"], long["grid_to"]) == ("past_cap", 0, [], None)
    quiet = history(api, "s-2")
    assert (quiet["unavailable"], quiet["bins"], quiet["runs"]) == (None, 20, [])


def test_a_caller_who_may_not_see_the_patient_reads_nothing_of_the_patient(api: Api) -> None:
    start(api, patients=[stay("s-1", 20)])
    ran = statements(api, lambda s, a, w: HistoryService(s, a, w).compute("rn-reyes", "s-1"))
    assert len(ran) == 2  # the scope only: no read of the patient, the bins, the observations or the events
    for table in ("vital_bin", "observation", "clinician_action", "clinical_note"):
        assert not any(table in q for q in ran)
