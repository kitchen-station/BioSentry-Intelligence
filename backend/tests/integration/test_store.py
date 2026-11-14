"""The PostgreSQL store: it reproduces the fixture, survives a restart, keeps its invariants in the
database, isolates runs, ingests the ward simulation and times it against the wall clock."""

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from conftest import dump_database, together
from fastapi.testclient import TestClient

from biosentry.api.app import create_app
from biosentry.core.config import DEFAULT_DATASET, Settings
from biosentry.db.seed import DEV_PASSPHRASE, ensure, reset
from biosentry.repositories.postgres import connect
from biosentry.schemas.ingest import Batch
from biosentry.services import ingest
from samples import admit_input  # isort: skip

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
KEY = "test-ingest-key"
DATA: dict[str, Any] = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
SEEDED = ("tier_b", "tier_c")  # demo keys the fixture file keeps and the API no longer serves


def bare(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in SEEDED}


CLINICIANS: dict[str, list[str]] = {c: list(e["patients"]) for c, e in DATA["clinicians"].items()}


class Wall:
    """A wall clock the test can move."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Api:
    def __init__(self, database_url: str, ingest_key: str | None = KEY) -> None:
        self.url = database_url
        self.wall = Wall()
        self.settings = Settings(database_url=database_url, ingest_key=ingest_key)
        self.client = TestClient(create_app(self.settings, clock=self.wall))
        self.tokens: dict[str, dict[str, str]] = {}

    def as_(self, clinician: str) -> dict[str, str]:
        if clinician not in self.tokens:
            body = {"clinician_id": clinician, "passphrase": DEV_PASSPHRASE}
            token = self.client.post("/session", json=body).json()["token"]
            self.tokens[clinician] = {"Authorization": f"Bearer {token}"}
        return self.tokens[clinician]

    def ward(self, clinician: str) -> dict[str, Any]:
        response = self.client.get("/wards/W4/rows", headers=self.as_(clinician))
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    def sql(self, statement: str, *args: Any) -> list[dict[str, Any]]:
        with connect(self.url) as conn:
            return conn.execute(statement, args).fetchall()

    def post(self, path: str, body: Any, key: str | None = KEY) -> Any:
        headers = {"X-Ingest-Key": key} if key is not None else {}
        return self.client.post(path, json=body, headers=headers)


@pytest.fixture
def api(database_url: str) -> Api:
    return Api(database_url)


# --- A sim ward: one ranked patient (NEWS2 10), one with no device data ----------------------

ORIGIN = "2026-01-01T00:00:00Z"


def obs(pid: str, code: str, minute: int, source: str = "device", **value: Any) -> dict[str, Any]:
    quality = "missing" if "absent_reason" in value else "ok"
    return {
        "patient_id": pid,
        "code": code,
        "minute": minute,
        "source": source,
        "quality": quality,
        **value,
    }


def sim_patient(pid: str = "s-1", rn: str = "rn-delacruz", md: str | None = "md-santos") -> dict:
    device = [
        obs(pid, "heart_rate", -2, num=118),
        obs(pid, "respiratory_rate", -2, num=24),
        obs(pid, "spo2", -2, num=93),
        obs(pid, "temperature", -2, num=38.5),
    ]
    nurse = [
        obs(pid, "blood_pressure", -10, "nurse", num=88, num2=52),
        obs(pid, "consciousness", -10, "nurse", txt="A"),
        obs(pid, "supplemental_o2", -10, "nurse", num=0),
        obs(pid, "spo2_scale", -10, "nurse", num=1),
    ]
    bins = [
        {"patient_id": pid, "vital": v, "minute": m, "value": None if m == -15 else 100.0 + m}
        for v in ("heart_rate", "respiratory_rate", "spo2", "temperature")
        for m in range(-45, 0, 15)
    ]
    return {
        "id": pid,
        "name": "Sim, Pat",
        "age": 61,
        "sex": "F",
        "bed": "9A",
        "context": "ctx",
        "story": "a story",
        "rn": rn,
        "md": md,
        "trend": "rising",
        "signal_quality": 97,
        "observations": [*device, *nurse],
        "bins": bins,
    }


def new_run(
    source: str = "synthetic", patients: list[dict] | None = None, client_id: str = "client-a"
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "source": source,
        "seed": 7,
        "origin": ORIGIN,
        "patients": patients or [sim_patient()],
    }


def batch(minute: int = 0, **over: Any) -> dict[str, Any]:
    body = {
        "minute": minute,
        "ended": False,
        "counts": {"received": 5, "rejected": 1, "abstained": 2},
        "patients": [
            {"id": "s-1", "trend": "stable", "signal_quality": 90, "last_sample_minute": minute}
        ],
        "observations": [obs("s-1", "heart_rate", minute, num=120)],
        "bins": [{"patient_id": "s-1", "vital": "heart_rate", "minute": minute, "value": 120.0}],
        "events": [
            {"minute": minute, "kind": "call", "patient_id": "s-1", "staff_id": None, "text": "t"}
        ],
    }
    return {**body, **over}


def start(api: Api, **kw: Any) -> int:
    response = api.post("/sim/runs", new_run(**kw))
    assert response.status_code == 201, response.text
    run_id: int = response.json()["run_id"]
    return run_id


# --- The fixture, served from the database -----------------------------------------------------


@pytest.mark.parametrize("who", CLINICIANS)
def test_a_seeded_database_reproduces_the_fixture(api: Api, who: str) -> None:
    # The fixture file still holds demo tier keys; the API no longer serves them.
    ward = api.ward(who)
    mine = set(CLINICIANS[who])
    expected = [bare(r) for r in DATA["ranked"] if r["id"] in mine]  # the fixture is in rank order
    assert [{**r, "rank": i} for i, r in enumerate(expected, 1)] == ward["ranked"]
    assert [a for a in DATA["abstained"] if a["id"] in mine] == ward["abstained"]
    assert ward["receiving_data"] is True
    assert [r["escalation_reasons"] for r in ward["ranked"]] == [
        r["escalation_reasons"] for r in expected
    ]


def ended(history: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """The fixture file gives each 15-minute bin's START; the API serves its END, as for a posted run."""

    def end(start: str) -> str:
        moment = datetime.fromisoformat(start) + timedelta(minutes=15)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {v: [{**h, "time": end(h["time"])} for h in points] for v, points in history.items()}


def test_the_fixture_detail_pages_are_reproduced(api: Api) -> None:
    for row in DATA["ranked"]:
        detail = api.client.get(f"/patients/{row['id']}", headers=api.as_("rn-manalo")).json()
        assert detail["vital_history"] == ended(DATA["details"][row["id"]]["vital_history"])
        # the last bin of each vital ends at the ward's own time
        assert {points[-1]["time"] for points in detail["vital_history"].values()} == {DATA["as_of"]}
        assert detail["tier_breakdown"] == bare(DATA["details"][row["id"]]["tier_breakdown"])


def test_notes_and_clinicians_are_seeded(api: Api) -> None:
    assert {r["id"]: r["role"] for r in api.sql("SELECT * FROM clinician")} == {
        c: e["role"] for c, e in DATA["clinicians"].items()
    }
    shown = api.client.get("/patients/p-001/notes", headers=api.as_("rn-manalo")).json()
    assert {n["id"] for n in shown} == {
        n["id"] for n in DATA["notes"] if n["patient_id"] == "p-001"
    }


def test_seeding_twice_changes_nothing(database_url: str) -> None:
    first = Api(database_url)
    before = first.sql("SELECT count(*) FROM observation")
    second = Api(database_url)  # a restart seeds again: a no-op
    assert second.sql("SELECT count(*) FROM observation") == before
    assert len(second.sql("SELECT * FROM run")) == 1


# --- Durability -------------------------------------------------------------------------------


def test_data_survives_closing_and_reopening_the_database(database_url: str) -> None:
    first = Api(database_url)
    h = first.as_("rn-manalo")
    first.client.post(
        "/patients/p-003/notes", headers=h, json={"author": "a", "role": "RN", "content": "kept"}
    )
    first.client.post("/patients/p-004/vitals", headers=h, json={"systolic": 130, "diastolic": 84})
    first.client.post("/patients/p-003/acknowledgement", headers=h, json={"score": 5})
    admitted = first.client.post(
        "/patients",
        headers=h,
        json={
            "lastName": "Kept",
            "firstName": "Pat",
            "age": 40,
            "sex": "F",
            "bed": "1A",
            "dateOfBirth": "1980-01-01",
            "admissionRoute": "transfer",
            "chiefComplaint": "c",
            "admittingContext": "a",
            "attendingPhysician": "p",
        },
    ).json()
    ward = first.ward("rn-manalo")

    second = Api(database_url)  # a new app on the same database, as after a restart
    again = second.client.get("/wards/W4/rows", headers=h)  # the old token still works
    assert again.status_code == 200
    assert again.json() == ward
    assert admitted["id"] in {a["id"] for a in ward["abstained"]}
    notes = second.client.get("/patients/p-003/notes", headers=h).json()
    assert "kept" in [n["content"] for n in notes]
    assert second.client.get("/acknowledgements", headers=h).json() == [
        {"patient_id": "p-003", "score": 5}
    ]
    assert "p-004" in {r["id"] for r in again.json()["ranked"]}


# --- Constraints held by the database itself ---------------------------------------------------


