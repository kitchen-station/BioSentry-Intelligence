"""A clinician's general question about the simulated trials (contract-ask): who may ask and read,
the claim by kind, the report, the limits, the separation from the other two kinds and the audit.
The API only stores."""

import json
import logging
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from conftest import together
from test_lab import CHARGE, MD, RN, WORKER, ask, done, running
from test_lab import claim as claim_compare
from test_overview import ADMIN, add_admin, audit_of, write
from test_patient_ask import answered as patient_answered
from test_patient_ask import insert as insert_row
from test_patient_ask import one as patient_one
from test_patient_ask import pclaim, put
from test_store import Api, api  # noqa: F401 (api: fixture)

Q = "What if we alerted at a NEWS2 total of 7 or more?"
SHA = "b" * 64


def qput(api: Api, who: str = MD, text: Any = Q, ward: str = "W4") -> Any:
    return api.client.post(f"/wards/{ward}/lab/questions", headers=api.as_(who), json={"question": text})


def qlist(api: Api, who: str = MD, ward: str = "W4") -> Any:
    return api.client.get(f"/wards/{ward}/lab/questions", headers=api.as_(who))


def qone(api: Api, who: str, qid: Any, ward: str = "W4") -> Any:
    return api.client.get(f"/wards/{ward}/lab/questions/{qid}", headers=api.as_(who))


def qclaim(api: Api, params: Any = None, headers: dict[str, str] = WORKER) -> Any:
    return api.client.post("/sim/lab/claim", params=params or {"kind": "question"}, headers=headers)


