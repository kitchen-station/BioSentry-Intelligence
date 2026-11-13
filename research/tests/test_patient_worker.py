import json
import re

import pytest
from patient_fixtures import abstained, patient
from test_agent_worker import Clock, Fake

from research.agent import worker as base
from research.agent.config import AgentError
from research.patient_agent import __main__ as cli
from research.patient_agent import ask, worker

KEY = "fake-ingest-key-0123456789"
URL = "http://api.test:8000"
CLAIM = f"{URL}/sim/lab/claim?kind=patient_question"
Q = "which alerts are active"


def job(**kw):
    return (200, {"id": 31, "kind": "patient_question", "question": Q, "patient": patient(), **kw})


def go(fake, args=(), commit=None, max_polls=1):
    clock, lines = Clock(), []
    env = {"BIOSENTRY_API_URL": URL, "BIOSENTRY_INGEST_KEY": KEY, **({"BIOSENTRY_AGENT_COMMIT": commit} if commit else {})}
    worker.run(list(args), env, fake, clock.sleep, clock, lines.append, max_polls)
    assert not fake.violations
    return lines


def body(fake, i=0):
    return json.loads(fake.reports[i][3])


def test_done_round_trip():
    fake = Fake(job())
    lines = go(fake, commit="d21de25")
    assert fake.claims[0][:2] == ("POST", CLAIM) and fake.claims[0][3] == b""
    assert fake.reports[0][1] == f"{URL}/sim/lab/runs/31/report"
    rep = body(fake)
    assert set(rep) == {"state", "record_json", "answer"} and rep["state"] == "done"
    assert rep["answer"] == ask.answer(patient(), Q)["text"]
    rec = json.loads(rep["record_json"])
    assert set(rec) == {"patient_question_format", "synthetic", "grounding", "agent", "kind", "args", "numbers", "data_sha256"}
    assert rec["patient_question_format"] == 1 and rec["synthetic"] is True and rec["grounding"] == "ok"
    assert rec["agent"] == {"ask": "keywords", "commit": "d21de25"} and rec["kind"] == "active_alerts" and rec["args"] == {}
    assert rec["numbers"] == ["5", "40"]
    assert len(rec["data_sha256"]) == 64 and rec["data_sha256"] == worker.data_sha256(patient())
    assert lines == ["worker started: http://api.test", "connection: connected", "job 31: done"]


def test_the_hash_is_the_canonical_form():
    import hashlib
    p = {"b": "é", "a": [1, 2.5, None]}
    want = hashlib.sha256('{"a":[1,2.5,null],"b":"é"}'.encode()).hexdigest()
    assert worker.data_sha256(p) == want == worker.data_sha256({"a": [1, 2.5, None], "b": "é"})


def test_the_record_holds_no_patient_data():
    fake = Fake(job())
    go(fake)
    raw = body(fake)["record_json"]
    for secret in ("Test Person", "B4", "p-1", "reached the trigger", "cardiac"):
        assert secret not in raw


@pytest.mark.parametrize("q", ["should we give fluids", "tell me more", "what is the baseline for 5"])
def test_refusals_carry_the_fixed_reason(q):
    fake = Fake(job(question=q))
    go(fake)
    assert body(fake) == {"state": "refused", "reason": "Refused: this agent answers only fixed kinds of question about "
                                                         "this patient's own data."}


def test_ungrounded_is_refused_never_done(monkeypatch):
    monkeypatch.setattr(ask, "grounded", lambda *a: False)
    fake = Fake(job())
    go(fake)
    assert body(fake) == {"state": "refused", "reason": "Refused: a number in the answer is not in this patient's data."}


def test_bad_data_and_errors_are_failed_with_fixed_reasons(monkeypatch):
    fake = Fake(job(patient=patient(detail={"escalation_reasons": 5})))
    go(fake)
    assert body(fake) == {"state": "failed", "reason": "the agent stopped with an error"}

    def boom(*a, **k):
        raise RuntimeError("/secret/path with the patient text")
    monkeypatch.setattr(ask, "answer", boom)
    fake = Fake(job())
    lines = go(fake)
    assert body(fake) == {"state": "failed", "reason": "the agent stopped with an error"}
    assert "secret" not in json.dumps(fake.calls, default=str) + " ".join(lines)


def test_a_deeply_nested_patient_is_failed_not_a_crash():
    deep = cur = {}
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    p = patient()
    p["detail"]["extra"] = deep
    assert worker.report_for({"question": Q, "patient": p}, None)["state"] in ("failed", "done")  # never an exception


@pytest.mark.parametrize("text, pad", [("a" * 4001, 0), ("", 0), ("fine", 131072)])
def test_over_cap_is_failed(monkeypatch, text, pad):
    monkeypatch.setattr(ask, "answer", lambda p, q, route=None: {
        "status": "answered", "text": text, "kind": "alerts", "args": {}, "numbers": ["x" * pad], "planner": "keywords"})
    fake = Fake(job())
    go(fake)
    assert body(fake) == {"state": "failed", "reason": "the agent's answer is over the size cap"}


def test_the_model_planner_is_in_the_record(monkeypatch):
    monkeypatch.setattr(ask, "answer", lambda p, q, route=None: {
        "status": "answered", "text": "ok.", "kind": "alerts", "args": {}, "numbers": [], "planner": "tuned-model"})
    fake = Fake(job())
    go(fake)
    assert json.loads(body(fake)["record_json"])["agent"]["ask"] == "tuned-model"


