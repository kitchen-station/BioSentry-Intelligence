"""The Simulation lab's store (contract-d7): a clinician's request for one simulated compare run,
the lab worker's claim of it, and the worker's report. A patient question (contract-patient-ask)
is a second kind of row in the same queue; `patient_ask.py` creates and reads it. The API never runs anything and nothing
that ranks or scores a patient reads these rows. Timeouts are applied here when a row is touched,
with the injected wall clock; nothing runs in the background."""

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from psycopg.types.json import Jsonb

from biosentry.core.audit import AuditLog
from biosentry.repositories.postgres import Conn, PostgresStore, iso, transaction, whole
from biosentry.schemas.lab import (
    LabAnswer,
    LabDone,
    LabJob,
    LabReport,
    LabRunDetail,
    LabRunItem,
    NewLabRun,
    PatientQuestionJob,
)

# judgement (contract-d7 sections 4 and 8): one worker, one job at a time, run time not measured
QUEUED_FOR = timedelta(hours=1)
RUNNING_FOR = timedelta(minutes=20)
OPEN_PER_CLINICIAN = 1
PER_DAY = 10
QUEUED_PER_WARD = 5
LIST_LIMIT = 20

Kind = Literal["compare", "patient_question"]
Result = Literal["ok", "not_found", "not_running", "mismatch"]
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('lab_run', 0))"


def _expire(conn: Conn, now: datetime) -> None:
    now = whole(now)
    for state, since, reason in (
        ("queued", "requested_at", "no worker claimed it within an hour"),
        ("running", "claimed_at", "the worker did not report within 20 minutes"),
    ):
        limit = QUEUED_FOR if state == "queued" else RUNNING_FOR
        conn.execute(
            f"UPDATE lab_run SET state = 'failed', reason = %s, finished_at = %s"
            f" WHERE state = %s AND {since} < %s",
            (reason, now, state, now - limit),
        )


def _item(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        k: row[k]
        for k in ("id", "kind", "requested_by", "mode", "seed", "days", "baseline", "change")
        + ("state", "reason")
    }
    for k in ("requested_at", "claimed_at", "finished_at"):
        out[k] = None if row[k] is None else iso(row[k])
    return out


def create(store: PostgresStore, audit: AuditLog, who: str, body: NewLabRun) -> LabRunItem | None:
    """None when a limit is reached (audited `limited`)."""
    now = whole(store.clock())
    with store.tx():
        store.conn.execute(_LOCK)  # one request at a time, so a burst cannot pass the counts
        _expire(store.conn, now)
        counts = store.conn.execute(
            "SELECT count(*) FILTER (WHERE requested_by = %(who)s AND state IN ('queued', 'running'))"
            " AS open, count(*) FILTER (WHERE requested_by = %(who)s AND requested_at > %(day)s)"
            " AS day, count(*) FILTER (WHERE state = 'queued' AND ward = %(ward)s) AS queued"
            " FROM lab_run WHERE kind = 'compare'",
            {"who": who, "day": now - timedelta(hours=24), "ward": store.ward},
        ).fetchone()
        assert counts is not None
        if (
            counts["open"] >= OPEN_PER_CLINICIAN
            or counts["day"] >= PER_DAY
            or counts["queued"] >= QUEUED_PER_WARD
        ):
            row = None
        else:
            row = store.conn.execute(
                "INSERT INTO lab_run (ward, requested_by, requested_at, kind, mode, seed, days,"
                " baseline, change, state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued')"
                " RETURNING *",
                (store.ward, who, now, body.kind, body.mode, body.seed, body.days,
                 body.baseline, body.change),
            ).fetchone()
            assert row is not None
    if row is None:  # audited after the transaction, so the expiry above is kept either way
        audit.append(who, "lab_request", store.ward, "limited")
        return None
    audit.append(who, "lab_request", f"lab_run:{row['id']}", "ok")
    return LabRunItem.model_validate(_item(row))


def list_runs(store: PostgresStore) -> list[LabRunItem]:
    _expire(store.conn, store.clock())
    rows = store.conn.execute(
        "SELECT * FROM lab_run WHERE ward = %s AND kind = 'compare' ORDER BY id DESC LIMIT %s", (store.ward, LIST_LIMIT)
    )
    return [LabRunItem.model_validate(_item(r)) for r in rows]


def get_run(store: PostgresStore, lab_run_id: int) -> LabRunDetail | None:
    _expire(store.conn, store.clock())
    row = store.conn.execute(
        "SELECT * FROM lab_run WHERE id = %s AND ward = %s AND kind = 'compare'",
        (lab_run_id, store.ward),
    ).fetchone()
    if row is None:
        return None
    return LabRunDetail.model_validate(
        {**_item(row), "report": row["report"], "report_text": row["report_text"]}
    )


