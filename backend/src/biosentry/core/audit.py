"""Append-only audit log (I7: every view is logged, whether it succeeded or not).

Records hold ids and outcomes only: no names, no PhilHealth PIN, no measurement values. A
record names the clinician by id and by the role they held when they acted (a manual entry
carries source `nurse` on the measurement; who typed it is answered here). The table refuses
UPDATE, DELETE, TRUNCATE and a repeated seq, but not someone who can drop its triggers; the
hash-chained form comes later. `resource` comes from the caller, so it is cut at 200 characters.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from biosentry.repositories.postgres import Conn, whole

RESOURCE_MAX = 200
Outcome = Literal["ok", "not_found", "denied", "rejected", "limited"]
Clock = Callable[[], datetime]
RoleOf = Callable[[str], str | None]


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

    def count(self, action: str, resource: str, outcome: Outcome, since: datetime) -> int:
        """How many records of this action on this resource with this outcome came after `since`."""
        row = self._conn.execute(
            "SELECT count(*) AS n FROM audit_log"
            " WHERE action = %s AND resource = %s AND outcome = %s AND at > %s",
            (action, resource, outcome, whole(since)),
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

    def recent(self, since: datetime, per_clinician: int) -> list[dict[str, Any]]:
        """What nurses and physicians did after `since`, the newest `per_clinician` each, by
        clinician id then newest first. Sign-in rows are left out (they are logged under the id
        that was tried, not the person who tried). `resource` is "-" unless the outcome is ok:
        on the other rows it is whatever the caller typed."""
        # shortcut: no index on audit_log (at), the window query scans the table; add an index on
        # (at) when the log passes about a million rows.
        return self._conn.execute(
            "SELECT seq, at, clinician_id, action, outcome,"
            " CASE WHEN outcome = 'ok' THEN resource ELSE '-' END AS resource FROM ("
            " SELECT *, row_number() OVER (PARTITION BY clinician_id ORDER BY seq DESC) AS n"
            " FROM audit_log WHERE at > %s AND role IN ('rn', 'md', 'charge_rn')"
            " AND action NOT IN ('sign_in', 'authenticate')) t"
            " WHERE n <= %s ORDER BY clinician_id, seq DESC",
            (whole(since), per_clinician),
        ).fetchall()