def rows(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT * FROM lab_run WHERE kind = 'question' ORDER BY id")


def record(**over: Any) -> dict[str, Any]:
    doc = {
        "question_format": 1, "synthetic": True, "grounding": "ok",
        "agent": {"ask": "keywords", "step": "S-0014", "commit": None},
        "experiment": {"template": "news2_threshold", "tool": "evaluate_rules", "args": {"window": "early"},
                       "result_sha256": SHA, "result": {"trials": 10}},
        "numbers": ["10", "7"],
    }  # fmt: skip
    return {k: v for k, v in {**doc, **over}.items() if v is not ...}


def answered(text: str = "line one\nline two", **over: Any) -> dict[str, Any]:
    return {"state": "done", "answer": text, "record_json": json.dumps(record(**over))}


def post_report(api: Api, body: Any, run_id: int = 1) -> Any:
    return api.client.post(f"/sim/lab/runs/{run_id}/report", headers=WORKER, json=body)


def queue_rows(api: Api, count: int, state: str = "queued") -> None:
    """Rows written straight to the table, so the limits do not stop a test of them."""
    write(api, "INSERT INTO lab_run (ward, requested_by, requested_at, kind, question, state)"
               " SELECT 'W4', 'rn-reyes', %s, 'question', 'q', %s FROM generate_series(1, %s)",
          api.wall.now, state, count)  # fmt: skip


def claimed(api: Api, who: str = MD) -> dict[str, Any]:
    assert qput(api, who).status_code == 201
    job = qclaim(api)
    assert job.status_code == 200
    return job.json()


# --- who may ask and read ----------------------------------------------------------------------


@pytest.mark.parametrize("who", [MD, CHARGE])
def test_an_md_or_charge_nurse_asks_and_reads(api: Api, who: str) -> None:
    made = qput(api, who)
    assert made.status_code == 201
    assert made.json() == {
        "id": 1, "requested_by": who, "requested_at": "2026-10-14T12:00:00Z", "question": Q,
        "state": "queued", "claimed_at": None, "finished_at": None, "reason": None,
    }  # fmt: skip
    assert qlist(api, who).json() == [made.json()]
    assert qone(api, who, 1).json() == {**made.json(), "answer": None, "report": None}
    assert audit_of(api, who) == [
        ("lab_question", "lab_run:1", "ok"),
        ("view_lab_questions", "W4", "ok"),
        ("view_lab_question", "lab_run:1", "ok"),
    ]


def test_an_rn_reads_but_may_not_ask(api: Api) -> None:
    assert qput(api, RN).status_code == 403
    assert audit_of(api, RN) == [("lab_question", "W4", "denied")]
    assert rows(api) == []
    qput(api, MD)
    assert [r["id"] for r in qlist(api, RN).json()] == [1]
    assert qone(api, RN, 1).status_code == 200


def test_an_admin_is_refused_on_all_three_routes_before_the_body_is_read(api: Api) -> None:
    qput(api, MD)
    headers = add_admin(api)
    for body in ({"question": Q}, {"bogus": 1}):
        response = api.client.post("/wards/W4/lab/questions", headers=headers, json=body)
        assert response.status_code == 403
    assert api.client.get("/wards/W4/lab/questions", headers=headers).status_code == 403
    assert api.client.get("/wards/W4/lab/questions/1", headers=headers).status_code == 403
    assert audit_of(api, ADMIN) == [
        ("lab_question", "W4", "denied"),
        ("lab_question", "W4", "denied"),
        ("view_lab_questions", "W4", "denied"),
        ("view_lab_question", "W4", "denied"),
    ]
    assert len(rows(api)) == 1


def test_another_ward_is_not_found(api: Api) -> None:
    assert qput(api, MD, ward="W9").status_code == 404
    assert qlist(api, RN, "W9").status_code == 404
    assert qone(api, RN, 1, "W9").status_code == 404
    assert audit_of(api, MD) == [("lab_question", "W9", "not_found")]
    assert audit_of(api, RN) == [
        ("view_lab_questions", "W9", "not_found"),
        ("view_lab_question", "W9", "not_found"),
    ]
    assert rows(api) == []


@pytest.mark.parametrize("qid", ["abc", "0", "01", "-1", "9" * 30, "999"])
def test_a_malformed_or_unknown_id_is_one_audited_404(api: Api, qid: str) -> None:
    qput(api, MD)
    response = qone(api, RN, qid)
    assert (response.status_code, response.json()) == (404, {"detail": "Not found"})
    assert audit_of(api, RN) == [("view_lab_question", "W4", "not_found")]


def test_the_list_is_the_newest_twenty_of_the_ward(api: Api) -> None:
    queue_rows(api, 21)
    assert [r["id"] for r in qlist(api, RN).json()] == list(range(21, 1, -1))


# --- the question ------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["x" * 500, "\U0001f600" * 500, "  spaced  "])
def test_a_question_is_stored_exactly_as_typed(api: Api, text: str) -> None:
    assert qput(api, MD, text).json()["question"] == text
    assert rows(api)[0]["question"] == text


BAD = [
    "", "x" * 501, "a\nb", "a\rb", "a\tb", "a\x7fb", "a\x85b", "a\u2028b", "a\u2029b", "a\u202eb",
    "a\u2066b", "a\x00b", 7, None,
]  # fmt: skip


@pytest.mark.parametrize("text", BAD, ids=repr)
def test_a_bad_question_is_refused_and_nothing_is_stored(api: Api, text: Any) -> None:
    response = qput(api, MD, text)
    assert response.status_code == 422 and "xxxx" not in response.text
    assert rows(api) == []
    assert audit_of(api, MD) == [("lab_question", "-", "rejected")]


@pytest.mark.parametrize(
    "content",
    ['{"question": "a\\ud800"}', "{nope", '{"question": "x", "kind": "question"}',
     '{"question": "x", "patient_id": "p-001"}', '{"question": "x", "mode": "w4"}', "{}"],
)  # fmt: skip
def test_a_lone_surrogate_bad_json_or_an_extra_key_is_refused(api: Api, content: str) -> None:
    headers = {**api.as_(MD), "Content-Type": "application/json"}
    response = api.client.post("/wards/W4/lab/questions", headers=headers, content=content)
    assert response.status_code == 422 and rows(api) == []


def test_a_compare_body_on_the_question_route_and_the_reverse_are_refused(api: Api) -> None:
    from test_lab import W4

    assert api.client.post("/wards/W4/lab/questions", headers=api.as_(MD), json=W4).status_code == 422
    assert ask(api, MD, {"kind": "question", "question": Q}).status_code == 422
    assert ask(api, MD, {**W4, "question": Q}).status_code == 422
    assert api.sql("SELECT 1 FROM lab_run") == []


# --- the claim, by kind ------------------------------------------------------------------------


def test_the_job_is_exactly_id_kind_and_question(api: Api) -> None:
    job = claimed(api)
    assert job == {"id": 1, "kind": "question", "question": Q}
    assert (rows(api)[0]["state"], rows(api)[0]["claimed_at"]) == ("running", api.wall.now)
    assert api.sql("SELECT resource FROM audit_log WHERE action = 'lab_claim'") == [{"resource": "lab_run:1"}]


def test_each_kind_claims_its_own_and_no_kind_claims_a_compare(api: Api) -> None:
    ask(api, MD)  # id 1
    put(api, MD, "p-001")  # id 2
    qput(api, MD)  # id 3
    first = claim_compare(api)
    assert (first.json()["kind"], first.json()["id"]) == ("compare", 1)
    assert claim_compare(api).status_code == 204
    got = qclaim(api).json()
    assert (got["kind"], got["id"]) == ("question", 3)
    got = pclaim(api).json()
    assert (got["kind"], got["id"]) == ("patient_question", 2)
    assert {r["state"] for r in api.sql("SELECT state FROM lab_run")} == {"running"}


def test_a_question_claim_never_hands_out_another_kind(api: Api) -> None:
    ask(api, MD)
    put(api, MD, "p-001")
    assert qclaim(api).status_code == 204
    assert {r["state"] for r in api.sql("SELECT state FROM lab_run")} == {"queued"}


def test_a_running_row_of_one_kind_does_not_block_the_others(api: Api) -> None:
    qput(api, MD)
    assert qclaim(api).status_code == 200
    ask(api, CHARGE)
    put(api, CHARGE, "p-001")
    assert claim_compare(api).status_code == 200
    assert pclaim(api).status_code == 200
    qput(api, CHARGE)
    assert qclaim(api).status_code == 204  # one running question


def test_a_burst_of_question_claims_hands_out_one(api: Api) -> None:
    qput(api, MD)
    qput(api, CHARGE)
    assert sorted(together(4, lambda _: qclaim(api).status_code)) == [200, 204, 204, 204]


@pytest.mark.parametrize("query", ["kind=Question", "kind=questions", "kind=question&kind=question"])
def test_a_wrong_or_repeated_kind_is_422_and_changes_nothing(api: Api, query: str) -> None:
    qput(api, MD)
    assert api.client.post(f"/sim/lab/claim?{query}", headers=WORKER).status_code == 422
    assert rows(api)[0]["state"] == "queued"
    assert api.sql("SELECT * FROM audit_log WHERE action = 'lab_claim'") == []


# --- the report --------------------------------------------------------------------------------


def test_a_done_report_is_stored_and_the_clinician_reads_the_answer(api: Api) -> None:
    claimed(api)
    body = answered("line one\nline two", extra_key={"a": 1})
    assert post_report(api, body).status_code == 204
    row = rows(api)[0]
    assert (row["state"], row["report_text"], row["finished_at"]) == ("done", "line one\nline two", api.wall.now)
    assert row["report"]["extra_key"] == {"a": 1} and row["report"]["experiment"]["result_sha256"] == SHA
    seen = qone(api, RN, 1).json()
    assert seen["state"] == "done" and seen["answer"] == "line one\nline two"
    assert seen["report"]["agent"]["step"] == "S-0014"
    assert api.sql("SELECT action, resource, outcome FROM audit_log WHERE action = 'lab_report'") == [
        {"action": "lab_report", "resource": "lab_run:1", "outcome": "ok"}
    ]


MISMATCH = {
    "format 2": {"question_format": 2},
    "format true": {"question_format": True},
    "format string": {"question_format": "1"},
    "no format": {"question_format": ...},
    "synthetic false": {"synthetic": False},
    "synthetic 1": {"synthetic": 1},
    "no synthetic": {"synthetic": ...},
    "grounding failed": {"grounding": "failed"},
    "grounding true": {"grounding": True},
    "no grounding": {"grounding": ...},
}


@pytest.mark.parametrize("over", MISMATCH.values(), ids=MISMATCH.keys())
def test_a_done_report_that_does_not_match_fails_the_row(api: Api, over: dict[str, Any]) -> None:
    claimed(api)
    response = post_report(api, answered(**over))
    assert (response.status_code, response.json()) == (
        422, {"detail": "The report does not match the request"},
    )
    row = rows(api)[0]
    assert (row["state"], row["reason"]) == ("failed", "the report did not match the request")
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report'") == [{"outcome": "rejected"}]


@pytest.mark.parametrize("record_json", ["nope", "[]", '{"a": NaN}', "[" * 65 + "]" * 65])
def test_a_record_that_is_not_an_object_or_not_json_fails_the_row(api: Api, record_json: str) -> None:
    claimed(api)
    assert post_report(api, {"state": "done", "answer": "x", "record_json": record_json}).status_code == 422
    assert rows(api)[0]["state"] == "failed"


def test_an_empty_answer_fails_the_row(api: Api) -> None:
    claimed(api)
    assert post_report(api, answered("")).status_code == 422
    assert rows(api)[0]["state"] == "failed"


def test_each_other_shaped_done_fails_on_a_question_and_a_question_done_fails_on_the_others(api: Api) -> None:
    claimed(api)
    assert post_report(api, done()).status_code == 422  # a compare done
    assert rows(api)[0]["state"] == "failed"
    qput(api, MD)  # id 2
    qclaim(api)
    assert post_report(api, patient_answered(SHA), 2).status_code == 422  # a patient question done
    assert api.sql("SELECT state FROM lab_run WHERE id = 2") == [{"state": "failed"}]
    running(api)  # a compare row, id 3
    assert post_report(api, answered(), 3).status_code == 422
    put(api, MD, "p-001")  # id 4
    pclaim(api)
    assert post_report(api, answered(), 4).status_code == 422
    assert api.sql("SELECT state FROM lab_run WHERE id IN (3, 4)") == [{"state": "failed"}] * 2


@pytest.mark.parametrize(
    "body",
    [
        {"state": "done", "answer": "x" * 4001, "record_json": "{}"},
        {"state": "done", "answer": "x", "record_json": " " * 131073},
        {"state": "done", "answer": "a\rb", "record_json": "{}"},
        {"state": "done", "answer": "a\tb", "record_json": "{}"},
        {"state": "done", "answer": "x", "record_json": "{}", "compare_json": "{}"},
        {"state": "done", "answer": "x"},
    ],
)
def test_a_report_that_breaks_the_shape_or_a_cap_changes_nothing(api: Api, body: Any) -> None:
    claimed(api)
    assert post_report(api, body).status_code == 422
    assert rows(api)[0]["state"] == "running"


def test_the_largest_allowed_answer_and_record_are_accepted(api: Api) -> None:
    claimed(api)
    body = answered("t" * 4000, pad="x" * 120000)
    assert len(body["record_json"]) <= 131072
    assert post_report(api, body).status_code == 204


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"state": "refused", "reason": "Refused: no template fits this question."}, "refused"),
        ({"state": "failed", "reason": "the agent's tool failed"}, "failed"),
    ],
)
def test_a_refused_or_failed_report_is_final(api: Api, body: dict[str, str], state: str) -> None:
    claimed(api)
    assert post_report(api, body).status_code == 204
    row = rows(api)[0]
    assert (row["state"], row["reason"]) == (state, body["reason"])
    assert post_report(api, body).status_code == 409
    seen = qone(api, MD, 1).json()
    assert (seen["state"], seen["reason"], seen["answer"]) == (state, body["reason"], None)


