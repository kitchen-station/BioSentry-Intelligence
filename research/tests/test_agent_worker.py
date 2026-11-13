import json

import pytest
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS

from research.agent import __main__ as cli
from research.agent import ask, ledger, tools, worker
from research.agent.config import AgentError
from test_agent_planner import env_for
from test_agent_tools import cfg_for, toy_trials

KEY = "fake-ingest-key-0123456789"
BASE = "http://api.test:8000"
Q = "What if we alerted at a NEWS2 total of 5 or more?"
CLAIM = f"{BASE}/sim/lab/claim?kind=question"
NO_ID = "ignored a job with no usable id"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.evaluate, "_baseline_params", lambda: (DEFAULTS, "test"))
    c = cfg_for(tmp_path)
    toy_trials(c, range(1, 11))
    c.models.mkdir()
    return c


class Fake:
    """A transport that plays the contract's answers. `script` is a list of (status, body) or an Exception,
    used in order; when it runs out, claims and reports get 204. Order violations are collected, because the
    worker swallows an exception raised here."""

    def __init__(self, *script):
        self.script, self.calls, self.violations, self.running = list(script), [], [], False
        self.strict = True  # off for a broken API that hands a job back without a report in between

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        is_claim = "/claim" in url
        if is_claim and self.running and self.strict:
            self.violations.append("claimed while a job runs")
        item = self.script.pop(0) if self.script else (204, b"")
        if isinstance(item, Exception):
            raise item
        status, data = item
        if is_claim and status == 200:
            self.running = True
        if not is_claim and status in (204, 404, 409, 422):
            self.running = False
        return status, data if isinstance(data, bytes) else json.dumps(data).encode()

    @property
    def claims(self):
        return [c for c in self.calls if "/claim" in c[1]]

    @property
    def reports(self):
        return [c for c in self.calls if "/report" in c[1]]


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def job(**kw):
    return (200, {"id": 31, "kind": "question", "question": Q, **kw})


def go(cfg, fake, args=(), env=None, max_polls=None, commit=None):
    clock, lines = Clock(), []
    env = env or {"BIOSENTRY_API_URL": BASE, "BIOSENTRY_INGEST_KEY": KEY,
                  **({"BIOSENTRY_AGENT_COMMIT": commit} if commit else {})}
    worker.run(cfg, list(args), env, fake, clock.sleep, clock, lines.append, max_polls)
    assert not fake.violations
    return lines, clock


def body_of(call):
    return json.loads(call[3])


# --- a round trip ------------------------------------------------------------------------------------------------

def test_done_round_trip_with_the_real_agent(cfg):
    fake = Fake(job())
    lines, _ = go(cfg, fake, max_polls=2, commit="d21de25")
    (m, url, headers, body, timeout), = fake.reports
    assert fake.claims[0][:2] == ("POST", CLAIM) and fake.claims[0][3] == b""
    assert fake.claims[0][2] == {"X-Ingest-Key": KEY} and timeout == 30
    assert (m, url, headers["X-Ingest-Key"], headers["Content-Type"]) == (
        "POST", f"{BASE}/sim/lab/runs/31/report", KEY, "application/json")
    rep = json.loads(body)
    assert set(rep) == {"state", "record_json", "answer"} and rep["state"] == "done"
    assert rep["answer"] == ask.ask(cfg, Q)
    rec = json.loads(rep["record_json"])
    assert rec["question_format"] == 1 and type(rec["question_format"]) is int
    assert rec["synthetic"] is True and rec["grounding"] == "ok"
    assert set(rec) == {"question_format", "synthetic", "grounding", "agent", "experiment", "numbers"}
    assert rec["agent"] == {"ask": "keywords", "step": "S-0001", "commit": "d21de25"}
    assert rec["experiment"]["template"] == "news2_threshold" and rec["experiment"]["tool"] == "evaluate_rules"
    assert len(rec["experiment"]["result_sha256"]) == 64 and rec["numbers"][0] == "10"
    assert lines == ["worker started: http://api.test", "connection: connected", "job 31: done"]
    assert len(fake.claims) == 2  # claimed again at once


def test_a_bad_commit_is_null(cfg):
    fake = Fake(job())
    go(cfg, fake, max_polls=1, commit="ABCDEF1")
    assert json.loads(json.loads(fake.reports[0][3])["record_json"])["agent"]["commit"] is None


