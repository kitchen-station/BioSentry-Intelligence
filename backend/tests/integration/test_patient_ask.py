"""A clinician's question about one patient (contract-patient-ask): who may ask and read, the
frozen context, the claim by kind, the report, the limits and the audit. The API only stores."""

import hashlib
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
from test_store import Api, api  # noqa: F401 (api: fixture)

from biosentry.services import patient_ask

Q = "What changed in the last 6 hours?"
MY = {MD: "p-001", RN: "p-003", CHARGE: "p-001"}  # a patient each of them is assigned to
CONTEXT_KEYS = {"format", "synthetic", "as_of", "detail", "baseline", "abstained"}
DETAIL_KEYS = {
    "rank", "score", "trend", "supporting", "escalation_reasons", "vitals",
    "signal_quality", "last_update_s", "vital_history",
}  # fmt: skip
SHA = "a" * 64


def put(api: Api, who: str, pid: str, text: Any = Q) -> Any:
    return api.client.post(f"/patients/{pid}/questions", headers=api.as_(who), json={"question": text})


def listing(api: Api, who: str, pid: str) -> Any:
    return api.client.get(f"/patients/{pid}/questions", headers=api.as_(who))


def one(api: Api, who: str, pid: str, qid: Any) -> Any:
    return api.client.get(f"/patients/{pid}/questions/{qid}", headers=api.as_(who))


def pclaim(api: Api, params: Any = None, headers: dict[str, str] = WORKER) -> Any:
    return api.client.post("/sim/lab/claim", params=params or {"kind": "patient_question"}, headers=headers)


def rows(api: Api) -> list[dict[str, Any]]:
    return api.sql("SELECT * FROM lab_run WHERE kind = 'patient_question' ORDER BY id")