def test_the_audit_log_refuses_update_delete_and_truncate(api: Api) -> None:
    api.client.get("/patients", headers=api.as_("rn-manalo"))
    before = api.sql("SELECT * FROM audit_log")
    with connect(api.url) as conn:
        for statement in (
            "UPDATE audit_log SET outcome = 'x'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="append-only"):
                conn.execute(statement)
    assert before
    assert api.sql("SELECT * FROM audit_log") == before


@pytest.mark.parametrize(
    ("num", "txt", "reason"),
    [(None, None, None), (1.0, None, "also a reason"), (None, "A", "also a reason")],
    ids=["neither", "number-and-reason", "text-and-reason"],
)
def test_an_observation_needs_a_value_or_a_reason_never_neither_or_both(
    api: Api, num: float | None, txt: str | None, reason: str | None
) -> None:
    with connect(api.url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO observation (run_id, patient_id, code, num, txt, absent_reason,"
            " observed_at, source, quality, synthetic)"
            " VALUES (1, 'p-001', 'heart_rate', %s, %s, %s, '2026-09-03T07:00:00Z', 'device', 'ok',"
            " TRUE)",
            (num, txt, reason),
        )


@pytest.mark.parametrize(
    ("source", "synthetic"),
    [("eicu_demo", True), ("eicu_demo", False), ("synthetic", False), ("fixture", False)],
)
def test_a_run_is_a_fixture_or_synthetic_and_for_now_always_synthetic(
    api: Api, source: str, synthetic: bool
) -> None:
    with connect(api.url) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO run (source, synthetic, ward, origin, as_of, tier_status)"
            " VALUES (%s, %s, 'W4', %s, %s, '[]')",
            (source, synthetic, ORIGIN, ORIGIN),
        )


def test_the_last_observation_wins_and_a_tie_goes_to_the_newest_row(api: Api) -> None:
    h = api.as_("rn-delacruz")
    for systolic in (140, 95):  # the same ward clock: the later write is the one served
        api.client.post(
            "/patients/p-004/vitals", headers=h, json={"systolic": systolic, "diastolic": 60}
        )
    rows = api.sql(
        "SELECT num FROM observation WHERE patient_id = 'p-004' AND code = 'blood_pressure'"
        " AND source = 'nurse' AND observed_at = '2026-09-03T07:00:00Z'"
    )
    assert [r["num"] for r in rows] == [95.0]  # the re-entry replaced the row: no duplicate
    ranked = {r["id"]: r for r in api.ward("rn-delacruz")["ranked"]}
    assert ranked["p-004"]["vitals"]["blood_pressure"]["data"]["value"]["systolic"] == 95


# --- Sessions and acknowledgements -------------------------------------------------------------


def test_sign_in_issues_a_random_token_stored_hashed(api: Api) -> None:
    token = api.as_("rn-manalo")["Authorization"].removeprefix("Bearer ")
    assert token != "rn-manalo"
    with connect(api.url) as conn:
        dump = dump_database(conn)
    assert token not in dump  # only its hash is stored
    body = {"clinician_id": "rn-manalo", "passphrase": DEV_PASSPHRASE}
    assert api.client.post("/session", json={**body, "clinician_id": "nobody"}).status_code == 401
    assert api.client.post("/session", json={}).status_code == 422
    assert api.client.post("/session", json={**body, "x": 1}).status_code == 422


def test_the_clinician_id_is_not_a_bearer_token_and_tokens_expire(api: Api) -> None:
    assert (
        api.client.get("/patients", headers={"Authorization": "Bearer rn-manalo"}).status_code
        == 401
    )
    h = api.as_("rn-manalo")
    assert api.client.get("/patients", headers=h).status_code == 200
    api.wall.now += timedelta(hours=12, seconds=1)
    assert api.client.get("/patients", headers=h).status_code == 401


def test_acknowledgements_persist_and_belong_to_one_clinician(api: Api) -> None:
    mine, theirs = api.as_("rn-delacruz"), api.as_("rn-reyes")
    assert api.client.get("/acknowledgements", headers=mine).json() == []
    assert (
        api.client.post(
            "/patients/p-001/acknowledgement", headers=mine, json={"score": 9}
        ).status_code
        == 204
    )
    api.client.post("/patients/p-001/acknowledgement", headers=mine, json={"score": 7})  # upsert
    api.client.post("/patients/p-003/acknowledgement", headers=theirs, json={"score": 5})
    assert api.client.get("/acknowledgements", headers=mine).json() == [
        {"patient_id": "p-001", "score": 7}
    ]
    assert api.client.get("/acknowledgements", headers=theirs).json() == [
        {"patient_id": "p-003", "score": 5}
    ]
    assert len(api.sql("SELECT * FROM acknowledgement")) == 2


def test_acknowledging_another_clinicians_patient_is_a_404_like_a_missing_one(api: Api) -> None:
    h = api.as_("rn-reyes")
    other = api.client.post("/patients/p-001/acknowledgement", headers=h, json={"score": 9})
    missing = api.client.post("/patients/p-999/acknowledgement", headers=h, json={"score": 9})
    assert (
        (other.status_code, other.text) == (missing.status_code, missing.text) == (404, other.text)
    )
    assert api.client.get("/acknowledgements", headers=h).json() == []
    audit = api.client.get("/audit", headers=h).json()
    assert [(e["action"], e["outcome"]) for e in audit if e["action"] == "acknowledge"] == [
        ("acknowledge", "not_found"),
        ("acknowledge", "not_found"),
    ]
    assert (
        api.client.post(
            "/patients/p-003/acknowledgement", headers=h, json={"score": 99}
        ).status_code
        == 422
    )


# --- The ward simulation -----------------------------------------------------------------------


def test_the_newest_run_is_active_and_runs_are_isolated(api: Api) -> None:
    fixture_ids = {r["id"] for r in api.ward("rn-manalo")["ranked"]}
    run_id = start(api)
    ward = api.ward("rn-manalo")  # the charge nurse is assigned every patient of the new run
    assert {r["id"] for r in ward["ranked"]} == {"s-1"}
    assert fixture_ids.isdisjoint({r["id"] for r in ward["ranked"]})
    assert api.client.get("/patients/p-001", headers=api.as_("rn-manalo")).status_code == 404
    assert api.client.get("/patients/s-1", headers=api.as_("rn-delacruz")).status_code == 200
    assert (
        api.client.get("/patients/s-1", headers=api.as_("rn-reyes")).status_code == 404
    )  # not theirs
    assert api.client.get("/patients/s-1", headers=api.as_("md-santos")).status_code == 200
    assert api.client.get("/patients/s-1", headers=api.as_("md-aquino")).status_code == 404
    assert run_id == 2
    assert start(api) == 3  # a third run takes over; the others are untouched
    assert len(api.sql("SELECT * FROM patient WHERE run_id = 1")) == len(DATA["ranked"]) + len(
        DATA["unscored"]
    )


def test_a_sim_run_is_scored_computed_and_timed_on_the_wall_clock(api: Api) -> None:
    start(api)
    row = api.ward("rn-delacruz")["ranked"][0]
    assert (row["id"], row["score"], row["rank"]) == ("s-1", 10, 1)
    assert row["mechanism"] == "Sorted on NEWS2 (Tier A). Live from the ward simulation."
    assert row["supporting"] == "RR 24 /min, SpO2 93 %, SBP 88, HR 118 bpm, Temp 38.5 °C"
    assert row["tier_status"] == [{"tier": "A", "label": "NEWS2", "live": True}]
    assert row["synthetic"] is True
    # Sim minute 0 is now: a reading from minute -2 is two minutes old.
    hr = row["vitals"]["heart_rate"]["provenance"]
    assert hr["observed_at"] == iso(NOW - timedelta(minutes=2))
    assert row["vitals"]["blood_pressure"]["provenance"]["observed_at"] == iso(
        NOW - timedelta(minutes=10)
    )
    assert row["last_update_s"] == 120
    api.wall.now += timedelta(seconds=20)
    assert api.ward("rn-delacruz")["ranked"][0]["last_update_s"] == 140  # measured from wall now
    # Gaps are null, never zero; the sparkline is the last bins.
    assert row["vital_trends"]["heart_rate"] == [55.0, 70.0, None]
    detail = api.client.get("/patients/s-1", headers=api.as_("rn-delacruz")).json()
    history = detail["vital_history"]["heart_rate"]
    assert [p["value"] for p in history] == [55.0, 70.0, None]
    assert history[-1]["time"] == iso(NOW - timedelta(minutes=15))


def test_the_ward_and_the_patient_carry_the_ward_time_they_were_computed_at(api: Api) -> None:
    seeded = api.ward("rn-manalo")["now"]
    assert seeded == "2026-09-03T07:00:00Z"  # the seeded run's own as_of, not the wall clock
    detail = api.client.get("/patients/p-001", headers=api.as_("rn-manalo")).json()
    assert detail["now"] == seeded
    start(api)
    api.wall.now += timedelta(seconds=20)
    h = api.as_("rn-delacruz")
    assert api.ward("rn-delacruz")["now"] == iso(api.wall.now)  # a posted run: the wall clock
    assert api.client.get("/patients/s-1", headers=h).json()["now"] == iso(api.wall.now)


def test_a_batch_moves_the_run_forward_in_one_transaction(api: Api) -> None:
    run_id = start(api)
    api.wall.now += timedelta(minutes=10)
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=10)).status_code == 204
    row = api.ward("rn-delacruz")["ranked"][0]
    assert row["trend"] == "stable" and row["signal_quality"] == 90
    # The new reading, at sim minute 10, is "now"; the old one at minute -2 is still 12 minutes old.
    assert row["vitals"]["heart_rate"]["data"]["value"] == 120
    assert row["vitals"]["heart_rate"]["provenance"]["observed_at"] == iso(api.wall.now)
    assert row["vitals"]["respiratory_rate"]["provenance"]["observed_at"] == iso(
        api.wall.now - timedelta(minutes=12)
    )
    (run,) = api.sql("SELECT * FROM run WHERE id = %s", run_id)
    assert run["as_of"] == datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    assert run["last_batch_at"] == api.wall.now
    assert (run["received"], run["rejected"], run["abstained"], run["ended"]) == (5, 1, 2, False)


def test_a_repeated_batch_adds_no_rows(api: Api) -> None:
    run_id = start(api)
    counts = (
        "SELECT (SELECT count(*) FROM observation) AS observations,"
        " (SELECT count(*) FROM vital_bin) AS bins, (SELECT count(*) FROM sim_event) AS events"
    )
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=3))
    (once,) = api.sql(counts)
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=3)).status_code == 204
    assert api.sql(counts) == [once]
    assert once["events"] == 1


def test_a_batch_into_a_new_sim_hour_prunes_what_is_older_than_the_retention(api: Api) -> None:
    old_run = start(api)
    kept = (
        "SELECT (SELECT count(*) FROM observation WHERE run_id = %s) AS observations,"
        " (SELECT count(*) FROM vital_bin WHERE run_id = %s) AS bins"
    )
    (other,) = api.sql(kept, old_run, old_run)
    run_id = start(api)  # the same client restarts: the first run's rows are as old, and not its
    week = 7 * 24 * 60
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=week + 30)).status_code == 204
    (before,) = api.sql(kept, run_id, run_id)
    # Still inside the hour the last batch reached: nothing is pruned, however old.
    late = batch(
        minute=week + 59,
        observations=[obs("s-1", "heart_rate", m, num=120) for m in (10, week + 59)],
        bins=[
            {"patient_id": "s-1", "vital": "heart_rate", "minute": m, "value": 120.0}
            for m in (10, week + 59)
        ],
    )
    assert api.post(f"/sim/runs/{run_id}/batch", late).status_code == 204
    (same_hour,) = api.sql(kept, run_id, run_id)
    assert same_hour == {"observations": before["observations"] + 2, "bins": before["bins"] + 2}

    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=week + 60)).status_code == 204
    cutoff = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # the new as_of less seven days
    bins = api.sql("SELECT bin_at FROM vital_bin WHERE run_id = %s ORDER BY bin_at", run_id)
    newest = [cutoff + timedelta(days=7, minutes=m) for m in (-30, -1, 0)]
    assert [b["bin_at"] for b in bins] == newest
    left = api.sql(
        "SELECT code FROM observation WHERE run_id = %s AND observed_at < %s", run_id, cutoff
    )
    # The old heart rates were superseded and are gone; every other old reading is the newest of
    # its code, so it stays and is still what the patient read serves.
    assert {o["code"] for o in left} == {
        "respiratory_rate",
        "spo2",
        "temperature",
        "blood_pressure",
        "consciousness",
        "supplemental_o2",
        "spo2_scale",
    }
    assert len(left) == 7
    vitals = api.ward("rn-delacruz")["ranked"][0]["vitals"]
    assert vitals["heart_rate"]["data"]["value"] == 120
    assert vitals["blood_pressure"]["data"]["value"] == {"systolic": 88, "diastolic": 52}
    assert vitals["blood_pressure"]["provenance"]["observed_at"] == iso(
        api.wall.now - timedelta(minutes=week + 70)
    )
    assert api.sql(kept, old_run, old_run) == [other]  # another run is never pruned


def test_the_run_decides_synthetic_not_the_client(api: Api) -> None:
    start(api)
    rows = api.sql("SELECT DISTINCT synthetic FROM observation WHERE run_id = 2")
    assert [r["synthetic"] for r in rows] == [True]
    body = batch(minute=1)
    body["observations"][0]["synthetic"] = False
    assert api.post("/sim/runs/2/batch", body).status_code == 422  # not a field the client may send