def test_no_template_is_refused(cfg):
    fake = Fake(job(question="What is the capital of France?"))
    lines, _ = go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "refused", "reason": "Refused: no template fits this question."}
    assert lines[-1] == "job 31: refused"


def test_ungrounded_is_refused_never_done(cfg, monkeypatch):
    monkeypatch.setattr(ask, "grounded", lambda *a: False)
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "refused",
                                        "reason": "Refused: a number in the answer is not in the tool result."}


@pytest.mark.parametrize("exc, reason", [
    (tools.ToolError("/secret/folder/seed-03 missing"), "the agent's tool failed"),
    (ledger.LedgerError("/secret/home/ledger.jsonl is full"), "the agent's ledger could not be written"),
    (RuntimeError("/secret/boom"), "the agent stopped with an error"),
])
def test_errors_map_to_fixed_reasons_without_their_text(cfg, monkeypatch, exc, reason):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(ask, "answer", boom)
    fake = Fake(job())
    lines, _ = go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": reason}
    assert "secret" not in json.dumps(fake.calls, default=str) + " ".join(lines)


def test_a_real_tool_error_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.evaluate, "_baseline_params", lambda: (DEFAULTS, "test"))
    empty = cfg_for(tmp_path)  # no trial data
    fake = Fake(job())
    go(empty, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": "the agent's tool failed"}


def fake_answer(text="ok.", record_pad=0):
    def answer(cfg, q):
        return {"status": "answered", "text": text, "step": "S-0001", "template": "t", "args": {},
                "result": {"pad": "x" * record_pad}, "result_sha256": "0" * 64, "numbers": []}
    return answer


@pytest.mark.parametrize("text, pad", [("a" * 4001, 0), ("", 0), ("fine", 131072)])
def test_over_cap_is_failed_and_nothing_is_cut(cfg, monkeypatch, text, pad):
    monkeypatch.setattr(ask, "answer", fake_answer(text, pad))
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": "the agent's answer is over the size cap"}


@pytest.mark.parametrize("text", ["a\x00b", "a‮b", "a\ud800b"])
def test_a_forbidden_character_is_an_error_not_a_size_problem(cfg, monkeypatch, text):
    monkeypatch.setattr(ask, "answer", fake_answer(text))
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": "the agent stopped with an error"}


def test_done_is_only_for_answered(cfg, monkeypatch):
    monkeypatch.setattr(ask, "answer", lambda c, q: {**fake_answer()(c, q), "status": "weird"})
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": "the agent stopped with an error"}


def test_nan_in_the_record_is_an_error(cfg, monkeypatch):
    def nan(c, q):
        return {**fake_answer()(c, q), "result": {"x": float("nan")}}
    monkeypatch.setattr(ask, "answer", nan)
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "failed", "reason": "the agent stopped with an error"}


def test_the_same_job_handed_back_is_not_run_again(cfg):
    fake = Fake(job(), (204, b""), job(), job(question="x" * 501, id=9), (204, b""), job(id=9))
    fake.strict = False
    lines, clock = go(cfg, fake, max_polls=5)
    assert len(fake.reports) == 2 and len(ledger.read(cfg)) == 2  # id 31 once, id 9 refused once
    assert lines.count("job 31: ignored, already handled") == 1 and lines.count("job 9: ignored, already handled") == 1
    assert clock.slept == [30, 30, 30]  # each repeat waits; the last is the idle poll


def test_the_handled_set_is_bounded(cfg, monkeypatch):
    monkeypatch.setattr(worker, "HANDLED_KEEP", 2)
    fake = Fake(*[x for i in (1, 2, 3) for x in (job(id=i, question="x" * 501), (204, b""))], job(id=1, question="x" * 501))
    go(cfg, fake, max_polls=4)
    assert len(fake.reports) == 4  # id 1 was forgotten, so it ran again


def test_an_answer_at_the_cap_with_lf_is_sent_whole(cfg, monkeypatch):
    text = ("a" * 1999 + "\n") * 2
    monkeypatch.setattr(ask, "answer", fake_answer(text))
    fake = Fake(job())
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0])["answer"] == text