def test_a_wrong_kind_stops_the_worker():
    fake = Fake((200, {"id": 7, "kind": "question", "question": Q, "patient": patient()}), (204, b""))
    with pytest.raises(AgentError, match="does not serve"):
        go(fake, max_polls=5)
    assert len(fake.claims) == 1 and body(fake) == {"state": "refused", "reason": "the worker does not accept this job: kind"}


@pytest.mark.parametrize("q", ["", "x" * 501, "a\nb", "a\x00b", 5, None])
def test_a_bad_question_is_refused(monkeypatch, q):
    monkeypatch.setattr(ask, "answer", lambda *a, **k: pytest.fail("ran"))
    fake = Fake(job(question=q))
    go(fake)
    assert body(fake) == {"state": "refused", "reason": "the worker does not accept this job: question"}


def cut(**kw):
    p = patient()
    p.update(kw)
    return p


@pytest.mark.parametrize("p", [None, 5, "x", [], {}, {"detail": None, "abstained": None},
                               {"detail": {}, "abstained": {}}, {"detail": 5, "abstained": None},
                               cut(format=2), cut(format=True), cut(format="1"), cut(format=1.0), cut(synthetic=1), cut(synthetic=False), cut(synthetic=None),
                               cut(synthetic="yes"), cut(abstained={"reason": "a", "instruction": "b"}),
                               cut(detail=None), {k: v for k, v in patient().items() if k != "format"},
                               {k: v for k, v in patient().items() if k != "synthetic"}])
def test_a_bad_patient_is_refused(p):
    fake = Fake(job(patient=p))
    go(fake)
    assert body(fake) == {"state": "refused", "reason": "the worker does not accept this job: patient"}


def test_a_missing_patient_is_refused():
    fake = Fake((200, {"id": 3, "kind": "patient_question", "question": Q}))
    go(fake)
    assert body(fake)["reason"].endswith("patient")


def test_not_ranked_is_done():
    fake = Fake(job(patient=abstained()))
    go(fake)
    assert body(fake)["state"] == "done" and "NOT RANKED" in body(fake)["answer"]


def test_no_log_line_holds_data_and_the_key_stays_in_the_header():
    p = patient()
    p["detail"]["supporting"] = "SECRETTEXT"
    fake = Fake(job(patient=p, question="why is this patient flagged"), OSError(KEY))
    lines = go(fake, max_polls=2)
    text = "\n".join(lines)
    assert "SECRETTEXT" not in text and "flagged" not in text and KEY not in text
    assert all(re.fullmatch(r"worker started: http://api.test|connection: \w+|job \d+: [a-z ]+", x) for x in lines)
    for _, url, headers, raw, _t in fake.calls:
        assert KEY not in url and KEY.encode() not in raw


def test_a_large_claim_is_accepted():
    p = patient()
    p["detail"]["unknown"] = "x" * 200_000
    fake = Fake(job(patient=p))
    go(fake)
    assert body(fake)["state"] == "done"


def test_the_transport_cap_is_the_claim_cap(monkeypatch):
    seen = {}
    monkeypatch.setattr(base, "urllib_transport", lambda *a, **k: seen.update(k) or (204, b""))
    clock = Clock()
    worker.run([], {"BIOSENTRY_API_URL": URL, "BIOSENTRY_INGEST_KEY": KEY}, None, clock.sleep, clock, lambda x: None, 1)
    assert seen == {"cap": 524288}


def test_the_command_refuses_with_one_line(capsys):
    assert cli.main([]) == 2 and cli.main(["worker", "--bad"]) == 2
    assert capsys.readouterr().err.count("\n") == 2
    assert cli.main(["worker"], {"BIOSENTRY_API_URL": "x", "BIOSENTRY_INGEST_KEY": KEY}) == 2
    assert KEY not in capsys.readouterr().err


def test_a_nan_in_the_patient_is_failed_not_done():
    p = patient()
    p["detail"]["unknown"] = float("nan")
    assert worker.report_for({"question": Q, "patient": p}, None) == {"state": "failed", "reason": "the agent stopped with an error"}
    with pytest.raises(ValueError):
        worker.data_sha256(p)


def test_the_hash_matches_the_contract_form_for_a_real_context():
    import hashlib
    p = patient()
    canonical = json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert worker.data_sha256(p) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("q", ["Show all alerts of this stay", "alerts yesterday", "what is the alert history"])
def test_an_alert_history_question_gets_the_third_reason(q):
    fake = Fake(job(question=q))
    go(fake)
    assert body(fake) == {"state": "refused", "reason": "Refused: the alert history of the stay is not available to this agent yet."}


def test_followed_by_a_deterioration_keeps_the_general_reason():
    fake = Fake(job(question="was an alert followed by a deterioration"))
    go(fake)
    assert body(fake)["reason"].startswith("Refused: this agent answers only fixed kinds")


@pytest.mark.parametrize("kind", ["own_baseline", "recent_change", "why_rank", "active_alerts"])
def test_the_record_names_each_kind(kind):
    q = {"own_baseline": "own baseline", "recent_change": "what changed in the last 2 hours",
         "why_rank": "why is this patient flagged", "active_alerts": "which alerts are active"}[kind]
    fake = Fake(job(question=q))
    go(fake)
    assert json.loads(body(fake)["record_json"])["kind"] == kind
