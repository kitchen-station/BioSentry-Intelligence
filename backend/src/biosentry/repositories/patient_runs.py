"""Read-only membership queries against the worker's explicit compare result links."""

from typing import Any

from psycopg.types.json import Jsonb

from biosentry.repositories.postgres import PostgresStore


def patient_runs(store: PostgresStore, patient_id: str, lab_run_id: int | None = None) -> list[dict[str, Any]]:
    return store.conn.execute(
        "SELECT l.* FROM lab_run l JOIN patient p ON p.run_id = %s AND p.id = %s"
        " WHERE l.kind = 'compare' AND l.state = 'done' AND l.ward = p.unit"
        " AND l.report @> %s AND (%s::bigint IS NULL OR l.id = %s)"
        " ORDER BY l.requested_at DESC, l.id DESC",
        (store.run_id, patient_id,
         Jsonb({"patient_refs": [{"run_id": store.run_id, "patient_id": patient_id}]}),
         lab_run_id, lab_run_id),
    ).fetchall()