# --- the job re-check --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False, 0, -1, 2**53, 1.5, 31.0, "31", None, [31]])
def test_an_unusable_id_is_ignored_and_nothing_is_reported(cfg, bad):
    fake = Fake((200, {"id": bad, "kind": "question", "question": Q}))
    lines, _ = go(cfg, fake, max_polls=1)
    assert not fake.reports and NO_ID in lines


def test_the_largest_id_is_used_in_the_url(cfg):
    fake = Fake(job(id=2**53 - 1))
    go(cfg, fake, max_polls=1)
    assert fake.reports[0][1].endswith(f"/runs/{2**53 - 1}/report")


FORBIDDEN = ["a\nb", "a\x00b", "a\tb", "a\x1fb", "a\x7fb", "a\x80b", "a\x9fb", "a b", "a b",
             "a‪b", "a‮b", "a⁦b", "a⁩b", "a\ud800b", "a\udfffb"]


@pytest.mark.parametrize("q", ["", "x" * 501, *FORBIDDEN, 5, None, ["a"], True])
def test_a_bad_question_is_refused_without_running(cfg, monkeypatch, q):
    monkeypatch.setattr(ask, "answer", lambda *a: pytest.fail("ran"))
    fake = Fake((200, {"id": 7, "kind": "question", "question": q}))
    go(cfg, fake, max_polls=1)
    assert body_of(fake.reports[0]) == {"state": "refused", "reason": "the worker does not accept this job: question"}


@pytest.mark.parametrize("kind", ["compare", "", None, 3])
def test_a_wrong_kind_is_refused_once_and_the_worker_stops(cfg, kind, monkeypatch):
    """An API that ignores ?kind=question hands out compare runs. The worker refuses one and never claims again."""
    monkeypatch.setattr(ask, "answer", lambda *a: pytest.fail("a job of another kind must not run"))
    fake = Fake((200, {"id": 7, "kind": kind, "question": Q}), (204, b""), (200, {"id": 8, "kind": kind, "question": Q}))
    with pytest.raises(AgentError, match="does not serve"):
        go(cfg, fake, max_polls=5)
    assert len(fake.claims) == 1 and len(fake.reports) == 1 and not fake.violations
    assert body_of(fake.reports[0]) == {"state": "refused", "reason": "the worker does not accept this job: kind"}


def test_a_wrong_kind_ends_the_command_with_exit_2_and_one_line(cfg, capsys):
    fake = Fake((200, {"id": 7, "kind": "compare", "mode": "ward"}))
    env = {"BIOSENTRY_API_URL": BASE, "BIOSENTRY_INGEST_KEY": KEY}
    clock, lines = Clock(), []
    with pytest.raises(AgentError) as e:  # `main` turns an AgentError into one `refused: ...` line and exit 2
        worker.run(cfg, [], env, fake, clock.sleep, clock, lines.append, 5)
    line = f"refused: {e.value}"
    assert "\n" not in line and KEY not in line and "compare" not in line
    assert lines[-1] == "job 7: refused" and len(fake.claims) == 1


def test_a_bad_question_does_not_stop_the_worker(cfg):
    fake = Fake(job(id=1, question=""), job(id=2, question=""))
    go(cfg, fake, max_polls=2)
    assert len(fake.claims) == 2 and len(fake.reports) == 2


