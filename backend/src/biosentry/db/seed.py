"""Migrate the schema, then create the clinicians and the fixture run from the committed synthetic ward.

    python -m biosentry.db.seed [--url DATABASE_URL] [--reset]

`--reset` drops everything first (make db-reset). Otherwise a no-op once a fixture run exists. The fixture is the dashboard's own dataset
(frontend/src/data/synthetic.json); the API serves it from here, scored on read.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from biosentry.core.config import DEFAULT_DATASET, Settings, is_loopback, restricted
from biosentry.db.migrate import SETUP_LOCK, migrate
from biosentry.db.passphrase import hash_passphrase
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

# Local development only: seeding gives every clinician this passphrase when the connection it is
# seeding over is to loopback (judged on the established connection, see is_loopback) and the
# process is not in production, whatever the host. Anywhere else a database is seeded with none:
# set them with `make db-passphrase`. It is public, in the README.
DEV_PASSPHRASE = "dev-passphrase-local"

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


def seed(conn: Conn, fixture: Path = DEFAULT_DATASET, dev_passphrase: bool = False) -> None:
    if conn.execute("SELECT 1 FROM run WHERE source = 'fixture'").fetchone():
        return
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    as_of = parse(raw["as_of"])
    patients = {p["id"]: p for p in raw["ranked"] + raw["unscored"]}
    with transaction(conn):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clinician (id, role, passphrase_hash) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [
                    (cid, c["role"], hash_passphrase(DEV_PASSPHRASE) if dev_passphrase else None)
                    for cid, c in raw["clinicians"].items()
                ],
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


def ensure(url: str, fixture: Path = DEFAULT_DATASET, production: bool | None = None) -> None:
    """Make the database ready to serve: migrations, clinicians, fixture run. Idempotent, and safe
    when several processes do it at once (one transaction, behind the setup lock). `production`
    None: the process's BIOSENTRY_ENV decides. Never seeds the dev passphrase in production."""
    production = restricted() if production is None else production
    with connect(url, production) as conn, transaction(conn):
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
        migrate(conn)
        seed(conn, fixture, not production and is_loopback(conn.info))


def reset(url: str) -> None:
    """Drop every table (and the audit trail with them), then set up again from the fixture.
    Only on this machine: the connection, once made and before anything is dropped, must be to
    loopback."""
    with connect(url) as conn:
        if not is_loopback(conn.info):
            raise SystemExit("refusing to reset a database that is not on loopback")
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
