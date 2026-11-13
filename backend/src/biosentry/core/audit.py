"""Append-only audit log (I7: every view is logged, whether it succeeded or not).

Records hold ids and outcomes only: no names, no PhilHealth PIN, no measurement values. A
record names the clinician by id and by the role they held when they acted (a manual entry
carries source `nurse` on the measurement; who typed it is answered here). The table refuses
UPDATE, DELETE, TRUNCATE and a repeated seq, but not someone who can drop its triggers; the
hash-chained form comes later. `resource` comes from the caller, so it is cut at 200 characters.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from biosentry.repositories.postgres import Conn, whole

RESOURCE_MAX = 200
Outcome = Literal["ok", "not_found", "denied", "rejected", "limited"]
Clock = Callable[[], datetime]
RoleOf = Callable[[str], str | None]
# The reads the dashboard repeats on a timer. A repeat of one of these by the same clinician on the
# same resource with outcome ok is recorded at most once in VIEW_ROW_EVERY (the owner's decision
# for the automatic refresh). Every other action, and every outcome but ok, is always recorded.
REPEATED_VIEWS = frozenset(
    {"view_ward", "view_live", "view_patient", "view_baseline", "view_lab_runs"}
)
VIEW_ROW_EVERY = timedelta(minutes=1)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    seq: int
    at: datetime
    clinician_id: str | None  # None: the caller was not identified
    role: str | None  # the role that clinician held when they acted; None when unidentified
    action: str
    resource: str  # a patient id, a ward, or "-"
    outcome: Outcome


class AuditLog:
    def __init__(self, conn: Conn, clock: Clock, role_of: RoleOf) -> None:
        self._conn = conn
        self._clock = clock
        self._role_of = role_of

    def append(
        self, clinician_id: str | None, action: str, resource: str, outcome: Outcome
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (at, clinician_id, role, action, resource, outcome)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                whole(self._clock()),
                clinician_id,
                self._role_of(clinician_id) if clinician_id is not None else None,
                action,
                resource.replace("\x00", "")[:RESOURCE_MAX],  # PostgreSQL text cannot hold a NUL
                outcome,
            ),
        )

    def append_view(self, clinician_id: str, action: str, resource: str) -> None:
        """An ok read. A timer-driven read (REPEATED_VIEWS) already recorded for this clinician
        on this resource within VIEW_ROW_EVERY is not recorded again; the data is served anyway."""
        if action in REPEATED_VIEWS and self.count(
            action, resource, "ok", self._clock() - VIEW_ROW_EVERY, clinician_id
        ):
            return
        self.append(clinician_id, action, resource, "ok")

    def count(
        self,
        action: str,
        resource: str,
        outcome: Outcome,
        since: datetime,
        clinician_id: str | None = None,
    ) -> int:
        """How many records of this action on this resource with this outcome came after `since`;
        only this clinician's when one is given."""
        row = self._conn.execute(
            "SELECT count(*) AS n FROM audit_log"
            " WHERE action = %s AND resource = %s AND outcome = %s AND at > %s"
            " AND (%s::text IS NULL OR clinician_id = %s)",
            (action, resource, outcome, whole(since), clinician_id, clinician_id),
        ).fetchone()
        assert row is not None
        return int(row["n"])

    def records(self, clinician_id: str | None = None) -> tuple[AuditRecord, ...]:
        """Every record, oldest first; or only this clinician's."""
        rows = self._conn.execute(
            "SELECT * FROM audit_log WHERE %(who)s::text IS NULL OR clinician_id = %(who)s"
            " ORDER BY seq",
            {"who": clinician_id},
        )
        return tuple(
            AuditRecord(
                r["seq"],
                r["at"],
                r["clinician_id"],
                r["role"],
                r["action"],
                r["resource"],
                r["outcome"],
            )
            for r in rows
        )

    def for_clinician(self, clinician_id: str) -> tuple[AuditRecord, ...]:
        return self.records(clinician_id)

    def recent(self, since: datetime) -> list[dict[str, Any]]:
        """What nurses and physicians did after `since`, as counts per clinician, action and
        outcome, by clinician id, action, outcome. Counts, not rows: a row would show which
        patient a named clinician opened and when, and oversight needs volume and failures.
        Sign-in rows are left out (they are logged under the id that was tried, not the person
        who tried), and so is any row without a clinician role."""
        return self._conn.execute(
            "SELECT clinician_id, action, outcome, count(*) AS count FROM audit_log"
            " WHERE at > %s AND role IN ('rn', 'md', 'charge_rn')"
            " AND action NOT IN ('sign_in', 'authenticate')"
            " GROUP BY clinician_id, action, outcome ORDER BY clinician_id, action, outcome",
            (whole(since),),
        ).fetchall()
