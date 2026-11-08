"""The SQLite store: it reproduces the fixture, survives a restart, keeps its invariants in the
database, isolates runs, ingests the ward simulation and times it against the wall clock."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from biosentry.api.app import create_app
from biosentry.core.config import DEFAULT_DATASET, Settings
from biosentry.repositories.sqlite import connect

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
KEY = "test-ingest-key"
REPO = Path(__file__).resolve().parents[3]
DATA: dict[str, Any] = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
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
    def __init__(self, tmp_path: Path, ingest_key: str | None = KEY) -> None:
        self.db = tmp_path / "t.db"
        self.wall = Wall()
        self.settings = Settings(db=self.db, ingest_key=ingest_key)
        self.client = TestClient(create_app(self.settings, clock=self.wall))
        self.tokens: dict[str, dict[str, str]] = {}

    def as_(self, clinician: str) -> dict[str, str]:
        if clinician not in self.tokens:
            token = self.client.post("/session", json={"clinician_id": clinician}).json()["token"]
            self.tokens[clinician] = {"Authorization": f"Bearer {token}"}
        return self.tokens[clinician]

    def ward(self, clinician: str) -> dict[str, Any]:
        response = self.client.get("/wards/W4/rows", headers=self.as_(clinician))
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    def sql(self, statement: str, *args: Any) -> list[sqlite3.Row]:
        conn = connect(self.db)
        try:
            return conn.execute(statement, args).fetchall()
        finally:
            conn.close()

    def post(self, path: str, body: Any, key: str | None = KEY) -> Any:
        headers = {"X-Ingest-Key": key} if key is not None else {}
        return self.client.post(path, json=body, headers=headers)


@pytest.fixture
def api(tmp_path: Path) -> Api:
    return Api(tmp_path)


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


def new_run(source: str = "synthetic", patients: list[dict] | None = None) -> dict[str, Any]:
    return {"source": source, "seed": 7, "origin": ORIGIN, "patients": patients or [sim_patient()]}


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
    ward = api.ward(who)
    mine = set(CLINICIANS[who])
    expected = [r for r in DATA["ranked"] if r["id"] in mine]  # the fixture is in rank order
    assert [{**r, "rank": i} for i, r in enumerate(expected, 1)] == ward["ranked"]
    assert [a for a in DATA["abstained"] if a["id"] in mine] == ward["abstained"]
    assert ward["receiving_data"] is True
    assert [r["escalation_reasons"] for r in ward["ranked"]] == [
        r["escalation_reasons"] for r in expected
    ]


def test_the_fixture_detail_pages_are_reproduced(api: Api) -> None:
    for row in DATA["ranked"]:
        detail = api.client.get(f"/patients/{row['id']}", headers=api.as_("rn-manalo")).json()
        assert detail["vital_history"] == DATA["details"][row["id"]]["vital_history"]
        assert detail["tier_breakdown"] == DATA["details"][row["id"]]["tier_breakdown"]


def test_notes_and_clinicians_are_seeded(api: Api) -> None:
    assert {r["id"]: r["role"] for r in api.sql("SELECT * FROM clinician")} == {
        c: e["role"] for c, e in DATA["clinicians"].items()
    }
    shown = api.client.get("/patients/p-001/notes", headers=api.as_("rn-manalo")).json()
    assert {n["id"] for n in shown} == {
        n["id"] for n in DATA["notes"] if n["patient_id"] == "p-001"
    }


def test_seeding_twice_changes_nothing(tmp_path: Path) -> None:
    first = Api(tmp_path)
    before = [tuple(r) for r in first.sql("SELECT count(*) FROM observation")]
    second = Api(tmp_path)  # a restart seeds again: a no-op
    assert [tuple(r) for r in second.sql("SELECT count(*) FROM observation")] == before
    assert len(second.sql("SELECT * FROM run")) == 1


# --- Durability -------------------------------------------------------------------------------


def test_data_survives_closing_and_reopening_the_database(tmp_path: Path) -> None:
    first = Api(tmp_path)
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

    second = Api(tmp_path)  # a new app on the same file, as after a restart
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


def test_the_audit_log_refuses_update_and_delete(api: Api) -> None:
    api.client.get("/patients", headers=api.as_("rn-manalo"))
    conn = connect(api.db)
    for statement in ("UPDATE audit_log SET outcome = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(statement)
    assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] >= 1
    conn.close()


@pytest.mark.parametrize(
    ("num", "txt", "reason"),
    [(None, None, None), (1.0, None, "also a reason"), (None, "A", "also a reason")],
    ids=["neither", "number-and-reason", "text-and-reason"],
)
def test_an_observation_needs_a_value_or_a_reason_never_neither_or_both(
    api: Api, num: float | None, txt: str | None, reason: str | None
) -> None:
    conn = connect(api.db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observation (run_id, patient_id, code, num, txt, absent_reason,"
            " observed_at, source, quality, synthetic)"
            " VALUES (1, 'p-001', 'heart_rate', ?, ?, ?, '2026-09-03T07:00:00Z', 'device', 'ok', 1)",
            (num, txt, reason),
        )
    conn.close()


def test_the_synthetic_flag_must_agree_with_the_source(api: Api) -> None:
    conn = connect(api.db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO run (source, synthetic, ward, origin, as_of, tier_status)"
            " VALUES ('eicu_demo', 1, 'W4', ?, ?, '[]')",
            (ORIGIN, ORIGIN),
        )
    conn.close()


def test_foreign_keys_and_wal_are_on_for_every_connection(api: Api) -> None:
    conn = connect(api.db)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()


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
    dump = "\n".join(connect(api.db).iterdump())
    assert token not in dump  # only its hash is stored
    assert api.client.post("/session", json={"clinician_id": "nobody"}).status_code == 403
    assert api.client.post("/session", json={}).status_code == 422
    assert (
        api.client.post("/session", json={"clinician_id": "rn-manalo", "x": 1}).status_code == 422
    )


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
    (run,) = api.sql("SELECT * FROM run WHERE id = ?", run_id)
    assert (run["as_of"], run["last_batch_at"]) == ("2026-01-01T00:10:00Z", iso(api.wall.now))
    assert (run["received"], run["rejected"], run["abstained"], run["ended"]) == (5, 1, 2, 0)


def test_a_repeated_batch_adds_no_rows(api: Api) -> None:
    run_id = start(api)
    counts = "SELECT (SELECT count(*) FROM observation), (SELECT count(*) FROM vital_bin), (SELECT count(*) FROM sim_event)"
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=3))
    once = tuple(api.sql(counts)[0])
    assert api.post(f"/sim/runs/{run_id}/batch", batch(minute=3)).status_code == 204
    assert tuple(api.sql(counts)[0]) == once
    assert once[2] == 1


def test_the_run_decides_synthetic_not_the_client(api: Api) -> None:
    start(api)
    rows = api.sql("SELECT DISTINCT synthetic FROM observation WHERE run_id = 2")
    assert [r["synthetic"] for r in rows] == [1]
    body = batch(minute=1)
    body["observations"][0]["synthetic"] = False
    assert api.post("/sim/runs/2/batch", body).status_code == 422  # not a field the client may send


def test_an_eicu_run_propagates_synthetic_false_and_the_purge_removes_it(api: Api) -> None:
    run_id = start(api, source="eicu_demo")
    api.post(f"/sim/runs/{run_id}/batch", batch(minute=1))
    row = api.ward("rn-delacruz")["ranked"][0]
    assert row["synthetic"] is False
    assert all(m["provenance"]["synthetic"] is False for m in row["vitals"].values())
    detail = api.client.get("/patients/s-1", headers=api.as_("rn-delacruz")).json()
    assert detail["synthetic"] is False
    api.client.post(
        "/patients/s-1/notes",
        headers=api.as_("rn-delacruz"),
        json={"author": "a", "role": "RN", "content": "c"},
    )
    note = api.client.get("/patients/s-1/notes", headers=api.as_("rn-delacruz")).json()[0]
    assert note["synthetic"] is False
    api.client.post(
        "/patients/s-1/acknowledgement", headers=api.as_("rn-delacruz"), json={"score": 10}
    )
    audit_before = len(api.sql("SELECT * FROM audit_log"))

    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    statement = re.search(r"(DELETE FROM run WHERE source='eicu_demo')", makefile)
    assert statement, "the Makefile's db-purge-eicu statement"
    conn = connect(api.db)
    conn.execute(statement.group(1))
    conn.close()

    for table in (
        "patient",
        "care_assignment",
        "observation",
        "vital_bin",
        "clinical_note",
        "acknowledgement",
        "sim_event",
    ):
        assert api.sql(f"SELECT * FROM {table} WHERE run_id = ?", run_id) == [], table
    assert api.sql("SELECT * FROM run WHERE source = 'eicu_demo'") == []
    assert len(api.sql("SELECT * FROM audit_log")) == audit_before  # the trail stays
    assert (
        api.ward("rn-manalo")["ranked"][0]["id"] == "p-001"
    )  # the fixture is the active run again


@pytest.mark.parametrize("key", [None, "wrong", KEY[:-1], KEY + "x"])
def test_ingest_refuses_a_missing_or_wrong_key_and_writes_nothing(
    api: Api, key: str | None
) -> None:
    assert api.post("/sim/runs", new_run(), key=key).status_code == 403
    assert api.post("/sim/runs/1/batch", batch(), key=key).status_code == 403
    assert len(api.sql("SELECT * FROM run")) == 1


def test_ingest_fails_closed_when_no_key_is_configured(tmp_path: Path) -> None:
    api = Api(tmp_path, ingest_key=None)
    for key in (None, "", "anything"):
        assert api.post("/sim/runs", new_run(), key=key).status_code == 503
        assert api.post("/sim/runs/1/batch", batch(), key=key).status_code == 503
    assert len(api.sql("SELECT * FROM run")) == 1


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
    (run,) = api.sql("SELECT * FROM run WHERE id = ?", run_id)
    assert run["as_of"] == ORIGIN  # not moved
    assert (
        api.sql("SELECT * FROM observation WHERE run_id = ? AND observed_at > ?", run_id, ORIGIN)
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


def test_a_deleted_runs_id_is_never_reused_so_orphans_are_not_adopted(api: Api) -> None:
    run_id = start(api, source="eicu_demo", patients=[sim_patient("orphan")])
    conn = connect(api.db)
    conn.execute("PRAGMA foreign_keys=OFF")  # a raw delete that leaves the run's rows behind
    conn.execute("DELETE FROM run WHERE id = ?", (run_id,))
    conn.close()
    assert len(api.sql("SELECT * FROM patient WHERE run_id = ?", run_id)) == 1
    new_id = start(api)
    assert new_id > run_id
    assert {r["id"] for r in api.ward("rn-manalo")["ranked"]} == {"s-1"}  # not the orphan, not eICU


def test_the_audit_log_refuses_overwriting_a_row_but_still_appends(api: Api) -> None:
    api.client.get("/patients", headers=api.as_("rn-manalo"))
    (first,) = api.sql("SELECT * FROM audit_log")
    conn = connect(api.db)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT OR REPLACE INTO audit_log (seq, at, action, resource, outcome)"
            " VALUES (?, 'x', 'forged', '-', 'ok')",
            (first["seq"],),
        )
    conn.close()
    assert [tuple(r) for r in api.sql("SELECT * FROM audit_log")] == [tuple(first)]
    api.client.get("/patients", headers=api.as_("rn-manalo"))  # ordinary appends still work
    assert len(api.sql("SELECT * FROM audit_log")) == 2


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
    assert pin not in json.dumps([tuple(r) for r in api.sql("SELECT * FROM audit_log")])


def test_a_long_resource_is_cut_in_the_audit_log(api: Api) -> None:
    api.client.get("/patients/" + "p" * 3000, headers=api.as_("rn-delacruz"))
    (row,) = api.sql("SELECT resource, outcome FROM audit_log")
    assert (len(row["resource"]), row["outcome"]) == (200, "not_found")


def test_the_audit_trail_holds_no_sim_patient_data(api: Api) -> None:
    start(api)
    api.ward("rn-delacruz")
    api.client.get("/patients/s-1", headers=api.as_("rn-delacruz"))
    dump = json.dumps([tuple(r) for r in api.sql("SELECT * FROM audit_log")])
    for secret in ("Sim, Pat", "118", "a story", "9A"):
        assert secret not in dump
