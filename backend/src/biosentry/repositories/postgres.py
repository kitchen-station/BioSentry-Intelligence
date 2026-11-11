"""The PostgreSQL store behind the API: the active run's patients, observations, notes and sessions.

Every patient is read as one `UnscoredPatient`: the patient row, the latest observation per code
(ties broken by the highest id) and the 15-minute bins. Scores, ranks, reasons and the ranked /
NOT RANKED split are never stored: the services compute them on every read with the real news2()
and arbiter, so a recorded value re-ranks the patient immediately.

One connection per request (sync handlers run in the threadpool). Writers that compute an id as
max + 1 take `pg_advisory_xact_lock(run_id)` first, so they take turns per run. The active run is
always the newest `run` row. Stored times are run time; a sim run is shifted onto the wall clock when served
(`shift` = last batch's wall time minus the run's `as_of`), a fixture run is served as stored.
"""

import hashlib
import re
import secrets
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from biosentry.core.config import DEV_DATABASE_PASSWORD, ConfigError, restricted
from biosentry.schemas import ClinicalNote, LabPanel, TierStatus, UnscoredPatient

LIVE_WITHIN = timedelta(seconds=30)  # a sim run whose last batch is older is not "receiving data"
SESSION_TTL = timedelta(hours=12)
TREND_BINS = 12
DEVICE_VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
VITALS = (*DEVICE_VITALS, "blood_pressure", "consciousness", "supplemental_o2", "spo2_scale")
LABS = tuple(name for name in LabPanel.model_fields if name != "freshness_hours")
HISTORY_VITALS = ("heart_rate", "respiratory_rate", "spo2")

Conn = psycopg.Connection[dict[str, Any]]

_VALUE: dict[str, Callable[[dict[str, Any]], Any]] = {
    "blood_pressure": lambda r: {"systolic": int(r["num"]), "diastolic": int(r["num2"])},
    "consciousness": lambda r: r["txt"],
    "supplemental_o2": lambda r: bool(r["num"]),
    "spo2_scale": lambda r: int(r["num"]),
}


def is_live(last_batch_at: datetime | None, ended: bool, now: datetime) -> bool:
    """A sim run is live until it ends or goes LIVE_WITHIN without a batch. Its creation counts as
    the first batch (it is created with last_batch_at set). A fixture run has none: never live."""
    return last_batch_at is not None and not ended and now - last_batch_at <= LIVE_WITHIN


def whole(moment: datetime) -> datetime:
    """A time as stored: UTC, whole seconds. Keeps the served time format, the retry key and
    session expiry exactly as they were."""
    return moment.astimezone(UTC).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return whole(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(url: str, production: bool | None = None) -> Conn:
    """Every connection the process makes comes through here. In production (`production`, or when
    None the BIOSENTRY_ENV of the process, where anything but `local` counts) a connection whose
    actual password is the dev one is refused, however the URL, PGPASSWORD or a passfile got it
    there."""
    # ponytail: one connection per request, opened and closed each time. Upgrade: psycopg_pool
    # when connection time shows up in the request latency.
    conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
    try:
        if (restricted() if production is None else production) and (
            conn.info.password == DEV_DATABASE_PASSWORD
        ):
            raise ConfigError("refusing to connect in production with the dev database password")
        # Not a connection option: PGTZ in the environment would override that. Served times end in Z.
        conn.execute("SET TIME ZONE 'UTC'")
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: Conn) -> Iterator[None]:
    if conn.info.transaction_status != TransactionStatus.IDLE:  # joins the caller's transaction
        yield
        return
    with conn.transaction():
        yield


def lock_run(conn: Conn, run_id: int) -> None:
    """Writers that compute max + 1 for a run take turns. Held until the transaction ends, so call
    it inside the transaction that also inserts the id."""
    # ponytail: one lock per run serializes notes, admissions and batches against each other.
    # Upgrade: a counter row (or sequence) per run and id kind if write throughput ever matters.
    assert conn.info.transaction_status != TransactionStatus.IDLE, "lock_run needs a transaction"
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (run_id,))


@dataclass(frozen=True, slots=True)
class Obs:
    """One observation, in run time. A value (num/txt) or the reason there is none (I1)."""

    patient_id: str
    code: str
    observed_at: datetime
    source: str
    quality: str = "ok"
    num: float | None = None
    num2: float | None = None
    txt: str | None = None
    absent_reason: str | None = None


