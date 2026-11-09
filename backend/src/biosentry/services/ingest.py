"""The ward simulation's writes: start a run, append a batch. Each is one transaction.

The run, never the client, says whether data is synthetic. A body that is refused writes nothing.
One shared ward: the newest run is the ward. A different client cannot start a run while the
newest is live (ConflictError), and a batch for a run that is no longer the newest is refused the
same way, so a superseded sim stops writing.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from biosentry.db.migrate import SETUP_LOCK
from biosentry.repositories.postgres import (
    Conn,
    Obs,
    assign,
    insert_observations,
    insert_patient,
    insert_run,
    is_live,
    lock_run,
    transaction,
    upsert_bins,
    whole,
)
from biosentry.schemas.ingest import Batch, Bin, NewRun
from biosentry.schemas.ingest import Obs as IngestObs

from .patients import RejectedError

class ConflictError(Exception):
    """The ward's state refuses this write (409). Nothing was written."""


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


def start_run(conn: Conn, body: NewRun, clock: Callable[[], datetime]) -> int:
    known = {r["id"] for r in conn.execute("SELECT id FROM clinician")}
    for p in body.patients:
        if p.rn not in known or (p.md is not None and p.md not in known):
            raise RejectedError("rn and md must be known clinicians")
    origin = body.origin
    with transaction(conn):
        # Runs are created under the setup lock (as the fixture is), so two registrations take
        # turns and the second sees the first. Then the newest run's lock: a batch in flight on it
        # finishes first, so its last_batch_at is what is judged below.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
        newest = conn.execute("SELECT id FROM run ORDER BY id DESC LIMIT 1").fetchone()
        if newest is not None:
            lock_run(conn, newest["id"])
            run = conn.execute("SELECT * FROM run WHERE id = %s", (newest["id"],)).fetchone()
            assert run is not None
            live = is_live(run["last_batch_at"], run["ended"], clock())
            if live and run["client_id"] != body.client_id:
                raise ConflictError("Another ward simulation is live")
        run_id = insert_run(
            conn,
            source=body.source,
            seed=body.seed,
            ward=WARD,
            origin=origin,
            as_of=origin,
            last_batch_at=clock(),  # a new run is live from creation
            tier_status=TIER_STATUS,
            client_id=body.client_id,
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
                    "last_sample_at": _at(origin, max(device)) if device else None,
                },
            )
            assign(conn, run_id, p.id, p.rn, p.md)
            insert_observations(
                conn,
                run_id,
                True,
                [_obs(origin, o) for o in p.observations],
                ignore=True,
            )
            upsert_bins(conn, run_id, [_bin(origin, b) for b in p.bins])
    return run_id


def add_batch(conn: Conn, run_id: int, body: Batch, clock: Callable[[], datetime]) -> bool:
    """False when there is no such run. A patient outside the run is refused (RejectedError)."""
    with transaction(conn):
        # A batch holds the run's lock from its first read to its commit: two batches on a run take
        # turns (the sim_event seq is max + 1, and the patient and run rows are updated in order).
        lock_run(conn, run_id)
        run = conn.execute("SELECT * FROM run WHERE id = %s", (run_id,)).fetchone()
        if run is None:
            return False
        newest = conn.execute("SELECT max(id) AS id FROM run").fetchone()
        assert newest is not None  # an aggregate always answers
        if newest["id"] != run_id:
            raise ConflictError("This run has been replaced by a newer one")
        origin = run["origin"]
        members = {
            r["id"] for r in conn.execute("SELECT id FROM patient WHERE run_id = %s", (run_id,))
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
            sample = None if p.last_sample_minute is None else _at(origin, p.last_sample_minute)
            conn.execute(
                "UPDATE patient SET trend = %s, signal_quality = %s, last_sample_at = %s"
                " WHERE run_id = %s AND id = %s",
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
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS last FROM sim_event WHERE run_id = %s", (run_id,)
        ).fetchone()
        assert row is not None
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sim_event (run_id, seq, minute, kind, patient_id, staff_id, text)"
                " SELECT %(run)s, %(seq)s, %(minute)s, %(kind)s, %(patient)s::text,"
                " %(staff)s::text, %(text)s"
                " WHERE NOT EXISTS (SELECT 1 FROM sim_event"
                " WHERE run_id = %(run)s AND minute = %(minute)s AND kind = %(kind)s"
                " AND patient_id IS NOT DISTINCT FROM %(patient)s"
                " AND staff_id IS NOT DISTINCT FROM %(staff)s AND text = %(text)s)",
                # a retried batch adds no events
                [
                    {
                        "run": run_id,
                        "seq": row["last"] + i,
                        "minute": e.minute,
                        "kind": e.kind,
                        "patient": e.patient_id,
                        "staff": e.staff_id,
                        "text": e.text,
                    }
                    for i, e in enumerate(body.events, 1)
                ],
            )
        conn.execute(
            "UPDATE run SET as_of = %s, last_batch_at = %s, ended = %s, received = %s,"
            " rejected = %s, abstained = %s WHERE id = %s",
            (
                whole(_at(origin, body.minute)),
                whole(clock()),
                body.ended,
                body.counts.received,
                body.counts.rejected,
                body.counts.abstained,
                run_id,
            ),
        )
    return True