# the outer `state = 'queued'` keeps a row that a concurrent read just failed from becoming running
CLAIM = (
    "UPDATE lab_run SET state = 'running', claimed_at = %s WHERE state = 'queued' AND id ="
    " (SELECT min(id) FROM lab_run WHERE state = 'queued' AND kind = %s) RETURNING *"
)


def claim(conn: Conn, now: datetime, kind: Kind = "compare") -> LabJob | PatientQuestionJob | None:
    """The oldest queued run of this kind, now running. None while nothing of this kind is queued
    or one is running."""
    with transaction(conn):
        conn.execute(_LOCK)  # two claims take turns, so two runs of one kind are never running
        _expire(conn, now)
        if conn.execute("SELECT 1 FROM lab_run WHERE state = 'running' AND kind = %s", (kind,)).fetchone():
            return None
        row = conn.execute(CLAIM, (whole(now), kind)).fetchone()
    if row is None:
        return None
    if kind == "compare":
        return LabJob.model_validate({k: row[k] for k in LabJob.model_fields})
    # the stored text, parsed again: the worker hashes what it receives
    return PatientQuestionJob.model_validate(
        {
            "id": row["id"],
            "kind": kind,
            "question": row["question"],
            "patient": json.loads(row["context"]),
        }
    )


def _is(value: Any, want: Any) -> bool:
    return type(value) is type(want) and value == want  # True is not 1


def _matches(row: dict[str, Any], doc: Any) -> bool:
    """contract-d7 v3 section 5: the checks on a `done` report, these and nothing else. The
    simulator adds keys between versions (`paired_episodes`), so no other key is read or
    required: the rest is stored as it comes."""
    return (
        isinstance(doc, dict)
        and _is(doc.get("compare_format"), 1)
        and doc.get("synthetic") is True
        and _is(doc.get("mode"), row["mode"])
    )


def _answered(row: dict[str, Any], doc: Any) -> bool:
    """contract-patient-ask 6.1: these and nothing else. The grounding check is the worker's; the
    API reads its verdict and that the record is about the data it froze."""
    return (
        isinstance(doc, dict)
        and _is(doc.get("patient_question_format"), 1)
        and doc.get("synthetic") is True
        and _is(doc.get("grounding"), "ok")
        and _is(doc.get("data_sha256"), row["context_sha256"])
    )


def _refuse_constant(name: str) -> Any:
    raise ValueError(name)  # NaN and Infinity are not JSON and jsonb cannot hold them


# A real compare.json is about 5 deep. Deeper is refused: past a limit the stored value could not
# be served again.
MAX_DEPTH = 64


def _too_deep(doc: Any) -> bool:
    stack = [(doc, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > MAX_DEPTH:
                return True
            stack.extend((v, depth + 1) for v in (node.values() if isinstance(node, dict) else node))
    return False


def _parse(text: str) -> Any:
    try:
        if "\\u0000" in text:  # jsonb cannot hold a NUL
            return None
        doc = json.loads(text, parse_constant=_refuse_constant)
        if _too_deep(doc):
            return None
        # 1e400 is infinity and "\ud800" is a lone surrogate: both fail here, not in the database
        json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return doc
    except (ValueError, RecursionError):
        return None


def report(conn: Conn, lab_run_id: int, body: LabReport, now: datetime) -> Result:
    now = whole(now)
    with transaction(conn):
        _expire(conn, now)
        row = conn.execute("SELECT * FROM lab_run WHERE id = %s FOR UPDATE", (lab_run_id,)).fetchone()
        if row is None:
            return "not_found"
        if row["state"] != "running":
            return "not_running"
        if isinstance(body, LabDone | LabAnswer):
            if isinstance(body, LabDone):
                doc, text = _parse(body.compare_json), body.compare_txt
                ok = row["kind"] == "compare" and _matches(row, doc)
            else:
                doc, text = _parse(body.record_json), body.answer
                ok = row["kind"] == "patient_question" and _answered(row, doc)
            if text and ok:
                conn.execute(
                    "UPDATE lab_run SET state = 'done', finished_at = %s, report = %s,"
                    " report_text = %s WHERE id = %s",
                    (now, Jsonb(doc), text, lab_run_id),
                )
                return "ok"
            state, reason = "failed", "the report did not match the request"
        else:
            state, reason = body.state, body.reason
        conn.execute(
            "UPDATE lab_run SET state = %s, reason = %s, finished_at = %s WHERE id = %s",
            (state, reason, now, lab_run_id),
        )
        return "mismatch" if isinstance(body, LabDone | LabAnswer) else "ok"