@pytest.mark.parametrize("key", [None, "wrong", KEY[:-1], KEY + "x"])
def test_ingest_refuses_a_missing_or_wrong_key_and_writes_nothing(
    api: Api, key: str | None
) -> None:
    assert api.post("/sim/runs", new_run(), key=key).status_code == 403
    assert api.post("/sim/runs/1/batch", batch(), key=key).status_code == 403
    assert api.post("/sim/lab/claim", None, key=key).status_code == 403
    lab_done = {"state": "failed", "reason": "x"}
    assert api.post("/sim/lab/runs/1/report", lab_done, key=key).status_code == 403
    assert len(api.sql("SELECT * FROM run")) == 1
    assert api.sql("SELECT * FROM lab_run") == []


def test_ingest_fails_closed_when_no_key_is_configured(database_url: str) -> None:
    api = Api(database_url, ingest_key=None)
    for key in (None, "", "anything"):
        assert api.post("/sim/runs", new_run(), key=key).status_code == 503
        assert api.post("/sim/runs/1/batch", batch(), key=key).status_code == 503
        assert api.post("/sim/lab/claim", None, key=key).status_code == 503
        lab_done = {"state": "failed", "reason": "x"}
        assert api.post("/sim/lab/runs/1/report", lab_done, key=key).status_code == 503
    assert len(api.sql("SELECT * FROM run")) == 1
    assert api.sql("SELECT * FROM lab_run") == []


def test_ingest_refuses_an_unknown_clinician_and_writes_nothing(api: Api) -> None:
    for patients in (
        [sim_patient(rn="rn-ghost")],
        [sim_patient(), sim_patient("s-2", md="md-ghost")],
    ):
        assert api.post("/sim/runs", new_run(patients=patients)).status_code == 422
    assert len(api.sql("SELECT * FROM run")) == 1  # a rejected body writes nothing
    assert len(api.sql("SELECT * FROM patient WHERE run_id > 1")) == 0
    assert start(api, patients=[sim_patient(md=None)]) == 2  # md may be null


def test_ingest_refuses_a_malformed_body(api: Api) -> None:
    broken = sim_patient()
    broken["observations"][0].pop("num")  # neither a value nor a reason
    for body in (
        new_run(patients=[broken]),
        {**new_run(), "source": "fixture"},
        {**new_run(), "source": "eicu_demo"},  # the replay is gone
        {**new_run(), "patients": []},
        {**new_run(), "extra": 1},
    ):
        assert api.post("/sim/runs", body).status_code == 422
    assert len(api.sql("SELECT * FROM run")) == 1


def test_ingest_caps_list_sizes(api: Api) -> None:
    many = new_run()
    many["patients"][0]["bins"] = [
        {"patient_id": "s-1", "vital": "spo2", "minute": m, "value": None} for m in range(20_001)
    ]
    assert api.post("/sim/runs", many).status_code == 422


def test_a_patient_outside_the_run_is_refused_and_the_whole_batch_writes_nothing(api: Api) -> None:
    run_id = start(api)
    body = batch(minute=5)
    body["bins"].append({"patient_id": "p-001", "vital": "spo2", "minute": 5, "value": 90.0})
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 422
    (run,) = api.sql("SELECT * FROM run WHERE id = %s", run_id)
    assert run["as_of"] == datetime(2026, 1, 1, tzinfo=UTC)  # not moved
    assert (
        api.sql("SELECT * FROM observation WHERE run_id = %s AND observed_at > %s", run_id, ORIGIN)
        == []
    )
    assert api.post("/sim/runs/99/batch", batch()).status_code == 404


def test_receiving_data_goes_false_for_a_stale_or_ended_run(api: Api) -> None:
    run_id = start(api)
    assert api.ward("rn-delacruz")["receiving_data"] is True
    api.wall.now += timedelta(seconds=30)
    assert api.ward("rn-delacruz")["receiving_data"] is True
    api.wall.now += timedelta(seconds=1)  # the last batch is more than 30 seconds old
    assert api.ward("rn-delacruz")["receiving_data"] is False
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=1))
    assert api.ward("rn-delacruz")["receiving_data"] is True
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=2, ended=True))
    assert api.ward("rn-delacruz")["receiving_data"] is False


RUN_TABLES = (
    "patient",
    "care_assignment",
    "observation",
    "vital_bin",
    "clinical_note",
    "acknowledgement",
    "sim_event",
    "clinician_action",
    "sim_command",
)


def test_a_deleted_runs_id_is_never_reused_and_foreign_keys_always_cascade(api: Api) -> None:
    run_id = start(api, patients=[sim_patient("orphan")])
    event = {"minute": 1, "kind": "call", "patient_id": None, "staff_id": None, "text": "t"}
    body = batch(minute=1, patients=[], observations=[], bins=[], events=[event])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    h = api.as_("rn-delacruz")
    api.client.post(
        "/patients/orphan/notes", headers=h, json={"author": "a", "role": "RN", "content": "c"}
    )
    api.client.post("/patients/orphan/acknowledgement", headers=h, json={"score": 3})
    for body in ({"kind": "reset"}, {"kind": "detach_patch", "patient_id": "orphan"}):
        command(api, "rn-manalo", **body)  # one that names no patient, one that does
    for table in RUN_TABLES:  # the run has rows everywhere, so the cascade below is not vacuous
        assert api.sql(f"SELECT * FROM {table} WHERE run_id = %s", run_id), table
    with connect(api.url) as conn:  # a raw delete: there is no way to leave the run's rows behind
        conn.execute("DELETE FROM run WHERE id = %s", (run_id,))
    for table in RUN_TABLES:
        assert api.sql(f"SELECT * FROM {table} WHERE run_id = %s", run_id) == [], table
    new_id = start(api)
    assert new_id > run_id  # an identity is never handed out twice
    assert {r["id"] for r in api.ward("rn-manalo")["ranked"]} == {"s-1"}  # not the old run's


def test_the_audit_log_refuses_overwriting_a_row_but_still_appends(api: Api) -> None:
    headers = api.as_("rn-manalo")
    api.client.get("/patients", headers=headers)
    first, second = api.sql("SELECT * FROM audit_log")  # the sign-in, then the view
    with connect(api.url) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):  # the primary key refuses the seq
            conn.execute(
                "INSERT INTO audit_log (seq, at, action, resource, outcome)"
                " OVERRIDING SYSTEM VALUE VALUES (%s, now(), 'forged', '-', 'ok')",
                (first["seq"],),
            )
        with pytest.raises(psycopg.errors.GeneratedAlways):  # and a seq cannot be chosen at all
            conn.execute(
                "INSERT INTO audit_log (seq, at, action, resource, outcome)"
                " VALUES (999, now(), 'forged', '-', 'ok')"
            )
    assert api.sql("SELECT * FROM audit_log") == [first, second]
    api.client.get("/patients", headers=headers)  # ordinary appends still work
    assert len(api.sql("SELECT * FROM audit_log")) == 3


def test_a_rejected_pin_is_not_echoed_back(api: Api) -> None:
    pin = "not-a-pin-7731"
    form = {
        "lastName": "X",
        "firstName": "Y",
        "dateOfBirth": "1970-01-01",
        "age": 50,
        "sex": "F",
        "bed": "1A",
        "admissionRoute": "transfer",
        "chiefComplaint": "c",
        "admittingContext": "a",
        "attendingPhysician": "p",
        "philhealthPin": pin,
    }
    response = api.client.post("/patients", headers=api.as_("rn-delacruz"), json=form)
    assert response.status_code == 422
    assert pin not in response.text
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "philhealthPin"]
    assert {"loc", "msg", "type"} <= set(error)
    assert "input" not in error
    assert pin not in json.dumps(api.sql("SELECT * FROM audit_log"), default=str)


def test_a_long_resource_is_cut_in_the_audit_log(api: Api) -> None:
    api.client.get("/patients/" + "p" * 3000, headers=api.as_("rn-delacruz"))
    (row,) = api.sql("SELECT resource, outcome FROM audit_log WHERE action = 'view_patient'")
    assert (len(row["resource"]), row["outcome"]) == (200, "not_found")


def test_the_audit_trail_holds_no_sim_patient_data(api: Api) -> None:
    start(api)
    api.ward("rn-delacruz")
    api.client.get("/patients/s-1", headers=api.as_("rn-delacruz"))
    dump = json.dumps(api.sql("SELECT * FROM audit_log"), default=str)
    for secret in ("Sim, Pat", "118", "a story", "9A"):
        assert secret not in dump


# --- Exact numbers and the time format ---------------------------------------------------------


def test_clinical_values_round_trip_exactly(api: Api) -> None:
    patient = sim_patient()
    patient["observations"] += [obs("s-1", "heart_rate", -1, num=118.3)]
    patient["observations"] += [obs("s-1", "temperature", -1, num=36.7)]
    run_id = start(api, patients=[patient])
    stored = {
        (r["code"], r["num"])
        for r in api.sql("SELECT code, num FROM observation WHERE run_id = %s", run_id)
    }
    assert {("heart_rate", 118.3), ("temperature", 36.7)} <= stored  # a 4-byte float would not be
    row = api.ward("rn-delacruz")["ranked"][0]
    assert row["vitals"]["heart_rate"]["data"]["value"] == 118.3
    assert row["vitals"]["temperature"]["data"]["value"] == 36.7
    h = api.as_("rn-delacruz")
    panel = api.client.post("/patients/s-1/labs", headers=h, json={"potassium": 4.1}).json()
    assert panel["potassium"]["data"]["value"] == 4.1


ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def served_times(node: Any) -> list[str]:
    """Every string in a response that looks like a date-time."""
    if isinstance(node, str):
        return [node] if re.match(r"^\d{4}-\d\d-\d\dT", node) else []
    if isinstance(node, list):
        return [t for item in node for t in served_times(item)]
    if isinstance(node, dict):
        return [t for item in node.values() for t in served_times(item)]
    return []


def test_served_times_are_whole_seconds_with_a_z_suffix_even_after_shifting(api: Api) -> None:
    api.wall.now = NOW + timedelta(microseconds=654_321)  # a real clock is never on the second
    fixture_times = served_times(api.ward("rn-manalo"))  # a fixture run, served as stored
    run_id = start(api)
    api.wall.now += timedelta(minutes=7, microseconds=111_111)
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=7)).status_code == 204
    h = api.as_("rn-delacruz")
    note = {"author": "a", "role": "RN", "content": "c"}
    api.client.post("/patients/s-1/notes", headers=h, json=note)
    api.client.post("/patients/s-1/labs", headers=h, json={"potassium": 4.1})
    seen = served_times(api.ward("rn-delacruz"))  # a sim run, shifted onto the wall clock
    seen += [api.client.get("/wards/W4/live", headers=h).json()["now"]]  # the ward time too
    seen += served_times(api.client.get("/patients/s-1", headers=h).json())
    seen += served_times(api.client.get("/patients/s-1/notes", headers=h).json())
    seen += served_times(api.client.get("/audit", headers=h).json())
    assert len(fixture_times) > 20 and len(seen) > 20  # the walk is not vacuous
    assert [t for t in fixture_times + seen if not ISO_Z.match(t)] == []


# --- Writers that compute max + 1 take turns ---------------------------------------------------
# Real threads, each request on its own connection, all released at once. Each of these fails
# (a duplicate key, so a 500) when its writer does not hold the run's advisory lock.

THREADS = 8
ROUNDS = 6


