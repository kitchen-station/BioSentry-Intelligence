"""Append-only audit log (I7: every view is logged, whether it succeeded or not).

Records hold ids and outcomes only: no names, no PhilHealth PIN, no measurement values. A
record names the clinician by id and by the role they held when they acted (a manual entry
carries source `nurse` on the measurement; who typed it is answered here). The table refuses
UPDATE, DELETE and overwriting a row, but not someone who can drop its triggers; the
hash-chained form comes later. `resource` comes from the caller, so it is cut at 200 characters.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from biosentry.repositories.sqlite import iso, parse

RESOURCE_MAX = 200
Outcome = Literal["ok", "not_found", "denied", "rejected"]
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
    def __init__(self, conn: sqlite3.Connection, clock: Clock, role_of: RoleOf) -> None:
        self._conn = conn
        self._clock = clock
        self._role_of = role_of

    def append(
        self, clinician_id: str | None, action: str, resource: str, outcome: Outcome
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (at, clinician_id, role, action, resource, outcome)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                iso(self._clock()),
                clinician_id,
                self._role_of(clinician_id) if clinician_id is not None else None,
                action,
                resource[:RESOURCE_MAX],
                outcome,
            ),
        )

    def records(self, clinician_id: str | None = None) -> tuple[AuditRecord, ...]:
        """Every record, oldest first; or only this clinician's."""
        rows = self._conn.execute(
            "SELECT * FROM audit_log WHERE ?1 IS NULL OR clinician_id = ?1 ORDER BY seq",
            (clinician_id,),
        )
        return tuple(
            AuditRecord(
                r["seq"],
                parse(r["at"]),
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
