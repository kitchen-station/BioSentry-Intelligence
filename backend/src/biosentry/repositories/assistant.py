"""Private assistant messages. Callers must check current patient visibility before reading."""

from datetime import datetime, timedelta
from typing import Any

from biosentry.repositories.postgres import Conn, iso

RETENTION = timedelta(days=90)


class AssistantRepository:
    def __init__(self, conn: Conn) -> None:
        self.conn = conn

    def prune(self, now: datetime) -> None:
        # Lazy cleanup on conversation reads/writes; the read predicate also enforces the cut.
        self.conn.execute("DELETE FROM assistant_message WHERE created_at < %s", (now - RETENTION,))

    def append(self, row: dict[str, Any], role: str, text: str, now: datetime) -> None:
        self.prune(now)
        self.conn.execute(
            "INSERT INTO assistant_message"
            " (user_id, run_id, patient_id, scope, role, text, lab_run_id, created_at)"
            " VALUES (%s, %s, %s, 'patient', %s, %s, %s, %s)",
            (row["requested_by"], row["run_id"], row["patient_id"], role, text, row["id"], now),
        )

    def recent(self, who: str, run_id: int, patient_id: str, now: datetime) -> list[dict[str, Any]]:
        self.prune(now)
        rows = self.conn.execute(
            "SELECT id, role, text, lab_run_id, created_at FROM assistant_message"
            " WHERE user_id = %s AND run_id = %s AND patient_id = %s AND scope = 'patient'"
            " AND created_at >= %s ORDER BY created_at, id",
            (who, run_id, patient_id, now - RETENTION),
        ).fetchall()
        return [{**r, "created_at": iso(r["created_at"])} for r in rows]
