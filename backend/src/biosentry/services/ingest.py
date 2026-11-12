"""The ward simulation's writes: start a run, append a batch, replace the live view. Each is one
transaction. And its two reads: the clinician actions and the viewer's commands it has not
applied yet.

The run, never the client, says whether data is synthetic. A body that is refused writes nothing.
One shared ward: the newest run is the ward. A different client cannot start a run while the
newest is live (ConflictError), and a batch for a run that is no longer the newest is refused the
same way (as is its live view), so a superseded sim stops writing.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from biosentry.core.audit import AuditLog
from biosentry.db.migrate import SETUP_LOCK
from biosentry.repositories.postgres import (
    Conn,
    Obs,
    PostgresStore,
    assign,
    insert_observations,
    insert_patient,
    insert_run,
    is_live,
    lock_run,
    set_live_view,
    transaction,
    upsert_bins,
    whole,
)
from biosentry.schemas.ingest import Batch, Bin, LiveView, NewCommand, NewRun, NewRunPatient
from biosentry.schemas.ingest import Obs as IngestObs

from .patients import ConflictError, RejectedError

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


def _check_clinicians(conn: Conn, patients: Iterable[NewRunPatient]) -> None:
    known = {r["id"] for r in conn.execute("SELECT id FROM clinician")}
    for p in patients:
        if p.rn not in known or (p.md is not None and p.md not in known):
            raise RejectedError("rn and md must be known clinicians")


def _add_patient(
    conn: Conn, run_id: int, origin: datetime, p: NewRunPatient, admitted_at: datetime | None
) -> None:
    device = [o.minute for o in p.observations if o.source == "device" and o.absent_reason is None]
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
            "admitted_at": admitted_at,
        },
    )
    assign(conn, run_id, p.id, p.rn, p.md)
    insert_observations(
        conn, run_id, True, [_obs(origin, o) for o in p.observations], ignore=True
    )
    upsert_bins(conn, run_id, [_bin(origin, b) for b in p.bins])


def start_run(conn: Conn, body: NewRun, clock: Callable[[], datetime]) -> int:
    _check_clinicians(conn, body.patients)
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
            _add_patient(conn, run_id, origin, p, None)
    return run_id


def _locked_newest(conn: Conn, run_id: int) -> dict[str, Any] | None:
    """Takes the run's lock and reads the run. None: no such run. Not the newest: ConflictError."""
    lock_run(conn, run_id)
    run = conn.execute("SELECT * FROM run WHERE id = %s", (run_id,)).fetchone()
    if run is None:
        return None
    newest = conn.execute("SELECT max(id) AS id FROM run").fetchone()
    assert newest is not None  # an aggregate always answers
    if newest["id"] != run_id:
        raise ConflictError("This run has been replaced by a newer one")
    return run


def add_batch(
    conn: Conn, run_id: int, body: Batch, clock: Callable[[], datetime], retention_days: int
) -> bool:
    """False when there is no such run. A patient outside the run is refused (RejectedError).
    A batch that moves the run into a new sim hour prunes what is older than `retention_days`."""
    with transaction(conn):
        # A batch holds the run's lock from its first read to its commit: two batches on a run take
        # turns (the sim_event seq is max + 1, and the patient and run rows are updated in order).
        run = _locked_newest(conn, run_id)
        if run is None:
            return False
        origin = run["origin"]
        members = {
            r["id"] for r in conn.execute("SELECT id FROM patient WHERE run_id = %s", (run_id,))
        }
        _check_clinicians(conn, body.admissions)
        for a in body.admissions:
            if a.id not in members:  # a retried batch adds nobody twice
                _add_patient(conn, run_id, origin, a, whole(_at(origin, a.minute)))
                members.add(a.id)
        named = {
            *(d.patient_id for d in body.departures),
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
        for d in body.departures:
            conn.execute(
                "UPDATE patient SET left_at = %s, outcome = %s"
                " WHERE run_id = %s AND id = %s AND left_at IS NULL",
                (whole(_at(origin, d.minute)), d.outcome, run_id, d.patient_id),
            )
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
        as_of = whole(_at(origin, body.minute))
        if as_of.replace(minute=0, second=0) != run["as_of"].replace(minute=0, second=0):
            cutoff = as_of - timedelta(days=retention_days)
            conn.execute("DELETE FROM vital_bin WHERE run_id = %s AND bin_at < %s", (run_id, cutoff))
            # The read takes the newest row per patient and code, and the sim sends a value only
            # when it changes: that row is the current value whatever its age, so it is kept.
            conn.execute(
                "DELETE FROM observation AS o WHERE o.run_id = %s AND o.observed_at < %s"
                " AND EXISTS (SELECT 1 FROM observation AS n WHERE n.run_id = o.run_id"
                " AND n.patient_id = o.patient_id AND n.code = o.code"
                " AND (n.observed_at, n.id) > (o.observed_at, o.id))",
                (run_id, cutoff),
            )
        conn.execute(
            "UPDATE run SET as_of = %s, last_batch_at = %s, ended = %s, received = %s,"
            " rejected = %s, abstained = %s WHERE id = %s",
            (
                as_of,
                whole(clock()),
                body.ended,
                body.counts.received,
                body.counts.rejected,
                body.counts.abstained,
                run_id,
            ),
        )
    return True


def set_view(conn: Conn, run_id: int, body: LiveView) -> bool:
    """Replaces the run's live view. False when there is no such run."""
    with transaction(conn):
        if _locked_newest(conn, run_id) is None:
            return False
        set_live_view(conn, run_id, body.model_dump())
    return True


def actions(store: PostgresStore, run_id: int, after: int) -> list[dict[str, Any]] | None:
    """The run's clinician actions after `after`. None when there is no such run."""
    with transaction(store.conn):  # under the run's lock: a write in flight commits first
        if _locked_newest(store.conn, run_id) is None:
            return None
        return store.actions(run_id, after)


def commands(store: PostgresStore, run_id: int, after: int) -> list[dict[str, Any]] | None:
    """The run's viewer commands after `after`. None when there is no such run."""
    with transaction(store.conn):  # under the run's lock, as `actions`
        if _locked_newest(store.conn, run_id) is None:
            return None
        return store.commands(run_id, after)


def add_command(store: PostgresStore, audit: AuditLog, who: str, body: NewCommand) -> bool:
    """Queue a command for the sim. False when the patient is not the caller's. A ward that is not
    receiving data from a live sim run (the fixture, or a run that stopped) is a ConflictError:
    nothing would read the command."""
    resource = body.patient_id or store.ward
    if body.patient_id is not None and body.patient_id not in store.assigned(who):
        audit.append(who, "sim_command", resource, "not_found")
        return False
    if not is_live(store.last_batch_at, store.ended, store.clock()):
        audit.append(who, "sim_command", resource, "rejected")
        raise ConflictError("The ward is not receiving data from a running simulation")
    data = {} if body.speed is None else {"speed": body.speed}
    with store.tx():
        store.add_command(body.kind, who, body.patient_id, data)
    audit.append(who, "sim_command", resource, "ok")
    return True
