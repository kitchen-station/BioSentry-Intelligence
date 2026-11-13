"""The PostgreSQL store behind the API: the active run's patients, observations, notes and sessions.

Every patient is read as one `UnscoredPatient`: the patient row, the latest observation per code
(ties broken by the highest id; for the four device vitals a valid nurse value can count
instead, see `_newest_device_or_nurse`) and the 15-minute bins. Scores, ranks, reasons and the
ranked / NOT RANKED split are never stored: the services compute them on every read with the
real news2() and arbiter, so a recorded value re-ranks the patient immediately.

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
from biosentry.schemas import ClinicalNote, LabPanel, TierStatus, UnscoredPatient, VitalHistory

LIVE_WITHIN = timedelta(seconds=30)  # a sim run whose last batch is older is not "receiving data"
CONTROL_ROLES = ("md", "charge_rn")  # the roles that may send commands to the ward simulation
ADMIN_ROLE = "admin"  # reads the ward and its patients, writes nothing, is never assigned
SESSION_TTL = timedelta(hours=12)
# judgement: RCP NEWS2 (2017) chart, "minimum 12 hourly" at aggregate 0, used for every score.
# A nurse-typed device value counts for this long. It is the nurse's own word, not a patch reading.
NURSE_VALID = timedelta(hours=12)
OUT_OF_DATE = "out_of_date"
TREND_BINS = 12
DEVICE_VITALS = ("heart_rate", "respiratory_rate", "spo2", "temperature")
VITALS = (*DEVICE_VITALS, "blood_pressure", "consciousness", "supplemental_o2", "spo2_scale")
LABS = tuple(name for name in LabPanel.model_fields if name != "freshness_hours")
HISTORY_VITALS = ("heart_rate", "respiratory_rate", "spo2")
CHARTED_VITALS = ("blood_pressure", "consciousness", "supplemental_o2", "spo2_scale")

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
class StoredObservation:
    """One stored observation as the models' as-of rebuild reads it, in served time."""

    patient_id: str
    code: str
    source: str
    observed_at: datetime
    id: int
    quality: str
    num: float | None
    num2: float | None
    txt: str | None
    absent_reason: str | None


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

    def can_control(self, clinician_id: str) -> bool:
        """Whether this clinician may send the sim commands: the one place that rule is written."""
        return self.role_of(clinician_id) in CONTROL_ROLES

    def is_admin(self, clinician_id: str) -> bool:
        """Whether this clinician is an admin: the one place that rule is written."""
        return self.role_of(clinician_id) == ADMIN_ROLE

    def on_ward(self) -> frozenset[str]:
        """Every patient still on the ward, whoever is assigned. For the admin's read-only views."""
        rows = self.conn.execute(
            "SELECT id FROM patient WHERE run_id = %s AND left_at IS NULL", (self.run_id,)
        )
        return frozenset(r["id"] for r in rows)

    def care(self, ids: frozenset[str]) -> dict[str, list[str]]:
        """The clinicians assigned to each of these patients, ids sorted."""
        out: dict[str, list[str]] = {pid: [] for pid in ids}
        rows = self.conn.execute(
            "SELECT patient_id, clinician_id FROM care_assignment"
            " WHERE run_id = %s AND patient_id = ANY(%s) ORDER BY clinician_id",
            (self.run_id, sorted(ids)),
        )
        for r in rows:
            out[r["patient_id"]].append(r["clinician_id"])
        return out

    def clinicians(self) -> list[tuple[str, str]]:
        """Every clinician but an admin, as (id, role), by id."""
        # shortcut: one ward per database today, so every clinician belongs to it; scope by
        # ward when clinicians carry one.
        rows = self.conn.execute(
            "SELECT id, role FROM clinician WHERE role <> %s ORDER BY id", (ADMIN_ROLE,)
        )
        return [(r["id"], r["role"]) for r in rows]

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

    def _newest_device_or_nurse(
        self, rows: list[dict[str, Any]], sample: datetime | None
    ) -> dict[str, Any]:
        """The row that counts for a device vital: a valid nurse value, else the device's row.

        `rows` holds the newest row of each kind for one code: a nurse value, another value, an
        absent row.
        """

        def key(r: dict[str, Any]) -> tuple[datetime, int]:
            return (r["observed_at"], r["id"])

        nurse = next(
            (r for r in rows if r["source"] == "nurse" and r["absent_reason"] is None), None
        )
        others = [r for r in rows if r is not nurse]
        other = max(others, key=key) if others else None
        if nurse is None:
            assert other is not None
            return other
        newer = other is None or key(nurse) > key(other)
        # A device value written after the nurse value replaced it for good, even if the patch
        # then drops out: the nurse value does not come back.
        replaced = any(r["absent_reason"] is None and key(r) > key(nurse) for r in others)
        valid = self.run_now - nurse["observed_at"] <= NURSE_VALID
        # sampled_since: the simulator sends a device row only on change, so an unchanged live
        # value keeps an old time. A later sample means the patch is live.
        sampled_since = sample is not None and sample > nurse["observed_at"]
        other_has_value = other is not None and other["absent_reason"] is None
        if valid and not replaced and (not other_has_value or not sampled_since):
            return nurse
        # An older device value counts again only while the patch is sampling: an expired nurse
        # value never falls back to a still older reading.
        if other_has_value and (not newer or sampled_since):
            assert other is not None
            return other
        if newer:
            return {**nurse, "absent_reason": OUT_OF_DATE, "quality": "missing"}
        assert other is not None
        return other

    def read(self, patient_id: str) -> UnscoredPatient | None:
        # ponytail: the reads below are not one snapshot, so a batch landing mid-read can mix two
        # sim minutes in one patient. Upgrade: a read transaction (BEGIN) around each request.
        p = self.conn.execute(
            "SELECT * FROM patient WHERE run_id = %s AND id = %s", (self.run_id, patient_id)
        ).fetchone()
        if p is None:
            return None
        by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY code,"
            " (source = 'nurse'), (absent_reason IS NULL)"
            " ORDER BY observed_at DESC, id DESC) AS n FROM observation"
            " WHERE run_id = %s AND patient_id = %s) AS latest WHERE n = 1",
            (self.run_id, patient_id),
        ):
            by_code[r["code"]].append(r)
        sample = p["last_sample_at"]
        latest = {
            code: (
                self._newest_device_or_nurse(rows, sample)
                if code in DEVICE_VITALS
                else max(rows, key=lambda r: (r["observed_at"], r["id"]))
            )
            for code, rows in by_code.items()
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
                "admitted_at": self._when(p["admitted_at"]) if p["admitted_at"] else None,
            }
        )

    def temperature_bins(self, ids: Iterable[str]) -> dict[str, list[VitalHistory]]:
        """The stored temperature bins of these patients, in bin order, in ONE statement. For the
        models only: temperature is not in HISTORY_VITALS and not in any response."""
        out: dict[str, list[VitalHistory]] = defaultdict(list)
        for r in self.conn.execute(
            "SELECT patient_id, bin_at, value FROM vital_bin WHERE run_id = %s"
            " AND patient_id = ANY(%s) AND vital = 'temperature' ORDER BY patient_id, bin_at",
            (self.run_id, sorted(set(ids))),
        ):
            out[r["patient_id"]].append(
                VitalHistory.model_construct(time=self._when(r["bin_at"]), value=r["value"])
            )
        return dict(out)

    def observations_between(
        self, windows: Iterable[tuple[str, datetime, datetime]]
    ) -> dict[str, list[StoredObservation]]:
        """For each (patient, from, to) in served time, in ONE statement: every row of the four
        charted codes and every nurse-typed row of the four device codes with
        from <= observed_at <= to (a device's own rows are not needed: the bins hold them), plus, for each charted
        code, the newest row before `from` (the as-of rule has no age limit). Per patient, oldest
        first by (observed_at, id). Served by `observation_latest`."""
        wins = sorted(windows)
        if not wins:
            return {}
        pids = [w[0] for w in wins]
        lows = [w[1] - self.shift for w in wins]
        highs = [w[2] - self.shift for w in wins]
        cols = "o.patient_id, o.code, o.source, o.observed_at, o.id, o.quality, o.num, o.num2, o.txt, o.absent_reason"
        out: dict[str, list[StoredObservation]] = defaultdict(list)
        for r in self.conn.execute(
            "WITH w AS (SELECT * FROM unnest(%(p)s::text[], %(lo)s::timestamptz[], %(hi)s::timestamptz[])"
            " AS w(patient_id, lo, hi))"
            f" SELECT {cols} FROM w JOIN observation o ON o.run_id = %(run)s"
            " AND o.patient_id = w.patient_id AND o.code = ANY(%(all)s)"
            " AND (o.code = ANY(%(charted)s) OR o.source = 'nurse')"
            " AND o.observed_at >= w.lo AND o.observed_at <= w.hi"
            f" UNION ALL SELECT {cols} FROM w CROSS JOIN unnest(%(charted)s::text[]) AS c(code)"
            f" CROSS JOIN LATERAL (SELECT {cols} FROM observation o WHERE o.run_id = %(run)s"
            " AND o.patient_id = w.patient_id AND o.code = c.code AND o.observed_at < w.lo"
            " ORDER BY o.observed_at DESC, o.id DESC LIMIT 1) AS o",
            {
                "p": pids,
                "lo": lows,
                "hi": highs,
                "run": self.run_id,
                "all": [*CHARTED_VITALS, *DEVICE_VITALS],
                "charted": list(CHARTED_VITALS),
            },
        ):
            out[r["patient_id"]].append(
                StoredObservation(**{**r, "observed_at": self._when(r["observed_at"])})
            )
        for rows in out.values():
            rows.sort(key=lambda o: (o.observed_at, o.id))
        return dict(out)

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

    def bed_occupied(self, bed: str) -> bool:
        """Whether a patient still in the ward has this bed. Call it after `next_patient_id`,
        which holds the run's lock, so two admissions to one bed cannot both pass."""
        row = self.conn.execute(
            "SELECT 1 FROM patient WHERE run_id = %s AND left_at IS NULL"
            " AND lower(btrim(bed)) = lower(%s) LIMIT 1",
            (self.run_id, bed.strip()),
        ).fetchone()
        return row is not None

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

    # --- the feed the ward sim reads -----------------------------------------------------------

    def add_action(
        self, kind: str, clinician_id: str, patient_id: str, data: dict[str, Any]
    ) -> None:
        """Append to the feed. Call it first in the transaction of the write it describes: it takes
        the run's lock, so seq order is commit order and a reader's cursor never skips a row."""
        lock_run(self.conn, self.run_id)
        self.conn.execute(
            "INSERT INTO clinician_action (run_id, kind, patient_id, clinician_id, at, data)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (self.run_id, kind, patient_id, clinician_id, whole(self.run_now), Jsonb(data)),
        )

    def actions(self, run_id: int, after: int) -> list[dict[str, Any]]:
        """The next 200 feed rows of a run after `after`, oldest first. The caller names the run
        (the one it checked under the lock), which is not always the one this store was made on."""
        rows = self.conn.execute(
            "SELECT seq, kind, patient_id, clinician_id, data FROM clinician_action"
            " WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT 200",
            (run_id, after),
        )
        return [{**r.pop("data"), **r} for r in rows]

    # --- the feed the ward sim reads for the viewer's commands ---------------------------------

    def add_command(
        self, kind: str, clinician_id: str, patient_id: str | None, data: dict[str, Any]
    ) -> None:
        """Append to the command feed. As `add_action`: it takes the run's lock, so seq order is
        commit order."""
        lock_run(self.conn, self.run_id)
        self.conn.execute(
            "INSERT INTO sim_command (run_id, kind, patient_id, clinician_id, at, data)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (self.run_id, kind, patient_id, clinician_id, whole(self.run_now), Jsonb(data)),
        )

    def commands(self, run_id: int, after: int) -> list[dict[str, Any]]:
        """The next 200 command rows of a run after `after`, oldest first (as `actions`)."""
        rows = self.conn.execute(
            "SELECT seq, kind, patient_id, clinician_id, data FROM sim_command"
            " WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT 200",
            (run_id, after),
        )
        return [{**r.pop("data"), **r} for r in rows]