def test_simultaneous_notes_get_distinct_ids(api: Api) -> None:
    h = api.as_("rn-manalo")
    before = len(api.sql("SELECT id FROM clinical_note"))
    ids: list[str] = []
    for _ in range(ROUNDS):
        sent = together(
            THREADS,
            lambda i: api.client.post(
                "/patients/p-001/notes",
                headers=h,
                json={"author": "a", "role": "RN", "content": f"note {i}"},
            ),
        )
        assert [r.status_code for r in sent] == [201] * THREADS
        ids += [r.json()["id"] for r in sent]
    assert len(set(ids)) == len(ids) == THREADS * ROUNDS
    assert len(api.sql("SELECT id FROM clinical_note")) == before + len(ids)


def test_simultaneous_admissions_get_distinct_patient_ids(api: Api) -> None:
    form = {
        "lastName": "Cruz", "firstName": "Pat", "dateOfBirth": "1980-01-01", "age": 44,
        "sex": "F", "bed": "1A", "admissionRoute": "transfer", "chiefComplaint": "c",
        "admittingContext": "a", "attendingPhysician": "p",
    }  # fmt: skip
    h = api.as_("rn-delacruz")
    ids: list[str] = []
    for round_ in range(ROUNDS):

        def admit(i: int, round_: int = round_) -> Any:  # a bed each: one bed takes one patient
            body = {**form, "bed": f"{round_}-{i}"}
            return api.client.post("/patients", headers=h, json=body)

        sent = together(THREADS, admit)
        assert [r.status_code for r in sent] == [201] * THREADS
        ids += [r.json()["id"] for r in sent]
    assert len(set(ids)) == len(ids) == THREADS * ROUNDS
    numbers = sorted(int(i.removeprefix("p-")) for i in ids)
    assert numbers == list(range(numbers[0], numbers[0] + len(ids)))  # no id skipped or repeated


def test_simultaneous_admissions_to_one_bed_admit_one_patient(api: Api) -> None:
    form = {
        "lastName": "Cruz", "firstName": "Pat", "dateOfBirth": "1980-01-01", "age": 44,
        "sex": "F", "bed": "1A", "admissionRoute": "transfer", "chiefComplaint": "c",
        "admittingContext": "a", "attendingPhysician": "p",
    }  # fmt: skip
    h = api.as_("rn-delacruz")
    sent = together(THREADS, lambda i: api.client.post("/patients", headers=h, json=form))
    assert sorted(r.status_code for r in sent) == [201] + [409] * (THREADS - 1)


def test_simultaneous_batches_on_one_run_give_each_event_its_own_seq(api: Api) -> None:
    run_id = start(api)
    minutes = 0
    for round_ in range(ROUNDS):

        def send(i: int, round_: int = round_) -> Any:
            minute = round_ * THREADS + i + 1
            event = {"minute": minute, "kind": "call", "patient_id": "s-1", "staff_id": None}
            # No patient row is updated, so nothing but the run's lock makes the batches take turns.
            body = batch(minute=minute, patients=[], events=[{**event, "text": f"e{minute}"}])
            return api.post(f"/sim/runs/{run_id}/batch", body)

        assert [r.status_code for r in together(THREADS, send)] == [204] * THREADS
        minutes += THREADS
    rows = api.sql("SELECT seq FROM sim_event WHERE run_id = %s ORDER BY seq", run_id)
    assert [r["seq"] for r in rows] == list(range(1, minutes + 1))  # every event kept, none shared
    (run,) = api.sql("SELECT as_of FROM run WHERE id = %s", run_id)
    assert run["as_of"] in {
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=m) for m in range(1, minutes + 1)
    }  # the run row holds one whole batch's time


def test_two_processes_starting_at_once_set_up_the_database_once(blank_database_url: str) -> None:
    together(4, lambda i: ensure(blank_database_url))  # four app starts on an empty database
    with connect(blank_database_url) as conn:
        runs = conn.execute("SELECT count(*) AS n FROM run WHERE source = 'fixture'").fetchone()
        clinicians = conn.execute("SELECT count(*) AS n FROM clinician").fetchone()
    assert runs == {"n": 1}
    assert clinicians == {"n": len(CLINICIANS)}


# --- Boundaries: input PostgreSQL cannot store, and where a reset may run -----------------------


def test_a_nul_in_text_is_refused_not_a_500_and_nothing_with_it_is_written(api: Api) -> None:
    h = api.as_("rn-manalo")
    notes = len(api.sql("SELECT id FROM clinical_note"))
    note = {"author": "a", "role": "RN", "content": "bad\x00byte"}  # a body field
    assert api.client.post("/patients/p-001/notes", headers=h, json=note).status_code == 422
    assert len(api.sql("SELECT id FROM clinical_note")) == notes
    sim = new_run(patients=[{**sim_patient(), "name": "Sim\x00"}])  # a sim field
    assert api.post("/sim/runs", sim).status_code == 422
    assert len(api.sql("SELECT id FROM run")) == 1
    response = api.client.get("/patients/p%00x/notes", headers=h)  # a path parameter
    assert response.status_code == 404
    audited = api.sql("SELECT resource FROM audit_log WHERE action = 'request'")
    assert audited == [{"resource": "px"}]  # audited, with the NUL dropped


def test_times_are_utc_even_when_the_environment_sets_a_time_zone(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGTZ", "Asia/Manila")  # libpq reads it on every connect
    with connect(api.url) as conn:
        assert conn.execute("SHOW TimeZone").fetchone() == {"TimeZone": "UTC"}
    seen = served_times(api.ward("rn-manalo"))
    seen += [e["at"] for e in api.client.get("/audit", headers=api.as_("rn-manalo")).json()]
    assert len(seen) > 20 and [t for t in seen if not ISO_Z.match(t)] == []


def test_reset_refuses_when_the_connection_made_is_not_to_loopback_and_drops_nothing(
    database_url: str, spoofed_server_address: None
) -> None:
    # The URL says 127.0.0.1; the connection libpq made went elsewhere: judged on the connection.
    with pytest.raises(SystemExit, match="loopback"):
        reset(database_url)
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM run").fetchone() == {"n": 1}
        assert conn.execute("SELECT count(*) AS n FROM clinician").fetchone() == {"n": 5}


def test_reset_on_loopback_rebuilds_from_the_seed(database_url: str) -> None:
    api = Api(database_url)
    api.client.get("/patients", headers=api.as_("rn-manalo"))
    assert api.sql("SELECT seq FROM audit_log")
    reset(database_url)  # a throwaway copy, not the configured database
    assert api.sql("SELECT seq FROM audit_log") == []
    assert [r["id"] for r in api.sql("SELECT id FROM run")] == [1]


def test_numbers_and_dates_outside_what_the_database_holds_are_422(api: Api) -> None:
    for change in (
        {"seed": 2**63},
        {"origin": "0001-01-01T00:00:00+14:00"},
        {"origin": "1999-12-31T23:59:59Z"},
        {"origin": "2101-01-01T00:00:00Z"},
    ):
        assert api.post("/sim/runs", {**new_run(), **change}).status_code == 422, change
    assert len(api.sql("SELECT id FROM run")) == 1
    for field in ("received", "rejected", "abstained"):
        body = batch()
        body["counts"][field] = 2**63
        assert api.post("/sim/runs/1/batch", body).status_code == 422, field
    edge = {"seed": 2**63 - 1, "origin": "2000-01-01T00:00:00Z"}  # the limits themselves are fine
    assert api.post("/sim/runs", {**new_run(), **edge}).status_code == 201
    body = batch()
    body["counts"] = {"received": 2**63 - 1, "rejected": 0, "abstained": 0}
    assert api.post("/sim/runs/2/batch", body).status_code == 204


# --- Edges: a database with no run, a connection that cannot be set up, what was never recorded --


def test_a_database_with_no_run_fails_the_request_and_closes_its_connection(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biosentry.api import app as app_module

    opened: list[psycopg.Connection[Any]] = []

    def tracked(url: str, production: bool | None = None) -> psycopg.Connection[Any]:
        opened.append(connect(url, production))
        return opened[-1]

    headers = api.as_("rn-delacruz")
    with connect(api.url) as conn:
        conn.execute("DELETE FROM run")
    monkeypatch.setattr(app_module, "connect", tracked)
    with pytest.raises(RuntimeError, match="the database has no run"):
        api.client.get("/wards/W4/rows", headers=headers)
    assert [c.closed for c in opened] == [True]


def test_a_connection_that_cannot_be_set_to_utc_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from biosentry.repositories import postgres

    class Refusing:
        closed = False

        def execute(self, *args: Any) -> None:
            raise psycopg.OperationalError("SET TIME ZONE refused")

        def close(self) -> None:
            self.closed = True

    made = Refusing()
    monkeypatch.setattr(postgres.psycopg, "connect", lambda *a, **k: made)
    with pytest.raises(psycopg.OperationalError, match="SET TIME ZONE refused"):
        connect("postgresql://unused")
    assert made.closed


def test_an_unknown_patient_reads_as_none_and_a_never_recorded_input_is_missing(api: Api) -> None:
    from biosentry.repositories.postgres import PostgresStore

    patient = sim_patient()
    patient["observations"] = [o for o in patient["observations"] if o["code"] != "heart_rate"]
    patient["observations"] = [o for o in patient["observations"] if o["code"] != "consciousness"]
    start(api, patients=[patient])
    with connect(api.url) as conn:
        store = PostgresStore(conn, api.wall)
        assert store.read("nobody") is None
        state = store.read("s-1")
    assert state is not None
    for code, source in (("heart_rate", "device"), ("consciousness", "nurse")):
        m = getattr(state.vitals, code)
        assert m.data.present is False and m.data.reason == "not_recorded"
        assert (m.provenance.source.value, m.provenance.quality.value) == (source, "missing")
    (row,) = api.ward("rn-delacruz")["abstained"]  # never imputed: NOT RANKED, naming both
    assert "Heart rate missing" in row["reason"] and "Level of consciousness missing" in row["reason"]


# --- A nurse-typed device value: valid for 12 hours, never erased by an absent device row -------

FOUR = {"heart_rate": 118, "respiratory_rate": 24, "spo2": 93, "temperature": 38.5}
HOUR12 = timedelta(hours=12)


def hr_of(api: Api) -> Any:
    """The stored heart rate measurement as the services read it."""
    from biosentry.repositories.postgres import PostgresStore

    with connect(api.url) as conn:
        state = PostgresStore(conn, api.wall).read("s-1")
    assert state is not None
    return state.vitals.heart_rate


def record_hr(api: Api, value: int = 100) -> None:
    post = api.client.post(
        "/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json={"heart_rate": value}
    )
    assert post.status_code == 200


def at(api: Api, minute: int) -> None:
    api.wall.now = NOW + timedelta(minutes=minute)
    api.tokens.clear()  # a session lasts as long as a nurse value: sign in again


def test_a_nurse_value_ranks_a_patient_with_no_patch_for_twelve_hours(api: Api) -> None:
    patient = sim_patient()
    patient["observations"] = [o for o in patient["observations"] if o["source"] == "nurse"]
    patient["bins"] = []
    start(api, patients=[patient])
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert row["reason"] == "No device data"
    post = api.client.post("/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json=FOUR)
    assert post.status_code == 200 and post.json()["ranked"] is True
    ranked = api.ward("rn-delacruz")["ranked"][0]
    assert ranked["last_update_s"] is None
    api.wall.now += HOUR12
    api.tokens.clear()
    assert [r["id"] for r in api.ward("rn-delacruz")["ranked"]] == ["s-1"]
    api.wall.now += timedelta(seconds=1)
    api.tokens.clear()
    ward = api.ward("rn-delacruz")
    assert ward["ranked"] == []
    (row,) = ward["abstained"]
    assert row["reason"] == (
        "No device data; Heart rate, Respiratory rate, SpO2, Temperature out of date"
    )
    assert row["instruction"] == "Apply the patch and verify the signal, or enter the observations"


def test_one_expired_nurse_value_is_named_and_the_others_are_not(api: Api) -> None:
    patient = sim_patient()
    patient["observations"] = [o for o in patient["observations"] if o["code"] != "heart_rate"]
    start(api, patients=[patient])
    record_hr(api)
    api.wall.now += HOUR12 + timedelta(seconds=1)
    api.tokens.clear()
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert row["reason"] == "Heart rate out of date"
    assert row["instruction"].endswith(", or enter the observations")
    m = hr_of(api)
    assert (m.data.reason, m.provenance.source.value) == ("out_of_date", "nurse")


def test_a_newer_device_value_beats_an_older_nurse_value(api: Api) -> None:
    run_id = start(api)
    record_hr(api)  # newer than the device row at minute -2
    assert hr_of(api).data.value == 100
    at(api, 5)
    batch_5 = batch(minute=5, observations=[obs("s-1", "heart_rate", 5, num=125)])
    assert api.post(f"/sim/runs/{run_id}/batch", batch_5).status_code == 204
    assert hr_of(api).data.value == 125


def test_a_patch_that_has_sampled_since_beats_an_older_nurse_value(api: Api) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)  # the patch sampled again, its unchanged heart rate sends no row
    empty = batch(minute=5, observations=[])
    assert api.post(f"/sim/runs/{run_id}/batch", empty).status_code == 204
    assert hr_of(api).data.value == 118


def test_an_absent_device_row_does_not_erase_a_valid_nurse_value(api: Api) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)
    gone = obs("s-1", "heart_rate", 5, absent_reason="No usable reading yet")
    patient = {"id": "s-1", "trend": "stable", "signal_quality": 0, "last_sample_minute": -2}
    body = batch(minute=5, observations=[gone], patients=[patient])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert hr_of(api).data.value == 100


def test_a_patch_that_samples_other_vitals_does_not_end_a_nurse_value_for_a_lost_one(
    api: Api,
) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)  # heart rate is lost, the patch still samples the rest at minute 5
    gone = obs("s-1", "heart_rate", 5, absent_reason="No usable reading for 5 min")
    assert api.post(
        f"/sim/runs/{run_id}/batch", batch(minute=5, observations=[gone])
    ).status_code == 204
    assert hr_of(api).data.value == 100


