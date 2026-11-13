"""The simulator's episode truth (contract-truth v1.1). It exists only for a simulated run. ONLY the
history route (`services/history.py`, through `api/app.py`) may import this module: nothing that
ranks, scores or feeds a model reads the table."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from biosentry.repositories.postgres import Conn, PostgresStore, transaction
from biosentry.schemas.episodes import Episodes

from .ingest import _at, _locked_newest
from .patients import ConflictError, RejectedError

ALLOWANCE = 15  # run minutes a start may lie after the newest minute received


@dataclass(frozen=True, slots=True)
class Started:
    kind: str
    start: datetime  # served times
    full: datetime


def store_episodes(conn: Conn, run_id: int, body: Episodes, clock: Callable[[], datetime]) -> bool:
    """False when there is no such run. Checks and stores under the run's lock, as a batch does.
    The same values again change nothing, `received_at` included; other values replace."""
    with transaction(conn):
        run = _locked_newest(conn, run_id)
        if run is None:
            return False
        if run["source"] != "synthetic":
            raise ConflictError("The run is not marked synthetic")
        members = {
            r["id"] for r in conn.execute("SELECT id FROM patient WHERE run_id = %s", (run_id,))
        }
        if any(e.patient_id not in members for e in body.episodes):
            raise RejectedError("a patient is not in this run")
        newest = (run["as_of"] - run["origin"]) // timedelta(minutes=1)
        if any(e.start_minute > newest + ALLOWANCE for e in body.episodes):
            raise RejectedError("an episode starts too far after the newest minute received")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sim_episode (run_id, patient_id, kind, start_minute, full_minute,"
                " received_at) VALUES (%(run)s, %(patient)s, %(kind)s, %(start)s, %(full)s, %(at)s)"
                " ON CONFLICT (run_id, patient_id) DO UPDATE SET kind = EXCLUDED.kind,"
                " start_minute = EXCLUDED.start_minute, full_minute = EXCLUDED.full_minute,"
                " received_at = EXCLUDED.received_at"
                " WHERE (sim_episode.kind, sim_episode.start_minute, sim_episode.full_minute)"
                " IS DISTINCT FROM (EXCLUDED.kind, EXCLUDED.start_minute, EXCLUDED.full_minute)",
                [
                    {
                        "run": run_id,
                        "patient": e.patient_id,
                        "kind": e.kind,
                        "start": e.start_minute,
                        "full": e.full_minute,
                        "at": clock(),
                    }
                    for e in body.episodes
                ],
            )
    return True


def started(store: PostgresStore, patient_id: str) -> Started | None:
    """The patient's episode of the store's run, only when it has begun on the run's own clock: a
    start after the run's current minute is never shown."""
    row = store.conn.execute(
        "SELECT e.kind, e.start_minute, e.full_minute, r.origin FROM sim_episode e"
        " JOIN run r ON r.id = e.run_id WHERE e.run_id = %s AND e.patient_id = %s"
        " AND r.origin + e.start_minute * interval '1 minute' <= r.as_of",
        (store.run_id, patient_id),
    ).fetchone()
    if row is None:
        return None
    origin = row["origin"]
    return Started(
        row["kind"],
        _at(origin, row["start_minute"]) + store.shift,
        _at(origin, row["full_minute"]) + store.shift,
    )
