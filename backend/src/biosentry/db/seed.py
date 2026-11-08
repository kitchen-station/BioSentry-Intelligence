"""Create the schema, the clinicians and the fixture run from the committed synthetic ward.

    python -m biosentry.db.seed [--url DATABASE_URL] [--reset]

`--reset` drops everything first (make db-reset). Otherwise a no-op once a fixture run exists. The fixture is the dashboard's own dataset
(frontend/src/data/synthetic.json); the API serves it from here, scored on read.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg.conninfo import conninfo_to_dict

from biosentry.core.config import DEFAULT_DATASET, Settings
from biosentry.repositories.postgres import (
    HISTORY_VITALS,
    Conn,
    Obs,
    assign,
    connect,
    insert_observations,
    insert_patient,
    insert_run,
    transaction,
    upsert_bins,
    whole,
)

SCHEMA = Path(__file__).with_name("schema.sql")
# One key for "this database is being set up": two API processes starting at once take turns, so
# neither trips over the other's CREATE or seeds the fixture twice. (Run locks use small run ids.)
SETUP_LOCK = 0x62696F7365656400
parse = datetime.fromisoformat  # the fixture file's times; the database hands back datetimes


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


def seed(conn: Conn, fixture: Path = DEFAULT_DATASET) -> None:
    if conn.execute("SELECT 1 FROM run WHERE source = 'fixture'").fetchone():
        return
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    as_of = parse(raw["as_of"])
    patients = {p["id"]: p for p in raw["ranked"] + raw["unscored"]}
    with transaction(conn):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clinician (id, role) VALUES (%s, %s) ON CONFLICT DO NOTHING",
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
                    "last_sample_at": (
                        None if age is None else whole(as_of - timedelta(seconds=age))
                    ),
                    "tier_b": p["tier_b"] or None,
                    "tier_c": p["tier_c"] or None,
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
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clinical_note (run_id, id, patient_id, author, role, timestamp,"
                " content, synthetic) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)",
                [
                    (
                        run_id,
                        n["id"],
                        n["patient_id"],
                        n["author"],
                        n["role"],
                        whole(parse(n["timestamp"])),
                        n["content"],
                    )
                    for n in raw["notes"]
                ],
            )


def ensure(url: str, fixture: Path = DEFAULT_DATASET) -> None:
    """Make the database ready to serve: schema, clinicians, fixture run. Idempotent, and safe
    when several processes do it at once (one transaction, behind the setup lock)."""
    with connect(url) as conn, transaction(conn):
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        seed(conn, fixture)


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def reset(url: str) -> None:
    """Drop every table (and the audit trail with them), then set up again from the fixture.
    Only on this machine: the database host must be loopback."""
    host = conninfo_to_dict(url).get("host")
    if host not in LOOPBACK:
        raise SystemExit(f"refusing to reset a database whose host is {host!r}: only loopback")
    with connect(url) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    ensure(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=Settings.from_env().database_url)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    (reset if args.reset else ensure)(args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