def test_an_expired_nurse_value_under_a_newer_device_value_serves_the_device_value(
    api: Api,
) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)
    batch_5 = batch(minute=5, observations=[obs("s-1", "heart_rate", 5, num=125)])
    assert api.post(f"/sim/runs/{run_id}/batch", batch_5).status_code == 204
    expire(api)
    assert hr_of(api).data.value == 125


def test_an_expired_nurse_value_does_not_fall_back_to_an_older_device_value(api: Api) -> None:
    start(api)
    record_hr(api)  # newer than the device row at minute -2; the patch does not sample again
    api.wall.now += HOUR12 + timedelta(seconds=1)
    m = hr_of(api)
    assert (m.data.reason, m.provenance.source.value) == ("out_of_date", "nurse")


def test_an_expired_nurse_value_under_a_patch_that_still_samples_serves_the_device_value(
    api: Api,
) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 13 * 60)  # the patch sampled again, its unchanged heart rate sends no row
    empty = batch(minute=13 * 60, observations=[])
    assert api.post(f"/sim/runs/{run_id}/batch", empty).status_code == 204
    assert hr_of(api).data.value == 118


def test_a_nurse_value_a_newer_device_value_replaced_does_not_come_back(api: Api) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)
    batch_5 = batch(minute=5, observations=[obs("s-1", "heart_rate", 5, num=130)])
    assert api.post(f"/sim/runs/{run_id}/batch", batch_5).status_code == 204
    assert hr_of(api).data.value == 130
    at(api, 10)  # the patch drops out: the older nurse value, still inside 12 hours, stays replaced
    gone = obs("s-1", "heart_rate", 10, absent_reason="No usable reading for 5 min")
    patient = {"id": "s-1", "trend": "stable", "signal_quality": 0, "last_sample_minute": 5}
    body = batch(minute=10, observations=[gone], patients=[patient])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    m = hr_of(api)
    assert (m.data.reason, m.provenance.source.value) == ("No usable reading for 5 min", "device")


def test_an_expired_nurse_value_then_a_newer_absent_device_row_serves_the_device_reason(
    api: Api,
) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 13 * 60)
    gone = obs("s-1", "heart_rate", 13 * 60, absent_reason="No usable reading for 5 min")
    patient = {"id": "s-1", "trend": "stable", "signal_quality": 0, "last_sample_minute": -2}
    body = batch(minute=13 * 60, observations=[gone], patients=[patient])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    m = hr_of(api)
    assert (m.data.reason, m.provenance.source.value) == ("No usable reading for 5 min", "device")


def vital_of(api: Api, code: str) -> Any:
    from biosentry.repositories.postgres import PostgresStore

    with connect(api.url) as conn:
        state = PostgresStore(conn, api.wall).read("s-1")
    assert state is not None
    return getattr(state.vitals, code)


def drop(patient: dict, *codes: str) -> None:
    patient["observations"] = [o for o in patient["observations"] if o["code"] not in codes]


def expire(api: Api) -> None:
    api.wall.now += HOUR12 + timedelta(seconds=1)
    api.tokens.clear()


def test_an_out_of_date_measurement_is_missing_nurse_and_keeps_the_entry_time(api: Api) -> None:
    patient = sim_patient()
    drop(patient, "heart_rate")
    start(api, patients=[patient])
    record_hr(api)
    entered = hr_of(api).provenance.observed_at
    expire(api)
    m = hr_of(api)
    assert m.provenance.quality.value == "missing"
    assert m.provenance.source.value == "nurse"
    assert m.provenance.observed_at == entered


def test_an_expired_heart_rate_beside_a_missing_acvpu_names_both(api: Api) -> None:
    patient = sim_patient()
    drop(patient, "heart_rate", "consciousness")
    start(api, patients=[patient])
    record_hr(api)
    expire(api)
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert row["reason"] == "Heart rate out of date, Level of consciousness missing"


def test_an_expired_spo2_beside_a_missing_scale_names_both_and_a_missing_scale_alone_is_not_old(
    api: Api,
) -> None:
    patient = sim_patient()
    drop(patient, "spo2", "spo2_scale")
    start(api, patients=[patient])
    h = api.as_("rn-delacruz")
    post = api.client.post("/patients/s-1/vitals", headers=h, json={"spo2": 93})
    assert post.status_code == 200
    expire(api)
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert row["reason"] == "SpO2 out of date, SpO2 scale missing"


def test_a_missing_spo2_scale_alone_is_not_called_out_of_date(api: Api) -> None:
    patient = sim_patient()
    drop(patient, "spo2_scale")
    start(api, patients=[patient])
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert "SpO2 scale missing" in row["reason"]
    assert "out of date" not in row["reason"]


def test_two_expired_and_two_never_recorded_with_no_patch_name_only_the_expired(api: Api) -> None:
    patient = sim_patient()
    patient["observations"] = [o for o in patient["observations"] if o["source"] == "nurse"]
    patient["bins"] = []
    start(api, patients=[patient])
    body = {"heart_rate": 118, "temperature": 38.5}
    post = api.client.post("/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json=body)
    assert post.status_code == 200
    expire(api)
    (row,) = api.ward("rn-delacruz")["abstained"]
    assert row["reason"] == "No device data; Heart rate, Temperature out of date"


def test_a_newer_nurse_absent_row_does_not_hide_a_valid_nurse_value(api: Api) -> None:
    run_id = start(api)
    record_hr(api)
    at(api, 5)
    gone = obs("s-1", "heart_rate", 5, "nurse", absent_reason="Entered in error")
    sent = api.post(f"/sim/runs/{run_id}/batch", batch(minute=5, observations=[gone]))
    assert sent.status_code == 204
    m = hr_of(api)
    assert m.data.value == 100
    assert m.provenance.source.value == "nurse"


def test_a_newer_nurse_absent_blood_pressure_row_serves_absent(api: Api) -> None:
    run_id = start(api)
    at(api, 5)
    gone = obs("s-1", "blood_pressure", 5, "nurse", absent_reason="Cuff refused")
    sent = api.post(f"/sim/runs/{run_id}/batch", batch(minute=5, observations=[gone]))
    assert sent.status_code == 204
    m = vital_of(api, "blood_pressure")
    assert (m.data.present, m.data.reason) == (False, "Cuff refused")


@pytest.mark.parametrize("nurse_first", [True, False])
def test_two_blood_pressure_rows_with_one_time_are_decided_by_the_higher_id(
    api: Api, nurse_first: bool
) -> None:
    run_id = start(api)
    at(api, 5)
    nurse = obs("s-1", "blood_pressure", 5, "nurse", num=120, num2=80)
    device = obs("s-1", "blood_pressure", 5, "device", num=100, num2=60)
    rows = [nurse, device] if nurse_first else [device, nurse]
    sent = api.post(f"/sim/runs/{run_id}/batch", batch(minute=5, observations=rows))
    assert sent.status_code == 204
    m = vital_of(api, "blood_pressure")
    assert m.provenance.source.value == ("device" if nurse_first else "nurse")


def test_the_feed_row_of_only_the_new_keys_is_served(api: Api) -> None:
    run_id = start(api)
    post = api.client.post("/patients/s-1/vitals", headers=api.as_("rn-delacruz"), json=FOUR)
    assert post.status_code == 200
    (row,) = feed(api, run_id)
    row.pop("seq")
    base = {**NOT_APPLICABLE, "clinician_id": "rn-delacruz", "kind": "record_vitals"}
    assert row == {**base, "patient_id": "s-1", **FOUR}


# --- One shared ward ---------------------------------------------------------------------------

LIVE = "Another ward simulation is live"
REPLACED = "This run has been replaced by a newer one"


def tally(api: Api) -> list[dict[str, Any]]:
    return api.sql(
        "SELECT (SELECT count(*) FROM run) AS runs, (SELECT count(*) FROM patient) AS patients,"
        " (SELECT count(*) FROM observation) AS observations,"
        " (SELECT count(*) FROM sim_event) AS events,"
        " (SELECT count(*) FROM care_assignment) AS assignments"
    )


def register(api: Api, client_id: str) -> Any:
    return api.post("/sim/runs", new_run(client_id=client_id))


def test_a_different_client_cannot_take_over_a_live_run_and_nothing_is_written(api: Api) -> None:
    first = start(api, client_id="client-a")
    before = tally(api)
    refused = register(api, "client-b")
    assert (refused.status_code, refused.json()) == (409, {"detail": LIVE})
    assert tally(api) == before
    assert api.sql("SELECT max(id) AS id FROM run") == [{"id": first}]
    assert api.ward("rn-delacruz")["receiving_data"] is True  # still the first client's ward
    assert api.sql("SELECT client_id FROM run ORDER BY id") == [
        {"client_id": None},  # the fixture
        {"client_id": "client-a"},
    ]


def test_the_same_client_may_replace_its_own_live_run_which_is_what_a_restart_does(
    api: Api,
) -> None:
    first = start(api, client_id="client-a")
    second = start(api, client_id="client-a")
    assert second > first
    assert api.ward("rn-delacruz")["receiving_data"] is True
    assert register(api, "client-b").status_code == 409  # and the new run is the one that holds it


def test_a_new_run_is_live_from_creation_and_every_batch_renews_it(api: Api) -> None:
    run_id = start(api, client_id="client-a")
    assert api.ward("rn-delacruz")["receiving_data"] is True  # no batch yet
    assert register(api, "client-b").status_code == 409  # not replaceable the moment it exists
    api.wall.now += timedelta(seconds=30)  # the window is 30 seconds, as receiving_data's
    assert api.ward("rn-delacruz")["receiving_data"] is True
    assert register(api, "client-b").status_code == 409
    api.wall.now += timedelta(seconds=1)
    assert api.ward("rn-delacruz")["receiving_data"] is False
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=1))  # a batch makes it live again
    assert api.ward("rn-delacruz")["receiving_data"] is True
    api.wall.now += timedelta(seconds=30)
    assert register(api, "client-b").status_code == 409
    api.wall.now += timedelta(seconds=1)
    assert register(api, "client-b").status_code == 201  # liveness and replaceability agree


