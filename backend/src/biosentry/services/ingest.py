"""The ward simulation's writes: start a run, append a batch. Each is one transaction.

The run, never the client, says whether data is synthetic. A body that is refused writes nothing.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from biosentry.repositories.sqlite import (
    Obs,
    assign,
    insert_observations,
    insert_patient,
    insert_run,
    iso,
    parse,
    transaction,
    upsert_bins,
)
from biosentry.schemas.ingest import Batch, Bin, NewRun
from biosentry.schemas.ingest import Obs as IngestObs

from .patients import RejectedError

WARD = "W4"
MECHANISM = "Sorted on NEWS2 (Tier A). Live from the ward simulation."
TIER_STATUS = [{"tier": "A", "label": "NEWS2", "live": True}]


def _at(origin: datetime, minute: int) -> datetime:
    return origin + timedelta(minutes=minute)


def _obs(origin: datetime, o: IngestObs) -> Obs:
    return Obs(
        o.patient_id,
        o.code,
        _at(origin, o.minute),
        o.source,
        o.quality,
        o.num,
        o.num2,
        o.txt,
        o.absent_reason,
    )


def _bin(origin: datetime, b: Bin) -> tuple[str, str, datetime, float | None]:
    return (b.patient_id, b.vital, _at(origin, b.minute), b.value)


def start_run(conn: sqlite3.Connection, body: NewRun, clock: Callable[[], datetime]) -> int:
    known = {r["id"] for r in conn.execute("SELECT id FROM clinician")}
    for p in body.patients:
        if p.rn not in known or (p.md is not None and p.md not in known):
            raise RejectedError("rn and md must be known clinicians")
    origin = body.origin
    with transaction(conn):
        run_id = insert_run(
            conn,
            source=body.source,
            seed=body.seed,
            ward=WARD,
            origin=origin,
            as_of=origin,
            last_batch_at=clock(),
            tier_status=TIER_STATUS,
        )
        for p in body.patients:
            device = [
                o.minute for o in p.observations if o.source == "device" and o.absent_reason is None
            ]
            insert_patient(
                conn,
                run_id,
                p.id,
                {
                    "name": p.name,
                    "age": p.age,
                    "sex": p.sex,
                    "bed": p.bed,
                    "admitting_context": p.context,
                    "mechanism": MECHANISM,
                    "supporting": "",
                    "trend": p.trend,
                    "signal_quality": p.signal_quality,
                    "story": p.story,
                    "last_sample_at": iso(_at(origin, max(device))) if device else None,
                },
            )
            assign(conn, run_id, p.id, p.rn, p.md)
            insert_observations(
                conn,
                run_id,
                body.source != "eicu_demo",
                [_obs(origin, o) for o in p.observations],
                ignore=True,
            )
            upsert_bins(conn, run_id, [_bin(origin, b) for b in p.bins])
    return run_id


def add_batch(
    conn: sqlite3.Connection, run_id: int, body: Batch, clock: Callable[[], datetime]
) -> bool:
    """False when there is no such run. A patient outside the run is refused (RejectedError)."""
    with transaction(conn):
        run = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            return False
        origin = parse(run["origin"])
        members = {
            r["id"] for r in conn.execute("SELECT id FROM patient WHERE run_id = ?", (run_id,))
        }
        named = {
            *(p.id for p in body.patients),
            *(o.patient_id for o in body.observations),
            *(b.patient_id for b in body.bins),
            *(e.patient_id for e in body.events if e.patient_id is not None),
        }
        if named - members:
            raise RejectedError("a patient is not in this run")
        for p in body.patients:
            sample = (
                None if p.last_sample_minute is None else iso(_at(origin, p.last_sample_minute))
            )
            conn.execute(
                "UPDATE patient SET trend = ?, signal_quality = ?, last_sample_at = ?"
                " WHERE run_id = ? AND id = ?",
                (p.trend, p.signal_quality, sample, run_id, p.id),
            )
        insert_observations(
            conn,
            run_id,
            bool(run["synthetic"]),
            [_obs(origin, o) for o in body.observations],
            ignore=True,
        )
        upsert_bins(conn, run_id, [_bin(origin, b) for b in body.bins])
        last = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM sim_event WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO sim_event (run_id, seq, minute, kind, patient_id, staff_id, text)"
            " SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7 WHERE NOT EXISTS (SELECT 1 FROM sim_event"
            " WHERE run_id = ?1 AND minute = ?3 AND kind = ?4 AND patient_id IS ?5"
            " AND staff_id IS ?6 AND text = ?7)",  # a retried batch adds no events
            [
                (run_id, last + i, e.minute, e.kind, e.patient_id, e.staff_id, e.text)
                for i, e in enumerate(body.events, 1)
            ],
        )
        conn.execute(
            "UPDATE run SET as_of = ?, last_batch_at = ?, ended = ?, received = ?, rejected = ?,"
            " abstained = ? WHERE id = ?",
            (
                iso(_at(origin, body.minute)),
                iso(clock()),
                int(body.ended),
                body.counts.received,
                body.counts.rejected,
                body.counts.abstained,
                run_id,
            ),
        )
    return True