# A re-entry replaces the row under its key. It also takes a fresh id (DEFAULT), so a tie on
# observed_at still goes to the newest write, as the old delete-and-insert did.
_REPLACE = (
    " ON CONFLICT (run_id, patient_id, code, observed_at, source) DO UPDATE SET id = DEFAULT,"
    " num = EXCLUDED.num, num2 = EXCLUDED.num2, txt = EXCLUDED.txt,"
    " absent_reason = EXCLUDED.absent_reason, quality = EXCLUDED.quality, tier = EXCLUDED.tier,"
    " model_id = EXCLUDED.model_id, computed_at = EXCLUDED.computed_at,"
    " synthetic = EXCLUDED.synthetic"
)


def insert_observations(
    conn: Conn, run_id: int, synthetic: bool, obs: Iterable[Obs], *, ignore: bool
) -> None:
    """`ignore`: a retried batch adds nothing. Otherwise the same key is replaced (a re-entry)."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO observation (run_id, patient_id, code, num, num2, txt, absent_reason,"
            " observed_at, source, quality, synthetic)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            + (" ON CONFLICT DO NOTHING" if ignore else _REPLACE),
            [
                (
                    run_id,
                    o.patient_id,
                    o.code,
                    o.num,
                    o.num2,
                    o.txt,
                    o.absent_reason,
                    whole(o.observed_at),
                    o.source,
                    o.quality,
                    synthetic,
                )
                for o in obs
            ],
        )


def upsert_bins(
    conn: Conn, run_id: int, bins: Iterable[tuple[str, str, datetime, float | None]]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO vital_bin (run_id, patient_id, vital, bin_at, value)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (run_id, patient_id, vital, bin_at) DO UPDATE SET value = EXCLUDED.value",
            [(run_id, pid, vital, whole(at), value) for pid, vital, at, value in bins],
        )


_PATIENT = (
    "name",
    "age",
    "sex",
    "bed",
    "admitting_context",
    "mechanism",
    "supporting",
    "trend",
    "signal_quality",
    "last_sample_at",
    "admission",
    "tier_b",
    "tier_c",
    "story",
    "admitted_at",
)


_JSON = ("admission", "tier_b", "tier_c")


def insert_patient(conn: Conn, run_id: int, patient_id: str, p: dict[str, Any]) -> None:
    """`admission`, `tier_b` and `tier_c` are JSON-ready values (dicts), not strings."""
    conn.execute(
        f"INSERT INTO patient (run_id, id, {', '.join(_PATIENT)})"
        f" VALUES (%s, %s, {', '.join(['%s'] * len(_PATIENT))})",
        [
            run_id,
            patient_id,
            *(Jsonb(p[c]) if c in _JSON and p.get(c) is not None else p.get(c) for c in _PATIENT),
        ],
    )


def assign(conn: Conn, run_id: int, patient_id: str, *clinicians: str | None) -> None:
    """Care assignments for these clinicians and, always, every charge nurse."""
    conn.execute(
        "INSERT INTO care_assignment (run_id, patient_id, clinician_id)"
        " SELECT %s, %s, id FROM clinician WHERE role = 'charge_rn' ON CONFLICT DO NOTHING",
        (run_id, patient_id),
    )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO care_assignment (run_id, patient_id, clinician_id) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(run_id, patient_id, c) for c in clinicians if c is not None],
        )


def insert_run(
    conn: Conn,
    *,
    source: str,
    seed: int | None,
    ward: str,
    origin: datetime,
    as_of: datetime,
    last_batch_at: datetime | None,
    tier_status: list[dict[str, Any]],
    client_id: str | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO run (source, synthetic, seed, ward, origin, as_of, last_batch_at, tier_status,"
        " client_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            source,
            True,  # I6: the flag stays; a future dataset of real records sets it False
            seed,
            ward,
            whole(origin),
            whole(as_of),
            whole(last_batch_at) if last_batch_at else None,
            Jsonb(tier_status),
            client_id,
        ),
    ).fetchone()
    assert row is not None
    run_id: int = row["id"]
    return run_id


def set_live_view(conn: Conn, run_id: int, view: dict[str, Any]) -> None:
    """Replaces the run's live view (a JSON-ready dict)."""
    conn.execute("UPDATE run SET live_view = %s WHERE id = %s", (Jsonb(view), run_id))