def test_a_stale_or_ended_run_may_be_replaced_by_anyone(api: Api) -> None:
    start(api, client_id="client-a")
    api.wall.now += timedelta(seconds=31)
    stale = register(api, "client-b")
    assert stale.status_code == 201
    api.post(f"/sim/runs/{stale.json()['run_id']}/batch", batch(minute=1, ended=True))
    assert register(api, "client-c").status_code == 201  # ended: free at once, however fresh


def test_a_fixture_run_never_blocks(api: Api) -> None:
    assert api.sql("SELECT source, last_batch_at FROM run") == [
        {"source": "fixture", "last_batch_at": None}
    ]
    assert register(api, "client-z").status_code == 201


def test_a_superseded_sim_gets_409_and_its_batch_writes_nothing(api: Api) -> None:
    old = start(api, client_id="client-a")
    api.wall.now += timedelta(seconds=31)
    new = start(api, client_id="client-b")
    before = tally(api)
    (run_before,) = api.sql("SELECT * FROM run WHERE id = %s", old)
    refused = api.post(f"/sim/runs/{old}/batch", batch(minute=5))
    assert (refused.status_code, refused.json()) == (409, {"detail": REPLACED})
    assert tally(api) == before
    assert api.sql("SELECT * FROM run WHERE id = %s", old) == [run_before]
    assert register(api, "client-a").status_code == 409  # the superseded sim cannot take it back
    assert api.post(f"/sim/runs/{new}/batch", batch(minute=1)).status_code == 204
    assert api.post("/sim/runs/99/batch", batch()).status_code == 404  # a missing run is not this


@pytest.mark.parametrize("client_id", [None, "", "x" * 65, 7, ["a"]])
def test_a_client_id_is_required_and_bounded(api: Api, client_id: Any) -> None:
    body = new_run()
    if client_id is None:
        del body["client_id"]
    else:
        body["client_id"] = client_id
    assert api.post("/sim/runs", body).status_code == 422
    assert len(api.sql("SELECT id FROM run")) == 1
    assert register(api, "x" * 64).status_code == 201  # the longest allowed


@pytest.mark.parametrize("sims", [2, THREADS])
def test_simultaneous_registrations_cannot_both_win(api: Api, sims: int) -> None:
    for round_ in range(ROUNDS):
        api.wall.now += timedelta(seconds=31)  # the last run is stale: the ward is free this round
        before = len(api.sql("SELECT id FROM run"))
        sent = together(sims, lambda i: register(api, f"client-{i}"))
        assert sorted(r.status_code for r in sent) == [201] + [409] * (sims - 1), round_
        assert len(api.sql("SELECT id FROM run")) == before + 1
        winner = next(r for r in sent if r.status_code == 201).json()["run_id"]
        assert api.sql("SELECT max(id) AS id FROM run") == [{"id": winner}]


def test_a_registration_waits_for_a_batch_in_flight_on_the_run_it_would_replace(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = start(api, client_id="client-a")
    api.wall.now += timedelta(seconds=40)  # the last batch is 40 seconds old: stale, until the next
    reached, go = threading.Event(), threading.Event()
    real = ingest.upsert_bins

    def pause(*args: Any) -> None:
        reached.set()  # in the batch, before it writes the run's last_batch_at
        assert go.wait(20)
        real(*args)

    monkeypatch.setattr(ingest, "upsert_bins", pause)

    def run_batch() -> bool:
        with connect(api.url) as conn:
            return ingest.add_batch(conn, run_id, Batch.model_validate(batch(minute=1)), api.wall, 7)

    with ThreadPoolExecutor(2) as pool:
        batch_done = pool.submit(run_batch)
        assert reached.wait(20)
        takeover = pool.submit(register, api, "client-b")
        for _ in range(1000):  # until the registration is blocked behind the batch
            if api.sql("SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock'"):
                break
            time.sleep(0.02)
        go.set()
        assert batch_done.result(30) is True
        refused = takeover.result(30)
    assert (refused.status_code, refused.json()) == (409, {"detail": LIVE})  # the batch renewed it
    assert api.sql("SELECT max(id) AS id FROM run") == [{"id": run_id}]


# --- Stays --------------------------------------------------------------------------------------


def stays(api: Api, who: str) -> dict[str, dict[str, Any]]:
    response = api.client.get("/wards/W4/stays", headers=api.as_(who))
    assert response.status_code == 200, response.text
    return {s["id"]: s for s in response.json()}


def ward_ids(api: Api, who: str) -> set[str]:
    ward = api.ward(who)
    return {r["id"] for r in (*ward["ranked"], *ward["abstained"])}


def admission(pid: str = "s-2", minute: int = 30, **kw: Any) -> dict[str, Any]:
    return {**sim_patient(pid, **kw), "minute": minute}


def test_a_ward_takes_admissions_and_departures_while_it_runs(api: Api) -> None:
    run_id = start(api)
    body = batch(
        minute=30,
        admissions=[admission()],
        departures=[{"patient_id": "s-1", "minute": 30, "outcome": "higher_care"}],
    )
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert ward_ids(api, "rn-manalo") == {"s-2"}

    everyone = stays(api, "rn-manalo")
    assert everyone["s-1"]["admitted_at"] is None  # on the ward before the run: arrival unknown
    assert everyone["s-1"]["outcome"] == "higher_care"
    assert everyone["s-1"]["left_at"] is not None
    assert everyone["s-2"]["admitted_at"] is not None
    assert (everyone["s-2"]["left_at"], everyone["s-2"]["outcome"]) == (None, None)
    assert all(s["synthetic"] for s in everyone.values())

    # A retry changes nothing, and the first departure time stands.
    before = api.sql("SELECT id, left_at, outcome FROM patient WHERE run_id = %s ORDER BY id", run_id)
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    late = {**body, "departures": [{"patient_id": "s-1", "minute": 99, "outcome": "discharged"}]}
    assert api.post(f"/sim/runs/{run_id}/batch", late).status_code == 204
    after = api.sql("SELECT id, left_at, outcome FROM patient WHERE run_id = %s ORDER BY id", run_id)
    assert before == after


def test_someone_who_has_left_is_out_of_every_live_view(api: Api) -> None:
    run_id = start(api)
    body = batch(minute=5, departures=[{"patient_id": "s-1", "minute": 5, "outcome": "discharged"}])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    headers = api.as_("rn-manalo")
    assert api.client.get("/patients", headers=headers).json() == []
    assert api.client.get("/patients/s-1", headers=headers).status_code == 404


def test_stays_are_scoped_and_audited(api: Api) -> None:
    run_id = start(api)
    body = batch(minute=5, admissions=[admission(rn="rn-reyes", md=None)])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    assert set(stays(api, "rn-delacruz")) == {"s-1"}
    assert set(stays(api, "rn-reyes")) == {"s-2"}
    assert set(stays(api, "rn-manalo")) == {"s-1", "s-2"}
    rows = api.sql("SELECT resource, outcome FROM audit_log WHERE action = 'view_stays'")
    assert rows == [{"resource": "W4", "outcome": "ok"}] * 3


def test_stays_for_another_ward_are_not_found(api: Api) -> None:
    response = api.client.get("/wards/W9/stays", headers=api.as_("rn-manalo"))
    assert response.status_code == 404
    (row,) = api.sql("SELECT resource, outcome FROM audit_log WHERE action = 'view_stays'")
    assert row == {"resource": "W9", "outcome": "not_found"}


def test_an_app_admission_records_when(api: Api) -> None:
    form = {**admit_input(), "bed": "12A"}  # the sample's 4A is taken
    response = api.client.post("/patients", json=form, headers=api.as_("rn-manalo"))
    assert response.status_code == 201, response.text
    everyone = stays(api, "rn-manalo")
    assert everyone[response.json()["id"]]["admitted_at"] is not None


@pytest.mark.parametrize(
    "over",
    [
        {"departures": [{"patient_id": "ghost", "minute": 5, "outcome": "discharged"}]},
        {"departures": [{"patient_id": "s-1", "minute": 5, "outcome": "vanished"}]},
        {"admissions": [admission(rn="nobody")]},
    ],
)
def test_a_bad_move_is_refused_and_writes_nothing(api: Api, over: dict[str, Any]) -> None:
    run_id = start(api)
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=5, **over)).status_code == 422
    rows = api.sql("SELECT id, left_at FROM patient WHERE run_id = %s", run_id)
    assert rows == [{"id": "s-1", "left_at": None}]


# --- The live view the ward viewer draws ---------------------------------------------------------


def view(*ids: str, **shared: Any) -> dict[str, Any]:
    return {"shared": shared, "patients": [{"id": pid, "view": {"bed": pid}} for pid in ids]}


def live(api: Api, who: str) -> dict[str, Any]:
    response = api.client.get("/wards/W4/live", headers=api.as_(who))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert isinstance(body.pop("can_control"), bool)  # what it says is its own test, below
    body.pop("now")  # ward time as served: its own test, below
    return body


def test_the_live_view_carries_the_ward_time_it_was_computed_at(api: Api) -> None:
    h = api.as_("rn-manalo")
    assert api.client.get("/wards/W4/live", headers=h).json()["now"] == "2026-09-03T07:00:00Z"  # seeded: as_of
    start(api)
    api.wall.now += timedelta(seconds=20)
    assert api.client.get("/wards/W4/live", headers=api.as_("rn-delacruz")).json()["now"] == iso(api.wall.now)


