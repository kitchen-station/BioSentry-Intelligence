"""Create the schema, the clinicians and the fixture run from the committed synthetic ward.

    python -m biosentry.db.seed [--db PATH]

A no-op once a fixture run exists. The fixture is the dashboard's own dataset
(frontend/src/data/synthetic.json); the API serves it from here, scored on read.
"""

import argparse
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from biosentry.core.config import DEFAULT_DATASET, DEFAULT_DB, Settings
from biosentry.repositories.sqlite import (
    HISTORY_VITALS,
    Obs,
    assign,
    connect,
    insert_observations,
    insert_patient,
    insert_run,
    iso,
    parse,
    transaction,
    upsert_bins,
)

SCHEMA = Path(__file__).with_name("schema.sql")


def _obs(patient_id: str, code: str, m: dict[str, Any]) -> Obs:
    data, prov = m["data"], m["provenance"]
    common = (patient_id, code, parse(prov["observed_at"]), prov["source"], prov["quality"])
    if not data["present"]:
        return Obs(*common, absent_reason=data["reason"])
    value = data["value"]
    if code == "blood_pressure":
        return Obs(*common, num=value["systolic"], num2=value["diastolic"])
    if code == "consciousness":
        return Obs(*common, txt=value)
    return Obs(*common, num=float(value))


def seed(conn: sqlite3.Connection, fixture: Path = DEFAULT_DATASET) -> None:
    if conn.execute("SELECT 1 FROM run WHERE source = 'fixture'").fetchone():
        return
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    as_of = parse(raw["as_of"])
    patients = {p["id"]: p for p in raw["ranked"] + raw["unscored"]}
    with transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO clinician (id, role) VALUES (?, ?)",
            [(cid, c["role"]) for cid, c in raw["clinicians"].items()],
        )
        run_id = insert_run(
            conn,
            source="fixture",
            seed=raw["seed"],
            ward=raw["ward"],
            origin=as_of,
            as_of=as_of,
            last_batch_at=None,
            tier_status=raw["ranked"][0]["tier_status"],
        )
        observations: list[Obs] = []
        bins = []
        for pid, p in patients.items():
            age = p["last_update_s"]
            insert_patient(
                conn,
                run_id,
                pid,
                {
                    **{
                        k: p[k]
                        for k in (
                            "name",
                            "age",
                            "sex",
                            "bed",
                            "admitting_context",
                            "mechanism",
                            "supporting",
                            "trend",
                            "signal_quality",
                        )
                    },
                    "last_sample_at": None if age is None else iso(as_of - timedelta(seconds=age)),
                    "tier_b": json.dumps(p["tier_b"]) if p["tier_b"] else None,
                    "tier_c": json.dumps(p["tier_c"]) if p["tier_c"] else None,
                    "story": raw["labels"][pid]["story"],
                },
            )
            observations += [_obs(pid, code, m) for code, m in p["vitals"].items()]
            observations += [
                _obs(pid, code, m)
                for code, m in p["labs"].items()
                if isinstance(m, dict) and "data" in m
            ]
            history = (raw["details"].get(pid) or p)["vital_history"]
            bins += [
                (pid, v, parse(h["time"]), h["value"]) for v in HISTORY_VITALS for h in history[v]
            ]
            # Temperature has a sparkline but no detail chart: only its last 12 bins exist.
            temp = p["vital_trends"]["temperature"]
            bins += [
                (pid, "temperature", as_of - timedelta(minutes=15 * (len(temp) - i)), value)
                for i, value in enumerate(temp)
            ]
        for pid in patients:
            assign(conn, run_id, pid)
        for cid, c in raw["clinicians"].items():
            for pid in c["patients"]:
                assign(conn, run_id, pid, cid)
        insert_observations(conn, run_id, True, observations, ignore=True)
        upsert_bins(conn, run_id, bins)
        conn.executemany(
            "INSERT INTO clinical_note (run_id, id, patient_id, author, role, timestamp, content,"
            " synthetic) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            [
                (
                    run_id,
                    n["id"],
                    n["patient_id"],
                    n["author"],
                    n["role"],
                    iso(parse(n["timestamp"])),
                    n["content"],
                )
                for n in raw["notes"]
            ],
        )


def ensure(db: Path = DEFAULT_DB, fixture: Path = DEFAULT_DATASET) -> None:
    """Make the database ready to serve: schema, clinicians, fixture run. Idempotent."""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        seed(conn, fixture)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=Settings.from_env().db)
    ensure(parser.parse_args(argv).db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