def sha_of(patient: Any) -> str:
    text = json.dumps(patient, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(sha: str, **over: Any) -> dict[str, Any]:
    doc = {
        "patient_question_format": 1, "synthetic": True, "grounding": "ok",
        "agent": {"ask": "keywords", "commit": None}, "kind": "own_baseline",
        "args": {"vital": "heart_rate"}, "numbers": ["72.0"], "data_sha256": sha,
    }  # fmt: skip
    return {k: v for k, v in {**doc, **over}.items() if v is not ...}


def answered(sha: str, text: str = "line one\nline two", **over: Any) -> dict[str, Any]:
    return {"state": "done", "answer": text, "record_json": json.dumps(record(sha, **over))}


def post_report(api: Api, body: Any, run_id: int = 1) -> Any:
    return api.client.post(f"/sim/lab/runs/{run_id}/report", headers=WORKER, json=body)


def finish_all(api: Api) -> None:
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at")


def claimed(api: Api, who: str = MD, pid: str = "p-001") -> tuple[dict[str, Any], str]:
    assert put(api, who, pid).status_code == 201
    job = pclaim(api)
    assert job.status_code == 200
    return job.json(), rows(api)[-1]["context_sha256"]


# --- who may ask and read ----------------------------------------------------------------------


@pytest.mark.parametrize("who", [MD, RN, CHARGE])
def test_every_clinician_who_may_see_the_patient_asks_and_reads(api: Api, who: str) -> None:
    pid = MY[who]
    made = put(api, who, pid)
    assert made.status_code == 201
    assert made.json() == {
        "id": 1, "requested_by": who, "requested_at": "2026-10-14T12:00:00Z", "question": Q,
        "state": "queued", "claimed_at": None, "finished_at": None, "reason": None,
    }  # fmt: skip
    listed = listing(api, who, pid)
    assert listed.status_code == 200 and listed.json() == [made.json()]
    detail = one(api, who, pid, 1)
    assert detail.status_code == 200
    assert detail.json() == {**made.json(), "answer": None, "report": None}
    assert audit_of(api, who) == [
        ("ask_patient", pid, "ok"),
        ("view_patient_questions", pid, "ok"),
        ("view_patient_question", "patient_question:1", "ok"),
    ]


def test_an_assignee_who_did_not_ask_reads_it(api: Api) -> None:
    put(api, MD, "p-001")
    assert [r["id"] for r in listing(api, "rn-delacruz", "p-001").json()] == [1]
    assert one(api, "rn-delacruz", "p-001", 1).status_code == 200


@pytest.mark.parametrize("pid", ["p-003", "p-999"])
def test_a_patient_who_is_not_yours_or_missing_is_one_audited_404(api: Api, pid: str) -> None:
    for response in (put(api, MD, pid), listing(api, MD, pid), one(api, MD, pid, 1)):
        assert (response.status_code, response.json()) == (404, {"detail": "Not found"})
    assert audit_of(api, MD)[:3] == [
        ("ask_patient", pid, "not_found"),
        ("view_patient_questions", pid, "not_found"),
        ("view_patient_question", pid, "not_found"),
    ]
    assert rows(api) == []


def test_a_patient_who_has_left_is_not_found_and_a_lost_patient_cannot_be_read(api: Api) -> None:
    put(api, MD, "p-001")
    write(api, "DELETE FROM care_assignment WHERE patient_id = 'p-001' AND clinician_id = %s", MD)
    assert listing(api, MD, "p-001").status_code == 404
    assert one(api, MD, "p-001", 1).status_code == 404
    write(api, "UPDATE patient SET left_at = %s, outcome = 'discharged' WHERE id = 'p-006'", api.wall.now)
    assert put(api, MD, "p-006").status_code == 404
    assert listing(api, MD, "p-006").status_code == 404
    assert len(rows(api)) == 1


def test_an_admin_is_refused_on_all_three_routes_before_anything_is_read(api: Api) -> None:
    put(api, MD, "p-001")
    headers = add_admin(api)
    bad = api.client.post("/patients/p-999/questions", headers=headers, json={"bogus": 1})
    ok = api.client.post("/patients/p-001/questions", headers=headers, json={"question": Q})
    assert (bad.status_code, ok.status_code) == (403, 403)
    assert api.client.get("/patients/p-999/questions", headers=headers).status_code == 403
    assert api.client.get("/patients/p-001/questions/1", headers=headers).status_code == 403
    assert audit_of(api, ADMIN) == [
        ("ask_patient", "p-999", "denied"),
        ("ask_patient", "p-001", "denied"),
        ("view_patient_questions", "p-999", "denied"),
        ("view_patient_question", "p-001", "denied"),
    ]
    assert len(rows(api)) == 1


@pytest.mark.parametrize("qid", ["abc", "0", "01", "-1", "9" * 30, "999"])
def test_a_malformed_or_unknown_question_id_is_one_audited_404(api: Api, qid: str) -> None:
    put(api, MD, "p-001")
    response = one(api, MD, "p-001", qid)
    assert (response.status_code, response.json()) == (404, {"detail": "Not found"})
    assert audit_of(api, MD)[-1] == ("view_patient_question", "p-001", "not_found")


def test_a_question_is_read_only_through_its_own_patient(api: Api) -> None:
    put(api, MD, "p-001")
    assert one(api, MD, "p-002", 1).status_code == 404
    assert listing(api, MD, "p-002").json() == []


def test_a_not_ranked_patient_may_be_asked_about(api: Api) -> None:
    assert put(api, RN, "p-005").status_code == 201
    context = json.loads(rows(api)[0]["context"])
    assert set(context) == CONTEXT_KEYS
    assert (context["detail"], context["baseline"]) == (None, None)
    assert set(context["abstained"]) == {"reason", "instruction"}
    assert listing(api, RN, "p-005").status_code == 200


# --- the question ------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["x" * 500, "\U0001f600" * 500, "  spaced  "])
def test_a_question_is_stored_exactly_as_typed(api: Api, text: str) -> None:
    assert put(api, MD, "p-001", text).json()["question"] == text
    assert rows(api)[0]["question"] == text


BAD = [
    "", "x" * 501, "a\nb", "a\rb", "a\tb", "a\x7fb", "a\x85b", "a b", "a b", "a‮b",
    "a⁦b", "a\x00b", 7, None,
]  # fmt: skip


@pytest.mark.parametrize("text", BAD, ids=repr)
def test_a_bad_question_is_refused_and_nothing_is_stored(api: Api, text: Any) -> None:
    response = put(api, MD, "p-001", text)
    assert response.status_code == 422 and "xxxx" not in response.text
    assert rows(api) == []
    assert audit_of(api, MD) == [("ask_patient", "p-001", "rejected")]


@pytest.mark.parametrize(
    "content",
    ['{"question": "a\\ud800"}', "{nope", '{"question": "x", "patient_id": "p-001"}', "{}"],
)
def test_a_lone_surrogate_bad_json_or_an_extra_key_is_refused(api: Api, content: str) -> None:
    headers = {**api.as_(MD), "Content-Type": "application/json"}
    response = api.client.post("/patients/p-001/questions", headers=headers, content=content)
    assert response.status_code == 422 and rows(api) == []


# --- the frozen context ------------------------------------------------------------------------


def test_the_context_is_the_allow_list_and_its_hash_is_the_documented_one(api: Api) -> None:
    put(api, MD, "p-001")
    row = rows(api)[0]
    context = json.loads(row["context"])
    assert set(context) == CONTEXT_KEYS and set(context["detail"]) == DETAIL_KEYS
    assert context["format"] == 1 and context["synthetic"] is True and context["abstained"] is None
    assert context["as_of"] == "2026-09-03T07:00:00Z"
    assert row["context_sha256"] == sha_of(context)
    assert row["context"] == json.dumps(
        context, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert (row["run_id"], row["patient_id"], row["ward"]) == (1, "p-001", "W4")


def test_nothing_that_names_a_patient_or_another_patient_is_in_the_context(api: Api) -> None:
    write(api, "UPDATE patient SET name = 'MARK-NAME', bed = 'MARK-BED', admitting_context = 'MARK-ADM',"
              " mechanism = 'MARK-MECH' WHERE id = 'p-001'")
    write(api, "UPDATE patient SET name = 'MARK-OTHER' WHERE id = 'p-002'")
    note = {"author": "a", "role": "RN", "content": "MARK-NOTE"}
    assert api.client.post("/patients/p-001/notes", headers=api.as_(MD), json=note).status_code == 201
    assert api.client.post("/patients/p-001/labs", headers=api.as_(MD), json={"potassium": 4.0}).status_code == 200
    put(api, MD, "p-001")
    text = rows(api)[0]["context"]
    for marker in ("MARK-", "potassium", "tier_b", "tier_c", "p-001", "p-002", '"labs"', '"name"', '"bed"'):
        assert marker not in text
    assert "MARK-" not in listing(api, MD, "p-001").text


def test_the_history_is_cut_to_72_hours(api: Api) -> None:
    write(api, "UPDATE vital_bin SET bin_at = bin_at - interval '5 days' WHERE patient_id = 'p-001'"
              " AND vital = 'spo2' AND bin_at < '2026-09-03T03:00:00Z'")
    put(api, MD, "p-001")
    history = json.loads(rows(api)[0]["context"])["detail"]["vital_history"]
    assert len(history["heart_rate"]) == 24 and 0 < len(history["spo2"]) < 24


def test_a_context_over_the_cap_refuses_the_ask(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(patient_ask, "MAX_CONTEXT", 100)
    response = put(api, MD, "p-001")
    assert (response.status_code, response.json()) == (
        422, {"detail": "This patient's data is too large to ask about"},
    )
    assert rows(api) == []
    assert audit_of(api, MD) == [("ask_patient", "p-001", "rejected")]


# --- the claim, by kind ------------------------------------------------------------------------


def test_the_job_is_exactly_id_kind_question_and_patient_and_its_hash_matches(api: Api) -> None:
    job, stored = claimed(api)
    assert set(job) == {"id", "kind", "question", "patient"}
    assert (job["id"], job["kind"], job["question"]) == (1, "patient_question", Q)
    assert job["patient"] == json.loads(rows(api)[0]["context"])
    assert sha_of(job["patient"]) == stored  # what the worker computes on what it received
    assert rows(api)[0]["state"] == "running" and rows(api)[0]["claimed_at"] == api.wall.now
    assert api.sql("SELECT resource FROM audit_log WHERE action = 'lab_claim'") == [
        {"resource": "lab_run:1"}
    ]


def test_a_claim_with_no_kind_never_hands_out_a_patient_question(api: Api) -> None:
    put(api, MD, "p-001")
    ask(api, CHARGE)
    first = claim_compare(api)
    assert first.status_code == 200 and first.json()["kind"] == "compare" and first.json()["id"] == 2
    assert claim_compare(api).status_code == 204  # the question stays queued
    assert rows(api)[0]["state"] == "queued"
    assert pclaim(api, {"kind": "compare"}).status_code == 204


def test_a_running_compare_does_not_block_a_question_and_the_reverse(api: Api) -> None:
    ask(api)
    assert claim_compare(api).status_code == 200
    put(api, MD, "p-001")
    assert pclaim(api).status_code == 200
    ask(api, CHARGE)
    assert claim_compare(api).status_code == 204  # one running compare
    put(api, CHARGE, "p-001")
    assert pclaim(api).status_code == 204  # one running question


def test_a_burst_of_question_claims_hands_out_one(api: Api) -> None:
    put(api, MD, "p-001")
    put(api, CHARGE, "p-001")
    assert sorted(together(4, lambda _: pclaim(api).status_code)) == [200, 204, 204, 204]


@pytest.mark.parametrize(
    "query",
    ["kind=other", "kind=", "kind=Patient_Question", "kind=compare&kind=patient_question",
     "kind=patient_question&kind=patient_question"],
)  # fmt: skip
def test_an_unknown_empty_or_repeated_kind_is_422_and_changes_nothing(api: Api, query: str) -> None:
    put(api, MD, "p-001")
    response = api.client.post(f"/sim/lab/claim?{query}", headers=WORKER)
    assert response.status_code == 422
    assert rows(api)[0]["state"] == "queued"
    assert api.sql("SELECT * FROM audit_log WHERE action = 'lab_claim'") == []


def test_a_wrong_key_with_a_bad_kind_is_403_first(api: Api) -> None:
    response = api.client.post("/sim/lab/claim?kind=other", headers={"X-Ingest-Key": "wrong"})
    assert response.status_code == 403
    assert api.sql("SELECT outcome FROM audit_log WHERE action = 'ingest'") == [{"outcome": "denied"}]


# --- the report --------------------------------------------------------------------------------


def test_a_done_report_is_stored_and_the_clinician_reads_the_answer(api: Api) -> None:
    _, sha = claimed(api)
    body = answered(sha, "line one\nline two", extra_key={"a": 1})
    assert post_report(api, body).status_code == 204
    row = rows(api)[0]
    assert (row["state"], row["report_text"], row["finished_at"]) == ("done", "line one\nline two", api.wall.now)
    assert row["report"]["extra_key"] == {"a": 1} and row["report"]["data_sha256"] == sha
    seen = one(api, "rn-delacruz", "p-001", 1).json()
    assert seen["state"] == "done" and seen["answer"] == "line one\nline two"
    assert seen["report"]["kind"] == "own_baseline"
    assert "context" not in seen and "patient" not in seen
    assert api.sql("SELECT action, resource, outcome FROM audit_log WHERE action = 'lab_report'") == [
        {"action": "lab_report", "resource": "lab_run:1", "outcome": "ok"}
    ]


MISMATCH = {
    "format 2": {"patient_question_format": 2},
    "format true": {"patient_question_format": True},
    "format string": {"patient_question_format": "1"},
    "no format": {"patient_question_format": ...},
    "synthetic false": {"synthetic": False},
    "synthetic 1": {"synthetic": 1},
    "no synthetic": {"synthetic": ...},
    "grounding failed": {"grounding": "failed"},
    "grounding true": {"grounding": True},
    "no grounding": {"grounding": ...},
    "no hash": {"data_sha256": ...},
    "hash number": {"data_sha256": 5},
    "hash of another context": {"data_sha256": SHA},
}


@pytest.mark.parametrize("over", MISMATCH.values(), ids=MISMATCH.keys())
def test_a_done_report_that_does_not_match_fails_the_run(api: Api, over: dict[str, Any]) -> None:
    claimed(api)
    response = post_report(api, answered(rows(api)[0]["context_sha256"], **over))
    assert (response.status_code, response.json()) == (
        422, {"detail": "The report does not match the request"},
    )
    row = rows(api)[0]
    assert (row["state"], row["reason"]) == ("failed", "the report did not match the request")
    assert audit_of_report(api) == "rejected"


def audit_of_report(api: Api) -> str:
    return api.sql("SELECT outcome FROM audit_log WHERE action = 'lab_report'")[-1]["outcome"]


def test_the_hash_off_by_one_hex_digit_fails(api: Api) -> None:
    _, sha = claimed(api)
    last = "0" if sha[-1] != "0" else "1"
    assert post_report(api, answered(sha[:-1] + last)).status_code == 422
    assert rows(api)[0]["state"] == "failed"


@pytest.mark.parametrize("record_json", ["nope", "[]", '{"a": NaN}', "[" * 65 + "]" * 65])
def test_a_record_that_is_not_an_object_or_not_json_fails_the_run(api: Api, record_json: str) -> None:
    claimed(api)
    body = {"state": "done", "answer": "x", "record_json": record_json}
    assert post_report(api, body).status_code == 422
    assert rows(api)[0]["state"] == "failed"


def test_an_empty_answer_fails_the_run(api: Api) -> None:
    _, sha = claimed(api)
    assert post_report(api, answered(sha, "")).status_code == 422
    assert rows(api)[0]["state"] == "failed"


def test_a_compare_shaped_done_on_a_question_and_the_reverse_fail(api: Api) -> None:
    claimed(api)
    assert post_report(api, done()).status_code == 422
    assert rows(api)[0]["state"] == "failed"
    running(api)  # a compare run, id 2
    assert post_report(api, answered(rows(api)[0]["context_sha256"]), 2).status_code == 422
    assert api.sql("SELECT state FROM lab_run WHERE id = 2") == [{"state": "failed"}]
    running(api)  # id 3
    assert post_report(api, done(), 3).status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {"state": "done", "answer": "x" * 4001, "record_json": "{}"},
        {"state": "done", "answer": "x", "record_json": " " * 131073},
        {"state": "done", "answer": "a\rb", "record_json": "{}"},
        {"state": "done", "answer": "a\tb", "record_json": "{}"},
        {"state": "done", "answer": "a b", "record_json": "{}"},
        {"state": "done", "answer": "x", "record_json": "{}", "compare_json": "{}", "compare_txt": "t"},
        {"state": "done", "answer": "x"},
        {"state": "answer", "answer": "x", "record_json": "{}"},
    ],
)
def test_a_report_that_breaks_the_shape_or_a_cap_changes_nothing(api: Api, body: Any) -> None:
    claimed(api)
    assert post_report(api, body).status_code == 422
    assert rows(api)[0]["state"] == "running"


def test_the_largest_allowed_answer_and_record_are_accepted(api: Api) -> None:
    _, sha = claimed(api)
    body = answered(sha, "t" * 4000, pad="x" * 120000)
    assert len(body["record_json"]) <= 131072
    assert post_report(api, body).status_code == 204


@pytest.mark.parametrize(
    ("body", "state"),
    [
        ({"state": "refused", "reason": "Refused: a number in the answer is not in this patient's data."}, "refused"),
        ({"state": "failed", "reason": "the agent stopped with an error"}, "failed"),
    ],
)
def test_a_refused_or_failed_report_is_final(api: Api, body: dict[str, str], state: str) -> None:
    claimed(api)
    assert post_report(api, body).status_code == 204
    row = rows(api)[0]
    assert (row["state"], row["reason"]) == (state, body["reason"])
    assert post_report(api, body).status_code == 409
    seen = one(api, MD, "p-001", 1).json()
    assert (seen["state"], seen["reason"], seen["answer"]) == (state, body["reason"], None)


def test_a_report_for_a_queued_question_is_409(api: Api) -> None:
    put(api, MD, "p-001")
    assert post_report(api, {"state": "failed", "reason": "x"}).status_code == 409


# --- limits ------------------------------------------------------------------------------------


def test_one_open_question_for_each_clinician_and_patient(api: Api) -> None:
    assert put(api, MD, "p-001").status_code == 201
    second = put(api, MD, "p-001")
    assert (second.status_code, second.json()) == (429, {"detail": "Question limit reached"})
    assert put(api, MD, "p-002").status_code == 201  # another patient
    assert put(api, CHARGE, "p-001").status_code == 201  # another clinician
    assert audit_of(api, MD) == [
        ("ask_patient", "p-001", "ok"), ("ask_patient", "p-001", "limited"), ("ask_patient", "p-002", "ok"),
    ]
    pclaim(api)  # running still counts as open
    assert put(api, MD, "p-001").status_code == 429
    post_report(api, {"state": "failed", "reason": "x"})
    assert put(api, MD, "p-001").status_code == 201


def test_twenty_in_a_day_and_the_next_day_is_allowed(api: Api) -> None:
    for _ in range(20):
        assert put(api, MD, "p-001").status_code == 201
        finish_all(api)
    assert put(api, MD, "p-001").status_code == 429
    api.wall.now += timedelta(hours=24, seconds=1)
    api.tokens.clear()
    assert put(api, MD, "p-001").status_code == 201


def test_five_queued_on_the_ward(api: Api) -> None:
    for n in range(1, 6):
        assert put(api, CHARGE, f"p-00{n}").status_code == 201
    assert put(api, CHARGE, "p-006").status_code == 429
    write(api, "UPDATE lab_run SET state = 'failed', reason = 'x', finished_at = requested_at WHERE id = 1")
    assert put(api, CHARGE, "p-006").status_code == 201


def test_a_burst_of_asks_passes_the_limit_once(api: Api) -> None:
    api.as_(MD)
    codes = together(6, lambda _: put(api, MD, "p-001").status_code)
    assert sorted(codes) == [201, 429, 429, 429, 429, 429]


def test_the_compare_limits_and_the_question_limits_never_count_each_other(api: Api) -> None:
    assert ask(api).status_code == 201  # an open compare
    assert put(api, MD, "p-001").status_code == 201
    assert ask(api).status_code == 429  # the compare limit is its own
    assert put(api, MD, "p-002").status_code == 201
    for n in range(3, 6):
        assert put(api, CHARGE, f"p-00{n}").status_code == 201
    assert len(api.sql("SELECT 1 FROM lab_run WHERE kind = 'patient_question' AND state = 'queued'")) == 5
    assert ask(api, CHARGE).status_code == 201  # five queued questions do not fill the compare queue


# --- the ward's compare routes never serve a question ------------------------------------------


def test_the_ward_lab_routes_never_list_or_serve_a_patient_question(api: Api) -> None:
    put(api, MD, "p-001")
    ask(api, CHARGE)
    listed = api.client.get("/wards/W4/lab/runs", headers=api.as_(RN)).json()
    assert [r["id"] for r in listed] == [2]
    assert api.client.get("/wards/W4/lab/runs/1", headers=api.as_(RN)).status_code == 404
    assert api.client.get("/wards/W4/lab/runs/2", headers=api.as_(RN)).status_code == 200


# --- timeouts ----------------------------------------------------------------------------------


def test_a_queued_question_fails_after_an_hour_and_a_running_one_after_twenty_minutes(api: Api) -> None:
    put(api, MD, "p-001")
    api.wall.now += timedelta(hours=1, seconds=30)
    assert listing(api, MD, "p-001").json()[0]["reason"] == "no worker claimed it within an hour"
    claimed(api, CHARGE)
    api.wall.now += timedelta(minutes=21)
    seen = one(api, CHARGE, "p-001", 2).json()
    assert (seen["state"], seen["reason"]) == ("failed", "the worker did not report within 20 minutes")


# --- privacy -----------------------------------------------------------------------------------


def test_the_question_and_the_context_are_in_no_audit_row_and_no_log(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    marker = "MARKER-QUESTION-8472"
    assert put(api, MD, "p-001", marker).status_code == 201
    listing(api, MD, "p-001")
    one(api, MD, "p-001", 1)
    assert pclaim(api).status_code == 200
    sha = rows(api)[0]["context_sha256"]
    post_report(api, answered(sha))
    put(api, MD, "p-002", marker)
    pclaim(api)
    post_report(api, {"state": "refused", "reason": "Refused: nope"}, 2)
    put(api, MD, "p-001", marker + "\n")  # a refused body
    dump = json.dumps(api.sql("SELECT * FROM audit_log"), default=str)
    assert marker not in dump and marker not in caplog.text
    context = rows(api)[0]["context"]
    assert context not in dump and sha not in dump
    assert "Research finding" not in dump and "Research finding" not in caplog.text


# --- the table's own checks --------------------------------------------------------------------


def insert(api: Api, **over: Any) -> None:
    row: dict[str, Any] = {
        "ward": "W4", "requested_by": MD, "requested_at": api.wall.now, "kind": "patient_question",
        "question": "q", "run_id": 1, "patient_id": "p-001", "context": "{}", "context_sha256": SHA,
        "state": "queued",
    }  # fmt: skip
    row = {k: v for k, v in {**row, **over}.items() if v is not ...}
    write(api, f"INSERT INTO lab_run ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", *row.values())


def test_the_table_takes_a_question_row_and_refuses_each_broken_one(api: Api) -> None:
    insert(api)
    assert len(rows(api)) == 1
    compare = {"kind": "compare", "question": ..., "run_id": ..., "patient_id": ..., "context": ...,
               "context_sha256": ..., "mode": "w4", "seed": 1, "days": 1,
               "baseline": "news2_total_ge_5", "change": "news2_chart_trigger"}  # fmt: skip
    insert(api, **compare)
    broken: list[dict[str, Any]] = [
        {"mode": "w4"}, {"seed": 1}, {"days": 1}, {"baseline": "news2_total_ge_5"},
        {"change": "news2_chart_trigger"}, {"question": ...}, {"run_id": ...}, {"patient_id": ...},
        {"context": ...}, {"context_sha256": ...}, {"question": ""}, {"question": "a\nb"},
        {"question": "x" * 501}, {"context_sha256": "A" * 64}, {"context_sha256": "a" * 63},
        {"context": "x" * 262145}, {"patient_id": "p-999"}, {"kind": "research"},
        {"state": "done", "claimed_at": api.wall.now, "finished_at": api.wall.now, "report_text": "t"},
    ]  # fmt: skip
    for over in broken:
        with pytest.raises(psycopg.errors.Error):
            insert(api, **over)
    for over in ({"question": "q"}, {"run_id": 1}, {"context": "{}"}, {"mode": ...}):
        with pytest.raises(psycopg.errors.Error):
            insert(api, **{**compare, **over})
    assert len(api.sql("SELECT 1 FROM lab_run")) == 2


def test_a_question_goes_with_its_patient(api: Api) -> None:
    put(api, MD, "p-001")
    with psycopg.connect(api.url) as conn:
        conn.execute("DELETE FROM care_assignment WHERE patient_id = 'p-001'")
        conn.execute("DELETE FROM observation WHERE patient_id = 'p-001'")
        conn.execute("DELETE FROM vital_bin WHERE patient_id = 'p-001'")
        conn.execute("DELETE FROM patient WHERE id = 'p-001'")
    assert rows(api) == []
