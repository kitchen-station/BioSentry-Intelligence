"""A clinician's question about ONE patient (contract-patient-ask). The API freezes an allow-listed
copy of the patient's data, stores it with its sha256 and queues the question in the lab queue; a
worker answers by code from that copy only. The copy is built by naming each key: a field added to
a source model never reaches it. Audit rows hold ids and outcomes, never the question or the data.
Nothing that ranks a patient imports this module."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from biosentry.core.audit import AuditLog
from biosentry.repositories.postgres import PostgresStore, iso, whole
from biosentry.schemas import AbstainedRow, BaselineResponse, PatientDetail, VitalHistoryFull
from biosentry.schemas.lab import (
    NewPatientQuestion,
    PatientContext,
    PatientContextAbstained,
    PatientContextBaseline,
    PatientContextDetail,
    PatientQuestionDetail,
    PatientQuestionItem,
)

from .baseline import BaselineService
from .lab import _LOCK, _expire
from .patients import RejectedError
from .ward import WardService

# judgement (contract-patient-ask 3): the compare limits are separate
OPEN_PER_PATIENT = 1
PER_DAY = 20
QUEUED_PER_WARD = 5
LIST_LIMIT = 20
HISTORY = timedelta(hours=72)
MAX_CONTEXT = 262144  # bytes; the table checks it too
TOO_LARGE = "This patient's data is too large to ask about"


def build(
    found: PatientDetail | AbstainedRow, baseline: BaselineResponse | None, as_of: datetime
) -> PatientContext:
    """The allow-list. Each key is named; nothing is dumped and then trimmed."""
    detail = baseline_part = abstained = None
    if isinstance(found, PatientDetail):
        since = as_of - HISTORY
        history = found.vital_history
        detail = PatientContextDetail(
            rank=found.rank,
            score=found.score,
            trend=found.trend,
            supporting=found.supporting,
            escalation_reasons=found.escalation_reasons,
            vitals=found.vitals,
            signal_quality=found.signal_quality,
            last_update_s=found.last_update_s,
            vital_history=VitalHistoryFull(
                heart_rate=[b for b in history.heart_rate if b.time >= since],
                respiratory_rate=[b for b in history.respiratory_rate if b.time >= since],
                spo2=[b for b in history.spo2 if b.time >= since],
            ),
        )
        if baseline is not None:
            baseline_part = PatientContextBaseline(
                admission_known=baseline.admission_known,
                label=baseline.label,
                params=baseline.params,
                vitals=baseline.vitals,
            )
    else:
        abstained = PatientContextAbstained(reason=found.reason, instruction=found.instruction)
    return PatientContext(
        format=1,
        synthetic=found.synthetic,
        as_of=iso(as_of),
        detail=detail,
        baseline=baseline_part,
        abstained=abstained,
    )


def canonical(context: PatientContext) -> tuple[str, str]:
    """The stored text and its sha256: the worker computes the same on the object it receives."""
    text = json.dumps(
        context.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _item(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "requested_by", "question", "state", "reason")}
    for k in ("requested_at", "claimed_at", "finished_at"):
        out[k] = None if row[k] is None else iso(row[k])
    return out


class PatientAskService:
    def __init__(
        self, store: PostgresStore, audit: AuditLog, wards: WardService, baselines: BaselineService
    ) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards
        self._baselines = baselines

    def create(
        self, who: str, patient_id: str, body: NewPatientQuestion
    ) -> PatientQuestionItem | Literal["not_found", "limited"]:
        """Freezes the patient's data as `who` may see it now, then queues the question."""
        found = self._wards.detail_or_abstained(who, patient_id)
        if found is None:  # missing, not yours and left look the same
            self._audit.append(who, "ask_patient", patient_id, "not_found")
            return "not_found"
        baseline = (
            self._baselines.compute(who, patient_id) if isinstance(found, PatientDetail) else None
        )
        text, sha = canonical(build(found, baseline, self._store.now()))
        if len(text.encode("utf-8")) > MAX_CONTEXT:
            self._audit.append(who, "ask_patient", patient_id, "rejected")
            raise RejectedError(TOO_LARGE)
        store = self._store
        now = whole(store.clock())
        with store.tx():
            store.conn.execute(_LOCK)  # one request at a time, so a burst cannot pass the counts
            _expire(store.conn, now)
            counts = store.conn.execute(
                "SELECT count(*) FILTER (WHERE requested_by = %(who)s AND run_id = %(run)s"
                " AND patient_id = %(pid)s AND state IN ('queued', 'running')) AS open,"
                " count(*) FILTER (WHERE requested_by = %(who)s AND requested_at > %(day)s) AS day,"
                " count(*) FILTER (WHERE state = 'queued' AND ward = %(ward)s) AS queued"
                " FROM lab_run WHERE kind = 'patient_question'",
                {
                    "who": who,
                    "run": store.run_id,
                    "pid": patient_id,
                    "day": now - timedelta(hours=24),
                    "ward": store.ward,
                },
            ).fetchone()
            assert counts is not None
            if (
                counts["open"] >= OPEN_PER_PATIENT
                or counts["day"] >= PER_DAY
                or counts["queued"] >= QUEUED_PER_WARD
            ):
                row = None
            else:
                row = store.conn.execute(
                    "INSERT INTO lab_run (ward, requested_by, requested_at, kind, question, run_id,"
                    " patient_id, context, context_sha256, state)"
                    " VALUES (%s, %s, %s, 'patient_question', %s, %s, %s, %s, %s, 'queued')"
                    " RETURNING *",
                    (store.ward, who, now, body.question, store.run_id, patient_id, text, sha),
                ).fetchone()
                assert row is not None
        if row is None:  # audited after the transaction, so the expiry above is kept either way
            self._audit.append(who, "ask_patient", patient_id, "limited")
            return "limited"
        self._audit.append(who, "ask_patient", patient_id, "ok")
        return PatientQuestionItem.model_validate(_item(row))

    def recent(self, who: str, patient_id: str) -> list[PatientQuestionItem] | None:
        """The newest 20 for this patient, for whoever may see the patient now."""
        if patient_id not in self._wards.visible(who):
            self._audit.append(who, "view_patient_questions", patient_id, "not_found")
            return None
        store = self._store
        _expire(store.conn, store.clock())
        rows = store.conn.execute(
            "SELECT * FROM lab_run WHERE kind = 'patient_question' AND run_id = %s"
            " AND patient_id = %s ORDER BY id DESC LIMIT %s",
            (store.run_id, patient_id, LIST_LIMIT),
        ).fetchall()
        self._audit.append_view(who, "view_patient_questions", patient_id)  # the panel reads it on a timer
        return [PatientQuestionItem.model_validate(_item(r)) for r in rows]

    def detail(
        self, who: str, patient_id: str, question_id: int | None
    ) -> PatientQuestionDetail | None:
        """One question with its answer. `question_id` is None when the path was not an id."""
        store = self._store
        row = None
        if question_id is not None and patient_id in self._wards.visible(who):
            _expire(store.conn, store.clock())
            row = store.conn.execute(
                "SELECT * FROM lab_run WHERE kind = 'patient_question' AND id = %s AND run_id = %s"
                " AND patient_id = %s",
                (question_id, store.run_id, patient_id),
            ).fetchone()
        if row is None:  # malformed, unknown, another patient's and not yours are one answer
            self._audit.append(who, "view_patient_question", patient_id, "not_found")
            return None
        self._audit.append(who, "view_patient_question", f"patient_question:{row['id']}", "ok")
        return PatientQuestionDetail.model_validate(
            {**_item(row), "answer": row["report_text"], "report": row["report"]}
        )