def test_a_report_for_a_queued_question_is_409(api: Api) -> None:
    qput(api, MD)
    assert post_report(api, {"state": "failed", "reason": "x"}).status_code == 409


# --- limits ------------------------------------------------------------------------------------


def test_one_open_question_for_each_clinician(api: Api) -> None:
    assert qput(api, MD).status_code == 201
    second = qput(api, MD)
    assert (second.status_code, second.json()) == (429, {"detail": "Lab run limit reached"})
    assert qput(api, CHARGE).status_code == 201
    assert audit_of(api, MD) == [("lab_question", "lab_run:1", "ok"), ("lab_question", "W4", "limited")]
    qclaim(api)  # running still counts as open
    assert qput(api, MD).status_code == 429
    post_report(api, {"state": "failed", "reason": "x"})
    assert qput(api, MD).status_code == 201


def test_ten_in_a_day_and_the_next_day_is_allowed(api: Api) -> None:
    for _ in range(10):
        assert qput(api, MD).status_code == 201
        write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at")
    assert qput(api, MD).status_code == 429
    api.wall.now += timedelta(hours=24, seconds=1)
    api.tokens.clear()
    assert qput(api, MD).status_code == 201


def test_five_queued_on_the_ward(api: Api) -> None:
    queue_rows(api, 5)
    assert qput(api, MD).status_code == 429
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at WHERE id = 1")
    assert qput(api, MD).status_code == 201