def test_500_code_points_of_allowed_unicode_are_accepted_and_unknown_fields_ignored(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(ask, "answer", lambda c, q: seen.append(q) or fake_answer()(c, q))
    q = ("é ­日\U0001f600" * 100)[:500]
    assert len(q) == 500
    fake = Fake(job(question=q, extra="x"))
    go(cfg, fake, max_polls=1)
    assert seen == [q] and body_of(fake.reports[0])["state"] == "done"


# --- the question is data ----------------------------------------------------------------------------------------

ATTACKS = ["threshold of 5; rm -rf ~", "threshold of 5 `id`", "threshold of 5 $(id)", "../../etc/passwd threshold of 5",
           "{0.__class__} threshold of 5", "%s%n threshold of 5", 'threshold of 5 "} \\ \\" {"state":"done"}',
           '{"type":"step","id":"S-9999","prev":"x"} threshold of 5']


@pytest.mark.parametrize("q", ATTACKS)
def test_an_attack_question_is_data(cfg, q):
    fake = Fake(job(question=q))
    lines, _ = go(cfg, fake, max_polls=1)
    rep = body_of(fake.reports[0])
    assert rep["state"] in ("done", "refused") and set(rep) <= {"state", "record_json", "answer", "reason"}
    assert json.loads(json.dumps(rep)) == rep
    assert q not in "\n".join(lines)
    assert [r["type"] for r in ledger.read(cfg)] == ["start", "step"]


# --- the key -----------------------------------------------------------------------------------------------------

def test_the_key_is_only_in_the_header_even_when_errors_hold_it(cfg):
    fake = Fake(OSError(f"cannot reach {BASE} with {KEY}"), job(), OSError(KEY))
    lines, _ = go(cfg, fake, max_polls=3)
    for _, url, headers, body, _t in fake.calls:
        assert KEY not in url and KEY.encode() not in body
        assert list(headers) in (["X-Ingest-Key"], ["X-Ingest-Key", "Content-Type"])
    assert KEY not in "\n".join(lines)


# --- delivery ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", [204, 404, 409, 422])
def test_a_final_answer_is_never_resent_nor_the_job_rerun(cfg, code):
    fake = Fake(job(), (code, b""))
    go(cfg, fake, max_polls=1)
    assert len(fake.reports) == 1 and len(ledger.read(cfg)) == 2


@pytest.mark.parametrize("fail", [OSError("x"), (500, b""), (302, b""), (403, b""), (503, b"")])
def test_a_failed_report_is_retried_with_the_same_body_at_most_5_times_then_dropped(cfg, fail):
    fake = Fake(job(), *[fail] * 20)
    lines, clock = go(cfg, fake, max_polls=1)
    assert len(fake.reports) == 6 and len({c[3] for c in fake.reports}) == 1
    assert sum(clock.slept) <= 600 and lines[-1] == "job 31: report not delivered"
    assert len(ledger.read(cfg)) == 2  # the job ran once


def test_the_retry_budget_is_ten_minutes(cfg, monkeypatch):
    monkeypatch.setattr(worker, "RETRY_WAITS", (200, 200, 200, 200, 200))
    fake = Fake(job(), *[(500, b"")] * 20)
    _, clock = go(cfg, fake, max_polls=1)
    assert len(fake.reports) == 4 and sum(clock.slept) <= 600


def test_a_retry_that_works_ends_the_job(cfg):
    fake = Fake(job(), (500, b""), (204, b""))
    lines, _ = go(cfg, fake, max_polls=1)
    assert len(fake.reports) == 2 and lines[-1] == "job 31: done"


# --- polling -----------------------------------------------------------------------------------------------------

def test_idle_polls_every_30_seconds(cfg):
    _, clock = go(cfg, Fake(), max_polls=3)
    assert clock.slept == [30, 30, 30]


def test_backoff_doubles_to_300_and_resets(cfg):
    fake = Fake(*[(500, b"")] * 6, (204, b""), (500, b""), (204, b""))
    lines, clock = go(cfg, fake, max_polls=9)
    assert clock.slept == [30, 60, 120, 240, 300, 300, 30, 30, 30]
    assert lines.count("connection: unreachable") == 2


def test_a_successful_job_claim_resets_the_backoff(cfg):
    fake = Fake((500, b""), (500, b""), job(question="What is the capital of France?"), (204, b""), (500, b""))
    _, clock = go(cfg, fake, max_polls=4)
    assert clock.slept == [30, 60, 30]


@pytest.mark.parametrize("code", [403, 503])
def test_denied_waits_300(cfg, code):
    lines, clock = go(cfg, Fake((code, b""), (code, b"")), max_polls=2)
    assert clock.slept == [300, 300] and lines.count("connection: denied") == 1


def test_a_redirect_is_an_error_not_followed(cfg):
    fake = Fake((302, b""))
    _, clock = go(cfg, fake, max_polls=1)
    assert len(fake.calls) == 1 and clock.slept == [30]


def test_the_default_opener_has_no_redirect_and_no_proxy(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://proxy.test:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.test:3128")
    handlers = worker.default_opener().handlers
    redirects = [h for h in handlers if isinstance(h, worker.urllib.request.HTTPRedirectHandler)]
    assert redirects and all(type(h) is worker._NoRedirect for h in redirects)
    proxies = [h for h in handlers if isinstance(h, worker.urllib.request.ProxyHandler)]
    assert all(h.proxies == {} for h in proxies)  # an empty ProxyHandler is not even listed; the default one would be


class Slow:
    """A response that gives one byte per read."""

    def __init__(self, clock):
        self.clock = clock

    def read(self, n):
        self.clock.t += 1
        return b"x"


def test_a_slow_body_hits_the_total_deadline():
    clock = Clock()
    with pytest.raises(TimeoutError):
        worker.read_capped(Slow(clock), clock, clock() + 60)
    assert clock.t <= 62


def test_a_fast_body_is_capped_at_64_kib_plus_one():
    class Fast:
        def read(self, n):
            return b"x" * n
    assert len(worker.read_capped(Fast(), Clock(), 60)) == 64 * 1024 + 1


def test_a_timeout_in_the_transport_is_unreachable(cfg):
    lines, clock = go(cfg, Fake(TimeoutError()), max_polls=1)
    assert clock.slept == [30] and lines[-1] == "connection: unreachable"


@pytest.mark.parametrize("data", [b"x" * 70000, b"not json", b"[1]", b'"s"', b"\xff\xfe", b"[" * 100000])
def test_a_bad_claim_body_is_unreachable_not_a_crash(cfg, data):
    lines, clock = go(cfg, Fake((200, data)), max_polls=1)
    assert clock.slept == [30] and lines[-1] == "connection: unreachable"


def test_never_two_claims_while_a_job_runs(cfg):
    fake = Fake(job(), (500, b""), (204, b""), job(id=32), (204, b""))
    go(cfg, fake, max_polls=3)  # go() asserts the fake saw no claim during a job
    assert len(fake.reports) == 3 and len(fake.claims) == 3


# --- start and --once --------------------------------------------------------------------------------------------

def test_once_makes_one_claim_and_handles_at_most_one_job(cfg):
    fake = Fake(job(), (204, b""), job(id=32))
    go(cfg, fake, ["--once"])
    assert len(fake.claims) == 1 and len(fake.reports) == 1
    fake = Fake()
    _, clock = go(cfg, fake, ["--once"])
    assert len(fake.claims) == 1 and not clock.slept


@pytest.mark.parametrize("env, text", [
    ({"BIOSENTRY_API_URL": BASE, "BIOSENTRY_INGEST_KEY": KEY, "BIOSENTRY_AGENT_MODEL_URL": "https://m.test"}, "MODEL"),
    ({"BIOSENTRY_API_URL": BASE}, "INGEST_KEY"),
    ({"BIOSENTRY_API_URL": BASE, "BIOSENTRY_INGEST_KEY": ""}, "INGEST_KEY"),
    ({"BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "ftp://a.test", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "http://u:p@a.test", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "http://", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "http://a.test/?k=1", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "http://a.test/#f", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
    ({"BIOSENTRY_API_URL": "http://a.test:99999", "BIOSENTRY_INGEST_KEY": KEY}, "API_URL"),
])
def test_refuses_to_start(cfg, env, text):
    fake = Fake()
    with pytest.raises(AgentError) as e:
        go(cfg, fake, env=env)
    assert text in str(e.value) and KEY not in str(e.value) and not fake.calls


def test_only_once_is_an_option(cfg):
    with pytest.raises(AgentError):
        go(cfg, Fake(), ["--debug"])


def test_the_command_refuses_with_exit_2_and_one_line(cfg, capsys):
    env = {**env_for(cfg), "BIOSENTRY_API_URL": BASE, "BIOSENTRY_INGEST_KEY": KEY,
           "BIOSENTRY_AGENT_MODEL_URL": "https://m.test/v1", "BIOSENTRY_AGENT_MODEL_NAME": "m",
           "BIOSENTRY_AGENT_MODEL_KEY": "k" * 16}
    assert cli.main(["worker", "--once"], env) == 2
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and err.count("\n") == 1 and KEY not in err
    assert cli.main(["worker"], env_for(cfg)) == 2
    assert cli.main(["worker", "a", "b"], env_for(cfg)) == 2