class PostgresStore:
    """The active run, through this request's connection."""

    def __init__(self, conn: Conn, clock: Callable[[], datetime]) -> None:
        # ponytail: the newest run is read once per request, so a write can land on the previous
        # run if a new one starts mid-request. Upgrade: re-check the newest id under lock_run.
        run = conn.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
        if run is None:
            raise RuntimeError("the database has no run: seed it (python -m biosentry.db.seed)")
        self.conn = conn
        self.clock = clock
        self.run_id: int = run["id"]
        self.ward: str = run["ward"]
        self.synthetic = bool(run["synthetic"])
        self.ended = bool(run["ended"])
        self.as_of: datetime = run["as_of"]
        self.last_batch_at: datetime | None = run["last_batch_at"]
        self.tier_status = [TierStatus.model_validate(t) for t in run["tier_status"]]
        # A fixture run is served in its own time. A sim run is mapped onto the wall clock.
        self.shift = self.last_batch_at - self.as_of if self.last_batch_at else timedelta(0)

    # --- time --------------------------------------------------------------------------------

    def now(self) -> datetime:
        """Ward time as served: the fixture's as_of, or the wall clock for a sim run."""
        return self.as_of if self.last_batch_at is None else self.clock()

    @property
    def run_now(self) -> datetime:
        """Ward time in run time: what a write stores."""
        # ponytail: a sim run's served times are all re-mapped under the current shift (last
        # batch's wall time minus as_of), so a time stored here assumes the sim keeps real-time
        # pace: if it is paused or its speed changes, nurse-entered times move relative to the
        # sim's own readings. Upgrade: store wall time and the run's offset at write.
        return self.now() - self.shift

    def _when(self, moment: datetime) -> datetime:
        return moment + self.shift

    def receiving_data(self) -> bool:
        return self.last_batch_at is None or is_live(self.last_batch_at, self.ended, self.clock())

    def tx(self) -> AbstractContextManager[None]:
        return transaction(self.conn)

    # --- identity and scope --------------------------------------------------------------------

    def role_of(self, clinician_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT role FROM clinician WHERE id = %s", (clinician_id,)
        ).fetchone()
        return row["role"] if row else None

    def passphrase_hash(self, clinician_id: str) -> tuple[bool, str | None]:
        """Whether this clinician exists, and their stored hash (None: no passphrase is set)."""
        row = self.conn.execute(
            "SELECT passphrase_hash FROM clinician WHERE id = %s", (clinician_id,)
        ).fetchone()
        return (row is not None, row["passphrase_hash"] if row else None)

    def assigned(self, clinician_id: str, *, including_left: bool = False) -> frozenset[str]:
        """The patients under this clinician's care. Unknown id: nobody (fails closed). Someone who
        has left the ward is out of every live view, and stays only in the stay history."""
        rows = self.conn.execute(
            "SELECT a.patient_id FROM care_assignment a JOIN patient p"
            " ON p.run_id = a.run_id AND p.id = a.patient_id"
            " WHERE a.run_id = %s AND a.clinician_id = %s AND (%s OR p.left_at IS NULL)",
            (self.run_id, clinician_id, including_left),
        )
        return frozenset(r["patient_id"] for r in rows)

    def open_session(self, clinician_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.tx():
            # SKIP LOCKED: two sign-ins never wait on, or deadlock over, the same expired rows.
            self.conn.execute(
                "DELETE FROM session WHERE token_hash IN (SELECT token_hash FROM session"
                " WHERE expires_at <= %s FOR UPDATE SKIP LOCKED)",
                (whole(now),),
            )
            self.conn.execute(
                "INSERT INTO session (token_hash, clinician_id, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    clinician_id,
                    whole(now),
                    whole(now + SESSION_TTL),
                ),
            )
        return token

    def clinician_for(self, token: str) -> str | None:
        """Whom a live session token names; None when unknown or expired."""
        row = self.conn.execute(
            "SELECT s.clinician_id FROM session s JOIN clinician c ON c.id = s.clinician_id"
            " WHERE s.token_hash = %s AND s.expires_at > %s",
            (hashlib.sha256(token.encode()).hexdigest(), whole(self.clock())),
        ).fetchone()
        return row["clinician_id"] if row else None

    # --- reading a patient ---------------------------------------------------------------------

    def _measurement(self, code: str, row: dict[str, Any] | None) -> dict[str, Any]:
        if row is None:  # nothing was ever recorded: missing is missing (I1)
            device = code in DEVICE_VITALS
            return {
                "data": {"present": False, "reason": "not_recorded"},
                "provenance": {
                    "source": "device" if device else "nurse",
                    "tier": None,
                    "computed_at": None,
                    "observed_at": self.now(),
                    "model_id": None,
                    "quality": "missing",
                    "synthetic": self.synthetic,
                },
            }
        data: dict[str, Any] = (
            {"present": False, "reason": row["absent_reason"]}
            if row["absent_reason"] is not None
            else {"present": True, "value": _VALUE.get(code, lambda r: r["num"])(row)}
        )
        return {
            "data": data,
            "provenance": {
                "source": row["source"],
                "tier": row["tier"],
                "observed_at": self._when(row["observed_at"]),
                "computed_at": self._when(row["computed_at"]) if row["computed_at"] else None,
                "model_id": row["model_id"],
                "synthetic": bool(row["synthetic"]),
                "quality": row["quality"],
            },
        }

    def read(self, patient_id: str) -> UnscoredPatient | None:
        # ponytail: the reads below are not one snapshot, so a batch landing mid-read can mix two
        # sim minutes in one patient. Upgrade: a read transaction (BEGIN) around each request.
        p = self.conn.execute(
            "SELECT * FROM patient WHERE run_id = %s AND id = %s", (self.run_id, patient_id)
        ).fetchone()
        if p is None:
            return None
        latest = {
            r["code"]: r
            for r in self.conn.execute(
                "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY code"
                " ORDER BY observed_at DESC, id DESC) AS n FROM observation"
                " WHERE run_id = %s AND patient_id = %s) AS latest WHERE n = 1",
                (self.run_id, patient_id),
            )
        }
        bins: dict[str, list[tuple[datetime, float | None]]] = defaultdict(list)
        for b in self.conn.execute(
            "SELECT vital, bin_at, value FROM vital_bin WHERE run_id = %s AND patient_id = %s"
            " ORDER BY bin_at",
            (self.run_id, patient_id),
        ):
            bins[b["vital"]].append((b["bin_at"], b["value"]))

        run_now = self.run_now
        ages = [
            max(0.0, (run_now - r["observed_at"]).total_seconds()) / 3600
            for code, r in latest.items()
            if code in LABS and r["absent_reason"] is None
        ]
        labs: dict[str, Any] = {
            name: self._measurement(name, latest[name]) if name in latest else None for name in LABS
        }
        labs["freshness_hours"] = round(max(ages), 1) if ages else None
        sample = p["last_sample_at"]
        return UnscoredPatient.model_validate(
            {
                "id": patient_id,
                "name": p["name"],
                "age": p["age"],
                "sex": p["sex"],
                "bed": p["bed"],
                "admitting_context": p["admitting_context"],
                "trend": p["trend"],
                "mechanism": p["mechanism"],
                "supporting": p["supporting"],
                "vitals": {v: self._measurement(v, latest.get(v)) for v in VITALS},
                "vital_trends": {
                    v: [value for _, value in bins[v][-TREND_BINS:]] for v in DEVICE_VITALS
                },
                "vital_history": {
                    v: [{"time": self._when(at), "value": value} for at, value in bins[v]]
                    for v in HISTORY_VITALS
                },
                "labs": labs,
                "tier_b": p["tier_b"],
                "tier_c": p["tier_c"],
                "signal_quality": p["signal_quality"],
                "last_update_s": (
                    max(0.0, (run_now - sample).total_seconds()) if sample else None
                ),
                "synthetic": self.synthetic,
            }
        )

    def stays(self, ids: frozenset[str]) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, name, bed, admitted_at, left_at, outcome FROM patient"
            " WHERE run_id = %s AND id = ANY(%s) ORDER BY id",
            (self.run_id, sorted(ids)),
        )
        return [
            {
                **r,
                "admitted_at": self._when(r["admitted_at"]) if r["admitted_at"] else None,
                "left_at": self._when(r["left_at"]) if r["left_at"] else None,
                "synthetic": self.synthetic,
            }
            for r in rows
        ]

    def live_view(self) -> dict[str, Any] | None:
        """The view the sim last published for this run, unscoped. None: nothing published."""
        row = self.conn.execute(
            "SELECT live_view FROM run WHERE id = %s", (self.run_id,)
        ).fetchone()
        assert row is not None  # the run was read when this store was made
        view: dict[str, Any] | None = row["live_view"]
        return view

    # --- writing -------------------------------------------------------------------------------

    def record(self, observations: Iterable[Obs]) -> None:
        """Store observations in one transaction. The run, not the client, says if they are synthetic."""
        with self.tx():
            insert_observations(self.conn, self.run_id, self.synthetic, observations, ignore=False)

    def next_patient_id(self) -> str:
        """The next free id. Call it inside the transaction that inserts the patient."""
        lock_run(self.conn, self.run_id)
        rows = self.conn.execute("SELECT id FROM patient WHERE run_id = %s", (self.run_id,))
        numbers = [int(m[1]) for r in rows if (m := re.fullmatch(r"p-(\d+)", r["id"]))]
        return f"p-{max(numbers, default=0) + 1:03d}"

    def add_patient(self, patient_id: str, clinician_id: str, fields: dict[str, Any]) -> None:
        with self.tx():
            insert_patient(self.conn, self.run_id, patient_id, fields)
            assign(self.conn, self.run_id, patient_id, clinician_id)

    # --- notes ---------------------------------------------------------------------------------

    def notes(self, patient_id: str) -> list[ClinicalNote]:
        rows = self.conn.execute(
            "SELECT * FROM clinical_note WHERE run_id = %s AND patient_id = %s AND deleted_at IS NULL"
            " ORDER BY seq DESC",
            (self.run_id, patient_id),
        )
        return [
            ClinicalNote.model_validate(
                {
                    **{k: r[k] for k in ("id", "patient_id", "author", "role", "content")},
                    "timestamp": self._when(r["timestamp"]),
                    "synthetic": bool(r["synthetic"]),
                }
            )
            for r in rows
        ]

    def add_note(self, patient_id: str, author: str, role: str, content: str) -> ClinicalNote:
        with self.tx():
            lock_run(self.conn, self.run_id)
            rows = self.conn.execute(
                "SELECT id FROM clinical_note WHERE run_id = %s", (self.run_id,)
            )
            number = max((int(r["id"].removeprefix("n-")) for r in rows), default=0) + 1
            note_id = f"n-{number:03d}"
            self.conn.execute(
                "INSERT INTO clinical_note (run_id, id, patient_id, author, role, timestamp, content,"
                " synthetic) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    self.run_id,
                    note_id,
                    patient_id,
                    author,
                    role,
                    whole(self.run_now),
                    content,
                    self.synthetic,
                ),
            )
        return next(n for n in self.notes(patient_id) if n.id == note_id)

    def delete_note(self, patient_id: str, note_id: str) -> None:
        self.conn.execute(
            "UPDATE clinical_note SET deleted_at = %s WHERE run_id = %s AND patient_id = %s AND id = %s"
            " AND deleted_at IS NULL",
            (whole(self.run_now), self.run_id, patient_id, note_id),
        )

    # --- acknowledgements ----------------------------------------------------------------------

    def acknowledgements(self, clinician_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT patient_id, score FROM acknowledgement WHERE run_id = %s AND clinician_id = %s"
            " ORDER BY patient_id",
            (self.run_id, clinician_id),
        )
        return [{"patient_id": r["patient_id"], "score": r["score"]} for r in rows]

    def acknowledge(self, clinician_id: str, patient_id: str, score: int) -> None:
        self.conn.execute(
            "INSERT INTO acknowledgement (run_id, patient_id, clinician_id, score, at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (run_id, patient_id, clinician_id) DO UPDATE"
            " SET score = EXCLUDED.score, at = EXCLUDED.at",
            (self.run_id, patient_id, clinician_id, score, whole(self.run_now)),
        )