def test_a_burst_of_asks_passes_the_limit_once(api: Api) -> None:
    api.as_(MD)
    assert sorted(together(6, lambda _: qput(api, MD).status_code)) == [201, 429, 429, 429, 429, 429]


def test_the_three_kinds_never_count_each_other(api: Api) -> None:
    assert ask(api).status_code == 201  # an open compare
    assert qput(api, MD).status_code == 201  # not blocked by it
    assert put(api, MD, "p-001").status_code == 201  # nor by either
    assert ask(api).status_code == 429 and qput(api).status_code == 429 and put(api, MD, "p-001").status_code == 429
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at WHERE kind = 'question'")
    assert qput(api, MD).status_code == 201  # only its own kind counted
    assert ask(api).status_code == 429


def test_five_queued_of_the_other_kinds_do_not_fill_the_ward_queue(api: Api) -> None:
    for n in range(1, 6):
        assert put(api, CHARGE, f"p-00{n}").status_code == 201
    assert qput(api, CHARGE).status_code == 201  # five queued patient questions do not fill this queue
    queue_rows(api, 4)
    assert ask(api, MD).status_code == 201  # five queued questions do not fill the compare queue


# --- separation --------------------------------------------------------------------------------


def test_each_kinds_routes_never_serve_another_kind(api: Api) -> None:
    ask(api, MD)  # 1 compare
    put(api, CHARGE, "p-001")  # 2 patient question
    qput(api, CHARGE)  # 3 question
    assert [r["id"] for r in qlist(api, RN).json()] == [3]
    assert [r["id"] for r in api.client.get("/wards/W4/lab/runs", headers=api.as_(RN)).json()] == [1]
    for run_id in (1, 2):
        assert qone(api, RN, run_id).status_code == 404
    for run_id in (2, 3):
        assert api.client.get(f"/wards/W4/lab/runs/{run_id}", headers=api.as_(RN)).status_code == 404
    for run_id in (1, 3):
        assert patient_one(api, MD, "p-001", run_id).status_code == 404
    assert patient_one(api, MD, "p-001", 2).status_code == 200
    assert [r["id"] for r in api.client.get("/patients/p-001/questions", headers=api.as_(MD)).json()] == [2]