def test_the_live_view_is_scoped_audited_and_replaced_whole(api: Api) -> None:
    run_id = start(api)
    assert live(api, "rn-manalo") == {"shared": None, "patients": []}  # nothing published yet
    body = batch(minute=5, admissions=[admission(rn="rn-reyes", md=None), admission("s-3")])
    assert api.post(f"/sim/runs/{run_id}/batch", body).status_code == 204
    # Any JSON goes in and comes back as it was. "s-9" is nobody's patient: nobody is served it.
    shared = {"staff": [{"id": "rn-reyes", "x": 1.5, "y": -2, "busy": False, "at": None}], "t": 0}
    sent = {**view("s-1", "s-2", "s-3", "s-9"), "shared": shared}
    assert api.post(f"/sim/runs/{run_id}/view", sent).status_code == 204

    assert live(api, "rn-reyes") == {**sent, "patients": sent["patients"][1:2]}
    assert live(api, "rn-delacruz") == {**sent, "patients": sent["patients"][0:3:2]}
    assert live(api, "rn-manalo") == {**sent, "patients": sent["patients"][:3]}

    api.wall.now += timedelta(seconds=61)  # a repeat inside a minute leaves no row; this one does
    gone = batch(minute=6, departures=[{"patient_id": "s-1", "minute": 6, "outcome": "discharged"}])
    assert api.post(f"/sim/runs/{run_id}/batch", gone).status_code == 204
    assert [p["id"] for p in live(api, "rn-manalo")["patients"]] == ["s-2", "s-3"]

    assert api.post(f"/sim/runs/{run_id}/view", view("s-2", minute=7)).status_code == 204
    assert live(api, "rn-manalo") == view("s-2", minute=7)  # replaced, not merged
    rows = api.sql(
        "SELECT clinician_id, resource, outcome FROM audit_log WHERE action = 'view_live'"
    )
    assert [r["clinician_id"] for r in rows] == [
        "rn-manalo", "rn-reyes", "rn-delacruz", "rn-manalo",
    ]  # fmt: skip
    assert {(r["resource"], r["outcome"]) for r in rows} == {("W4", "ok")}


def test_a_live_view_for_another_ward_is_not_found(api: Api) -> None:
    response = api.client.get("/wards/W9/live", headers=api.as_("rn-manalo"))
    assert response.status_code == 404
    (row,) = api.sql("SELECT resource, outcome FROM audit_log WHERE action = 'view_live'")
    assert row == {"resource": "W9", "outcome": "not_found"}


def test_a_live_view_needs_the_ingest_key_and_the_newest_run(api: Api) -> None:
    old = start(api, client_id="client-a")
    for key in (None, "wrong"):
        assert api.post(f"/sim/runs/{old}/view", view("s-1"), key=key).status_code == 403
    assert Api(api.url, ingest_key=None).post(f"/sim/runs/{old}/view", view()).status_code == 503
    assert api.post("/sim/runs/99/view", view("s-1")).status_code == 404
    api.wall.now += timedelta(seconds=31)
    start(api, client_id="client-b")
    refused = api.post(f"/sim/runs/{old}/view", view("s-1"))
    assert (refused.status_code, refused.json()) == (409, {"detail": REPLACED})
    assert api.sql("SELECT count(*) AS n FROM run WHERE live_view IS NOT NULL") == [{"n": 0}]
    assert api.sql("SELECT * FROM audit_log WHERE action LIKE 'view%%'") == []


def _raw(text: str) -> str:
    return '{"shared": {"k": %s}, "patients": []}' % text


@pytest.mark.parametrize(
    "body",
    [
        {**view(), "shared": {"k": "x" * 65_529}},  # {"k":"…"} is 65,537 characters
        {"shared": {}, "patients": [{"id": "s-1", "view": {"k": "x" * 65_529}}]},
        view("s-1", "s-1"),
        {"shared": {}, "patients": [{"id": f"s-{i}", "view": {}} for i in range(201)]},
        {"shared": {}, "patients": [{"id": "", "view": {}}]},
        {"shared": {}, "patients": [{"id": "s-1", "view": {}, "name": "x"}]},
        {"shared": [], "patients": []},
        {"shared": None, "patients": []},
        {"patients": []},
        {**view(), "extra": 1},
        {**view(), "shared": {"deep": [{"text": "a\x00b"}]}},
        {**view(), "shared": {"a\x00b": 1}},
        _raw("NaN"),
        _raw('[1, {"a": -Infinity}]'),
        _raw('"\\ud800"'),  # a lone surrogate is not text PostgreSQL can hold
    ],
)
def test_a_live_view_that_is_refused_writes_nothing(api: Api, body: Any) -> None:
    run_id = start(api)
    assert api.post(f"/sim/runs/{run_id}/view", view("s-1", kept=True)).status_code == 204
    headers = {"X-Ingest-Key": KEY, "Content-Type": "application/json"}
    content = body if isinstance(body, str) else json.dumps(body)
    refused = api.client.post(f"/sim/runs/{run_id}/view", content=content, headers=headers)
    assert refused.status_code == 422, refused.text
    assert "xxxx" not in refused.text  # the refused value is not echoed
    assert live(api, "rn-manalo") == view("s-1", kept=True)


def test_a_live_view_at_the_size_limit_is_stored(api: Api) -> None:
    run_id = start(api)
    most = {"k": "é" * 65_528}  # characters, not bytes
    body = {"shared": most, "patients": [{"id": "s-1", "view": most}]}
    assert api.post(f"/sim/runs/{run_id}/view", body).status_code == 204
    assert live(api, "rn-delacruz") == body


# --- The feed of clinician actions the ward sim reads -------------------------------------------

NOTE = {"author": "a", "role": "RN", "content": "SECRET-NOTE-TEXT"}
NOT_APPLICABLE = dict.fromkeys(
    ("score", "systolic", "diastolic", "acvpu", "on_oxygen", "spo2_scale")
    + ("heart_rate", "respiratory_rate", "spo2", "temperature")
    + ("name", "age", "sex", "bed", "context")
)


def actions(api: Api, run_id: int, key: str | None = KEY, **params: Any) -> Any:
    headers = {"X-Ingest-Key": key} if key is not None else {}
    return api.client.get(f"/sim/runs/{run_id}/actions", params=params, headers=headers)


def feed(api: Api, run_id: int, **params: Any) -> list[dict[str, Any]]:
    response = actions(api, run_id, **params)
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["actions"]
    return rows


def test_each_clinician_write_puts_one_row_on_the_feed(api: Api) -> None:
    run_id = start(api)
    h, post = api.as_("rn-delacruz"), api.client.post
    every = {"systolic": 120, "diastolic": 80, "acvpu": "V", "on_oxygen": True, "spo2_scale": 2}
    full = {**admit_input(), "initialBp": "118/76", "initialAvpu": "C"}
    assert post("/patients/s-1/acknowledgement", headers=h, json={"score": 9}).status_code == 204
    assert post("/patients/s-1/vitals", headers=h, json=every).status_code == 200
    assert post("/patients/s-1/vitals", headers=h, json={"on_oxygen": False}).status_code == 200
    assert post("/patients/s-1/notes", headers=h, json=NOTE).status_code == 201
    assert post("/patients", headers=h, json=full).status_code == 201
    assert post("/patients", headers=h, json={**admit_input(), "bed": "4B"}).status_code == 201

    audited = api.sql("SELECT count(*) AS n FROM audit_log")
    response = actions(api, run_id)
    assert api.sql("SELECT count(*) AS n FROM audit_log") == audited  # not a clinician's read
    admitted = {"name": "Santos, Maria", "age": 67, "sex": "F", "bed": "4A"}
    admitted["context"] = "Acute decompensated heart failure"
    expected = [
        {"kind": "acknowledge", "patient_id": "s-1", "score": 9},
        {"kind": "record_vitals", "patient_id": "s-1", **every},
        {"kind": "record_vitals", "patient_id": "s-1", "on_oxygen": False},
        {"kind": "add_note", "patient_id": "s-1"},
        {"kind": "admit_patient", "patient_id": "p-001", **admitted}
        | {"systolic": 118, "diastolic": 76, "acvpu": "C"},
        {"kind": "admit_patient", "patient_id": "p-002", **admitted, "bed": "4B"},
    ]
    rows = response.json()["actions"]
    seqs = [r.pop("seq") for r in rows]
    assert seqs == sorted(set(seqs))
    assert rows == [{**NOT_APPLICABLE, "clinician_id": "rn-delacruz", **e} for e in expected]
    assert "SECRET" not in response.text  # a note's text never reaches the feed
    assert "SECRET" not in str(api.sql("SELECT * FROM clinician_action"))


def test_a_refused_write_labs_and_a_deleted_note_put_nothing_on_the_feed(api: Api) -> None:
    run_id = start(api)
    mine, theirs, post = api.as_("rn-delacruz"), api.as_("rn-reyes"), api.client.post
    for path, body in (
        ("/patients/s-1/acknowledgement", {"score": 9}),
        ("/patients/s-1/vitals", {"acvpu": "A"}),
        ("/patients/s-1/notes", NOTE),
    ):
        assert post(path, headers=theirs, json=body).status_code == 404  # not their patient
    bad = {"systolic": 500, "diastolic": 80}
    assert post("/patients/s-1/vitals", headers=mine, json=bad).status_code == 422
    refused = {**admit_input(), "initialBp": "999/80"}
    assert post("/patients", headers=mine, json=refused).status_code == 422
    assert post("/patients/s-1/acknowledgement", headers=mine, json={"score": 99}).status_code == 422
    assert feed(api, run_id) == []

    assert post("/patients/s-1/labs", headers=mine, json={"potassium": 4.0}).status_code == 200
    note = post("/patients/s-1/notes", headers=mine, json=NOTE).json()["id"]
    assert api.client.delete(f"/patients/s-1/notes/{note}", headers=mine).status_code == 204
    assert [r["kind"] for r in feed(api, run_id)] == ["add_note"]