# --- timeouts ----------------------------------------------------------------------------------


def test_a_queued_question_fails_after_an_hour_and_a_running_one_after_twenty_minutes(api: Api) -> None:
    qput(api, MD)
    api.wall.now += timedelta(hours=1, seconds=30)
    assert qlist(api, RN).json()[0]["reason"] == "no worker claimed it within an hour"
    claimed(api, CHARGE)
    api.wall.now += timedelta(minutes=21)
    seen = qone(api, RN, 2).json()
    assert (seen["state"], seen["reason"]) == ("failed", "the worker did not report within 20 minutes")


# --- privacy -----------------------------------------------------------------------------------


def test_the_question_and_the_answer_are_in_no_audit_row_and_no_log(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    marker, reply = "MARKER-QUESTION-5521", "MARKER-ANSWER-9035"
    assert qput(api, MD, marker).status_code == 201
    qlist(api, MD)
    qone(api, MD, 1)
    assert qclaim(api).status_code == 200
    assert post_report(api, answered(reply)).status_code == 204
    qput(api, MD, marker)
    qclaim(api)
    post_report(api, {"state": "refused", "reason": "Refused: nope"}, 2)
    qput(api, MD, marker + "\n")  # a refused body
    qone(api, MD, 1)
    dump = json.dumps(api.sql("SELECT * FROM audit_log"), default=str)
    for text in (marker, reply):
        assert text not in dump and text not in caplog.text


# --- the table's own checks --------------------------------------------------------------------


def test_the_table_takes_a_question_row_and_refuses_each_broken_one(api: Api) -> None:
    write(api, "INSERT INTO lab_run (ward, requested_by, requested_at, kind, question, state)"
               " VALUES ('W4', %s, %s, 'question', 'q', 'queued')", MD, api.wall.now)
    broken: list[dict[str, Any]] = [
        {"mode": "w4"}, {"seed": 1}, {"days": 1}, {"baseline": "news2_total_ge_5"},
        {"change": "news2_chart_trigger"}, {"run_id": 1}, {"patient_id": "p-001"},
        {"context": "{}"}, {"context_sha256": SHA}, {"question": ...}, {"question": ""},
        {"question": "a\nb"}, {"question": "x" * 501},
    ]  # fmt: skip
    for over in broken:
        row = {"ward": "W4", "requested_by": MD, "requested_at": api.wall.now, "kind": "question",
               "question": "q", "state": "queued", **over}  # fmt: skip
        row = {k: v for k, v in row.items() if v is not ...}
        with pytest.raises(psycopg.errors.Error):
            write(api, f"INSERT INTO lab_run ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", *row.values())
    compare = {"kind": "compare", "mode": "w4", "seed": 1, "days": 1,
               "baseline": "news2_total_ge_5", "change": "news2_chart_trigger"}  # fmt: skip
    for over in ({"question": "q"}, {"run_id": 1}):
        with pytest.raises(psycopg.errors.Error):
            insert_row(api, **{**compare, "question": ..., "run_id": ..., "patient_id": ..., "context": ...,
                               "context_sha256": ..., **over})  # fmt: skip
    with pytest.raises(psycopg.errors.Error):  # a patient question row with no patient
        insert_row(api, kind="patient_question", run_id=..., patient_id=...)
    assert len(api.sql("SELECT 1 FROM lab_run")) == 1