def test_a_write_and_its_feed_row_are_one_transaction(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biosentry.repositories.postgres import PostgresStore

    def fail(*args: Any, **kw: Any) -> None:
        raise RuntimeError("the database went away")

    start(api)
    h = api.as_("rn-delacruz")
    count = (
        "SELECT (SELECT count(*) FROM clinician_action) AS actions,"
        " (SELECT count(*) FROM acknowledgement) AS acks, (SELECT count(*) FROM patient) AS patients,"
        " (SELECT count(*) FROM clinical_note) AS notes, (SELECT count(*) FROM observation) AS obs"
    )
    before = api.sql(count)
    monkeypatch.setattr(PostgresStore, "acknowledge", fail)  # the write fails: no feed row
    with pytest.raises(RuntimeError):
        api.client.post("/patients/s-1/acknowledgement", headers=h, json={"score": 9})
    monkeypatch.setattr(PostgresStore, "add_action", fail)  # the feed row fails: no write
    for path, body in (
        ("/patients/s-1/vitals", {"acvpu": "V"}),
        ("/patients/s-1/notes", NOTE),
        ("/patients", admit_input()),
    ):
        with pytest.raises(RuntimeError):
            api.client.post(path, headers=h, json=body)
    assert api.sql(count) == before


def test_the_feed_is_read_in_order_200_rows_at_a_time_after_a_cursor(api: Api) -> None:
    h = api.as_("rn-delacruz")
    api.client.post("/patients/p-001/acknowledgement", headers=h, json={"score": 9})
    assert [r["kind"] for r in feed(api, 1)] == ["acknowledge"]  # the fixture ward has a feed too
    run_id = start(api)
    assert feed(api, run_id) == []  # nothing yet, and never another run's rows
    seqs = [
        r["seq"]
        for r in api.sql(
            "INSERT INTO clinician_action (run_id, kind, patient_id, clinician_id, at, data)"
            " SELECT %s, 'add_note', 's-1', 'rn-delacruz', now(), '{}'::jsonb"
            " FROM generate_series(1, 205) RETURNING seq",
            run_id,
        )
    ]
    first = [r["seq"] for r in feed(api, run_id)]
    assert first == seqs[:200] == [r["seq"] for r in feed(api, run_id, after=0)]
    assert [r["seq"] for r in feed(api, run_id, after=first[-1])] == seqs[200:]
    assert feed(api, run_id, after=seqs[-1]) == feed(api, run_id, after=2**63 - 1) == []
    for after in (-1, 2**63, "x", 1.5):
        assert actions(api, run_id, after=after).status_code == 422, after


def test_the_feed_needs_the_ingest_key_and_the_newest_run(api: Api) -> None:
    old = start(api, client_id="client-a")
    for key in (None, "wrong"):
        assert actions(api, old, key=key).status_code == 403
        assert actions(api, old, key=key, after=-1).status_code == 403  # the key comes first
    assert actions(Api(api.url, ingest_key=None), old).status_code == 503
    assert actions(api, 99).status_code == 404
    api.wall.now += timedelta(seconds=31)
    start(api, client_id="client-b")
    refused = actions(api, old)
    assert (refused.status_code, refused.json()) == (409, {"detail": REPLACED})
    rows = api.sql("SELECT clinician_id, action, resource, outcome FROM audit_log")
    assert rows == [{"clinician_id": None, "action": "ingest", "resource": "-", "outcome": "denied"}] * 5


# --- The commands the ward viewer sends and the sim reads ---------------------------------------

SPEED = {"kind": "set_speed", "speed": 10}
PATIENT_KINDS = ("inject_deterioration", "start_arrhythmia", "detach_patch", "apply_patch")
NOT_RUNNING = "The ward is not receiving data from a running simulation"


def command(api: Api, who: str, ward: str = "W4", **body: Any) -> Any:
    return api.client.post(f"/wards/{ward}/sim/commands", headers=api.as_(who), json=body)


def commands(api: Api, run_id: int, key: str | None = KEY, **params: Any) -> Any:
    headers = {"X-Ingest-Key": key} if key is not None else {}
    return api.client.get(f"/sim/runs/{run_id}/commands", params=params, headers=headers)


def sent(api: Api, run_id: int, **params: Any) -> list[dict[str, Any]]:
    response = commands(api, run_id, **params)
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["commands"]
    return rows


def sim_audit(api: Api) -> list[tuple[str | None, str, str]]:
    rows = api.sql(
        "SELECT clinician_id, resource, outcome FROM audit_log WHERE action = 'sim_command'"
        " ORDER BY seq"
    )
    return [(r["clinician_id"], r["resource"], r["outcome"]) for r in rows]


def test_only_an_md_or_the_charge_nurse_is_told_they_can_control_the_sim(api: Api) -> None:
    start(api)
    for who, allowed in (
        ("md-santos", True), ("md-aquino", True), ("rn-manalo", True),
        ("rn-delacruz", False), ("rn-reyes", False),
    ):  # fmt: skip
        response = api.client.get("/wards/W4/live", headers=api.as_(who))
        assert response.json()["can_control"] is allowed, who


def test_a_command_goes_on_the_feed_in_order_with_what_it_says_and_who_sent_it(api: Api) -> None:
    run_id = start(api)
    assert sent(api, run_id) == []
    assert command(api, "md-santos", **SPEED).status_code == 204
    assert command(api, "rn-manalo", kind="reset").status_code == 204
    for kind in PATIENT_KINDS:
        assert command(api, "md-santos", kind=kind, patient_id="s-1").status_code == 204
    assert command(api, "rn-manalo", kind="set_speed", speed=0).status_code == 204
    rows = sent(api, run_id)
    seqs = [r.pop("seq") for r in rows]
    assert seqs == sorted(set(seqs)) and len(seqs) == 7
    expected = [
        ("set_speed", None, "md-santos", 10), ("reset", None, "rn-manalo", None),
        *((kind, "s-1", "md-santos", None) for kind in PATIENT_KINDS),
        ("set_speed", None, "rn-manalo", 0),
    ]  # fmt: skip
    assert rows == [
        {"kind": k, "patient_id": p, "clinician_id": c, "speed": v} for k, p, c, v in expected
    ]
    later = sent(api, run_id, after=seqs[3])
    assert [r["seq"] for r in later] == seqs[4:]
    assert sim_audit(api) == [
        ("md-santos", "W4", "ok"), ("rn-manalo", "W4", "ok"),
        *(("md-santos", "s-1", "ok") for _ in PATIENT_KINDS),
        ("rn-manalo", "W4", "ok"),
    ]  # fmt: skip


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"kind": "explode"},
        {"kind": "set_speed"},
        {"kind": "set_speed", "speed": 5},
        {"kind": "set_speed", "speed": "10"},
        {"kind": "set_speed", "speed": True},
        {"kind": "set_speed", "speed": 1.0},
        {"kind": "set_speed", "speed": 10, "patient_id": "s-1"},
        {"kind": "reset", "speed": 1},
        {"kind": "reset", "patient_id": "s-1"},
        {"kind": "detach_patch"},
        {"kind": "detach_patch", "patient_id": ""},
        {"kind": "detach_patch", "patient_id": "s-1", "speed": 1},
        {"kind": "apply_patch", "patient_id": "s-1", "note": "x"},
    ],
)
def test_a_command_that_does_not_fit_its_kind_is_422_and_leaves_no_row(
    api: Api, body: dict[str, Any]
) -> None:
    run_id = start(api)
    assert command(api, "md-santos", **body).status_code == 422
    assert sent(api, run_id) == []
    assert sim_audit(api) == [("md-santos", "-", "rejected")]


def test_a_command_with_no_session_is_401_before_anything_is_said_of_its_body(api: Api) -> None:
    start(api)
    for body in ({"kind": "reset"}, {"kind": "explode"}):
        assert api.client.post("/wards/W4/sim/commands", json=body).status_code == 401
    assert sim_audit(api) == []


def test_a_clinician_who_may_not_command_the_sim_gets_403_and_it_is_audited(api: Api) -> None:
    run_id = start(api)
    for who in ("rn-delacruz", "rn-reyes"):
        assert command(api, who, **SPEED).status_code == 403
        assert command(api, who, kind="detach_patch", patient_id="s-1").status_code == 403
    assert sent(api, run_id) == []
    assert sim_audit(api) == [
        ("rn-delacruz", "W4", "denied"), ("rn-delacruz", "W4", "denied"),
        ("rn-reyes", "W4", "denied"), ("rn-reyes", "W4", "denied"),
    ]  # fmt: skip


def test_a_command_for_another_ward_or_a_patient_that_is_not_yours_is_404(api: Api) -> None:
    run_id = start(api)
    assert command(api, "md-santos", ward="W9", **SPEED).status_code == 404
    # md-aquino is a doctor of the fixture ward and has no patient in this run; "ghost" is nobody's
    for patient in ("s-1", "ghost"):
        refused = command(api, "md-aquino", kind="apply_patch", patient_id=patient)
        assert (refused.status_code, refused.json()) == (404, {"detail": "Not found"})
    assert command(api, "md-santos", kind="apply_patch", patient_id="s-1").status_code == 204
    assert [r["patient_id"] for r in sent(api, run_id)] == ["s-1"]
    assert sim_audit(api) == [
        ("md-santos", "W9", "not_found"), ("md-aquino", "s-1", "not_found"),
        ("md-aquino", "ghost", "not_found"), ("md-santos", "s-1", "ok"),
    ]  # fmt: skip


def test_a_patient_who_has_left_is_not_one_to_command(api: Api) -> None:
    run_id = start(api)
    gone = batch(minute=1, departures=[{"patient_id": "s-1", "minute": 1, "outcome": "discharged"}])
    assert api.post(f"/sim/runs/{run_id}/batch", gone).status_code == 204
    assert command(api, "md-santos", kind="detach_patch", patient_id="s-1").status_code == 404


def test_nothing_is_queued_for_a_ward_that_is_not_running_a_sim(api: Api) -> None:
    fixture = command(api, "rn-manalo", **SPEED)
    assert (fixture.status_code, fixture.json()) == (409, {"detail": NOT_RUNNING})
    run_id = start(api)
    assert command(api, "rn-manalo", **SPEED).status_code == 204
    api.wall.now += timedelta(seconds=31)  # stale
    assert command(api, "rn-manalo", **SPEED).status_code == 409
    api.wall.now -= timedelta(seconds=31)
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=1, ended=True)).status_code == 204
    assert command(api, "rn-manalo", kind="reset").status_code == 409  # ended
    assert len(sent(api, run_id)) == 1
    assert [o for _, _, o in sim_audit(api)] == ["rejected", "ok", "rejected", "rejected"]


def test_a_command_for_a_run_replaced_while_it_was_being_handled_is_refused_and_writes_nothing(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biosentry.repositories.postgres import PostgresStore

    start(api, client_id="client-a")
    api.as_("md-santos")
    original = PostgresStore.can_control

    def replaced_then_checked(self: PostgresStore, clinician_id: str) -> bool:
        start(api, client_id="client-a")  # the same client replaces its own run, after this request began
        return original(self, clinician_id)

    monkeypatch.setattr(PostgresStore, "can_control", replaced_then_checked)
    refused = command(api, "md-santos", **SPEED)
    assert (refused.status_code, refused.json()) == (409, {"detail": REPLACED})
    assert api.sql("SELECT * FROM sim_command") == []
    assert sim_audit(api) == [("md-santos", "W4", "rejected")]


def test_a_command_and_its_feed_row_are_one_transaction(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biosentry.repositories.postgres import PostgresStore

    def fail(*args: Any, **kw: Any) -> None:
        raise RuntimeError("the database went away")

    run_id = start(api)
    api.as_("md-santos")
    monkeypatch.setattr(PostgresStore, "add_command", fail)
    with pytest.raises(RuntimeError):
        command(api, "md-santos", **SPEED)
    assert sent(api, run_id) == [] and sim_audit(api) == []


def test_the_command_feed_is_read_in_order_200_rows_at_a_time_after_a_cursor(api: Api) -> None:
    run_id = start(api)
    assert sent(api, run_id) == []  # nothing yet, and never another run's rows
    seqs = [
        r["seq"]
        for r in api.sql(
            "INSERT INTO sim_command (run_id, kind, patient_id, clinician_id, at, data)"
            " SELECT %s, 'reset', NULL, 'md-santos', now(), '{}'::jsonb"
            " FROM generate_series(1, 205) RETURNING seq",
            run_id,
        )
    ]
    first = [r["seq"] for r in sent(api, run_id)]
    assert first == seqs[:200] == [r["seq"] for r in sent(api, run_id, after=0)]
    assert [r["seq"] for r in sent(api, run_id, after=first[-1])] == seqs[200:]
    assert sent(api, run_id, after=seqs[-1]) == sent(api, run_id, after=2**63 - 1) == []
    for after in (-1, 2**63, "x", 1.5):
        assert commands(api, run_id, after=after).status_code == 422, after


def test_the_command_feed_needs_the_ingest_key_and_the_newest_run(api: Api) -> None:
    old = start(api, client_id="client-a")
    for key in (None, "wrong"):
        assert commands(api, old, key=key).status_code == 403
        assert commands(api, old, key=key, after=-1).status_code == 403  # the key comes first
    assert commands(Api(api.url, ingest_key=None), old).status_code == 503
    assert commands(api, 99).status_code == 404
    api.wall.now += timedelta(seconds=31)
    start(api, client_id="client-b")
    refused = commands(api, old)
    assert (refused.status_code, refused.json()) == (409, {"detail": REPLACED})
